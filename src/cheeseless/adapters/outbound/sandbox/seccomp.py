"""A hand-packed classic-BPF seccomp filter (ADR 0003, ledger I2).

The filter checks the audit architecture, kills x32 and foreign-architecture calls,
answers ``EPERM`` to the calls that let one process read or steer another, and allows
the rest. It is passed to ``bwrap --seccomp FD``. Only x86_64 is supported; any other
machine is refused so that the sandbox never runs without the filter.
"""

import errno
import os
import platform
import struct

from cheeseless.ports.sandbox import SandboxError

AUDIT_ARCH_X86_64 = 0xC000003E
X32_SYSCALL_BIT = 0x40000000
RET_KILL = 0x80000000  # SECCOMP_RET_KILL_PROCESS
RET_ALLOW = 0x7FFF0000
RET_EPERM = 0x00050000 | errno.EPERM  # SECCOMP_RET_ERRNO | EPERM

# ptrace, process_vm_readv, process_vm_writev, pidfd_getfd
DENIED_X86_64 = (101, 310, 311, 438)

_LD_W_ABS = 0x20  # BPF_LD | BPF_W | BPF_ABS
_JEQ_K = 0x15  # BPF_JMP | BPF_JEQ | BPF_K
_JGE_K = 0x35  # BPF_JMP | BPF_JGE | BPF_K
_RET_K = 0x06  # BPF_RET | BPF_K
_OFF_NR = 0  # struct seccomp_data.nr
_OFF_ARCH = 4  # struct seccomp_data.arch
_X86_64_NAMES = frozenset({"x86_64", "amd64", "AMD64"})


def _ins(code: int, jt: int, jf: int, k: int) -> bytes:
    return struct.pack("<HBBI", code, jt, jf, k)


def program(machine: str | None = None) -> bytes:
    """The filter for ``machine`` (default: this host) as packed ``sock_filter`` records."""
    machine = machine or platform.machine()
    if machine not in _X86_64_NAMES:
        raise SandboxError(f"no seccomp filter for machine {machine}; refusing to run")
    prog = [
        _ins(_LD_W_ABS, 0, 0, _OFF_ARCH),
        _ins(_JEQ_K, 1, 0, AUDIT_ARCH_X86_64),
        _ins(_RET_K, 0, 0, RET_KILL),
        _ins(_LD_W_ABS, 0, 0, _OFF_NR),
        _ins(_JGE_K, 0, 1, X32_SYSCALL_BIT),
        _ins(_RET_K, 0, 0, RET_KILL),
    ]
    count = len(DENIED_X86_64)
    for i, nr in enumerate(DENIED_X86_64):
        # On a match, jump over the remaining checks and the ALLOW to the EPERM return.
        prog.append(_ins(_JEQ_K, count - i, 0, nr))
    prog += [_ins(_RET_K, 0, 0, RET_ALLOW), _ins(_RET_K, 0, 0, RET_EPERM)]
    return b"".join(prog)


def program_fd(machine: str | None = None) -> int:
    """A readable pipe fd holding the filter, ready for ``bwrap --seccomp``.

    The filter is far smaller than a pipe buffer, so the write never blocks.
    """
    data = program(machine)
    read_end, write_end = os.pipe()
    try:
        os.write(write_end, data)
    finally:
        os.close(write_end)
    return read_end
