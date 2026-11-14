"""Host-side egress proxy process (ADR 0007).

Wires a mitmproxy ``DumpMaster`` to the DLP addon and a private Unix socket. The
session CA directory stays on the host; only the CA certificate is mounted into the
sandbox (``CODEX_CA_CERTIFICATE`` / ``NODE_EXTRA_CA_CERTS``). The addon never sees
candidate code and the proxy never retains hidden plaintext.
"""

from __future__ import annotations

import asyncio
import os
import socket
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from mitmproxy import options as mitm_options
from mitmproxy.tools.dump import DumpMaster

from cheeseless.adapters.outbound.egress.addon import EgressAddon, HitCallback
from cheeseless.adapters.outbound.egress.bridge import UnixToTcpBridge
from cheeseless.domain.dlp import DlpScanner, Fingerprints
from cheeseless.domain.egress_policy import EgressPolicy

START_TIMEOUT = 15.0


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    policy: EgressPolicy
    confdir: Path
    socket_path: Path
    listen_host: str = "127.0.0.1"
    upstream_trusted_ca: Path | None = None
    insecure_upstream: bool = False


class ProxyError(RuntimeError):
    """The proxy could not be started safely."""


@dataclass
class EgressProxy:
    config: ProxyConfig
    fingerprints: Fingerprints
    on_hit: HitCallback | None = None
    _master: DumpMaster | None = field(default=None, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)
    _bridge: UnixToTcpBridge | None = field(default=None, init=False)
    _error: BaseException | None = field(default=None, init=False)
    _port: int = field(default=0, init=False)

    @property
    def ca_cert_path(self) -> Path:
        return self.config.confdir / "mitmproxy-ca-cert.pem"

    @property
    def socket_path(self) -> Path:
        return self.config.socket_path

    @property
    def tcp_port(self) -> int:
        return self._port

    def start(self) -> None:
        if self._master is not None:
            raise ProxyError("proxy already started")
        _private_dir(self.config.confdir)
        self._port = _free_port(self.config.listen_host)
        opts = mitm_options.Options(
            listen_host=self.config.listen_host,
            listen_port=self._port,
            confdir=str(self.config.confdir),
            ssl_insecure=self.config.insecure_upstream,
            http2=True,
        )
        if self.config.upstream_trusted_ca is not None:
            opts.ssl_verify_upstream_trusted_ca = str(self.config.upstream_trusted_ca)
        addon = EgressAddon(self.config.policy, DlpScanner(self.fingerprints), self.on_hit)

        def runner() -> None:
            async def main() -> None:
                master = DumpMaster(opts, with_termlog=False, with_dumper=False)
                self._master = master
                master.addons.add(addon)  # type: ignore[no-untyped-call]
                await master.run()

            try:
                asyncio.run(main())
            except BaseException as exc:  # noqa: BLE001 - surfaced to the caller
                self._error = exc

        self._thread = threading.Thread(target=runner, name="cheeseless-egress", daemon=True)
        self._thread.start()
        self._wait_ready()
        bridge = UnixToTcpBridge(self.config.socket_path, (self.config.listen_host, self._port))
        try:
            bridge.start()
        except Exception:
            self.stop()
            raise
        self._bridge = bridge

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if self._error is not None:
                raise ProxyError(f"proxy failed to start: {type(self._error).__name__}")
            if self._master is not None and self.ca_cert_path.is_file():
                if _accepts(self.config.listen_host, self._port):
                    return
            time.sleep(0.05)
        self.stop()
        raise ProxyError("proxy did not become ready")

    def stop(self) -> None:
        bridge = self._bridge
        self._bridge = None
        if bridge is not None:
            bridge.close()
        master = self._master
        self._master = None
        if master is not None:
            try:
                master.shutdown()  # type: ignore[no-untyped-call]
            except Exception:  # noqa: S110 - teardown is best effort
                pass
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=START_TIMEOUT)
        try:
            os.unlink(self.config.socket_path)
        except OSError:
            pass


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
        raise ProxyError("proxy directory is not private")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise ProxyError("proxy directory is not private")
    os.chmod(path, 0o700)


def _free_port(host: str) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _accepts(host: str, port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.2)
    try:
        probe.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


__all__ = ["EgressProxy", "ProxyConfig", "ProxyError"]
