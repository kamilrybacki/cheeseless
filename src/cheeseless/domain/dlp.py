"""Hidden-content fingerprints and byte scanners (Q46, Q48, Q49, D16, I7).

The proxy never receives hidden plaintext. Freeze reduces each hidden artifact to

* literal sentinel patterns, stored only as truncated digests keyed by their exact
  length (raw, base64, hex and reversed forms), and
* every k-gram hash of the hidden content, with k-grams that also occur in the
  agent-visible projection removed (I7: no winnowing claim after subtraction).

Scanning is exact for the literal forms and rolling for k-grams. A streaming scan
holds back ``max(longest pattern, k - 1)`` bytes, so a match that straddles a chunk
boundary is still detected before any byte of it is forwarded (R2, D16).

Archives are recognised by magic bytes, never by ``Content-Type``. Members are
decompressed in memory, recursively inspected and never extracted to disk, so a
malicious member name or symlink has no filesystem effect (D9, D16).
"""

import base64
import bz2
import hashlib
import io
import lzma
import re
import tarfile
import zipfile
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

KGRAM_SIZE = 24
MASK = (1 << 64) - 1
BASE = 257
DLP_RULES = ("literal", "kgram", "archive", "encoded", "protocol")
ARCHIVE_INPUT_LIMIT = 16 * 1024 * 1024
ARCHIVE_EXPANDED_LIMIT = 32 * 1024 * 1024
ARCHIVE_MEMBER_LIMIT = 8 * 1024 * 1024
ARCHIVE_FILE_LIMIT = 256
ARCHIVE_DEPTH_LIMIT = 4
DIGEST_HEX = 32
MIN_SENTINELS_PER_CLASS = 2

_SENTINEL = re.compile(rb"CHEESELESS-(?:SENTINEL|CANARY)-[A-Za-z0-9_-]{6,64}")
_SENTINEL_TEXT = re.compile(_SENTINEL.pattern.decode(), re.IGNORECASE)
_WHITESPACE = re.compile(rb"\s+")
_BASE64_ALIGNMENTS = (0, 1, 2)

ARTIFACT_TESTS = "tests"
ARTIFACT_FIXTURES = "fixtures"
ARTIFACT_EXPECTED = "expected"
ARTIFACT_SEED = "seed"
ARTIFACT_NOTES = "notes"
ARTIFACT_CLASSES = (
    ARTIFACT_TESTS,
    ARTIFACT_FIXTURES,
    ARTIFACT_EXPECTED,
    ARTIFACT_SEED,
    ARTIFACT_NOTES,
)

_PATH_WORDS = re.compile(r"[/._-]+")
_EXPECTED_WORDS = frozenset({"expected", "golden", "snapshot", "snapshots"})
_SEED_WORDS = frozenset({"seed", "seeds"})
_FIXTURE_WORDS = frozenset({"fixture", "fixtures", "data"})
_NOTE_WORDS = frozenset({"note", "notes"})
_NOTE_SUFFIXES = (".md", ".txt")


def digest(data: bytes) -> str:
    """Truncated digest used as a fingerprint id; never reversible to content."""
    return hashlib.sha256(data).hexdigest()[:DIGEST_HEX]


def _window_digest(window: bytes) -> str:
    return hashlib.blake2b(window, digest_size=DIGEST_HEX // 2).hexdigest()


@dataclass(frozen=True, slots=True)
class LiteralForm:
    """One encoded form of one sentinel. The plaintext never reaches the proxy."""

    length: int
    value: str
    sentinel_id: str
    artifact_class: str
    form: str

    def __post_init__(self) -> None:
        if isinstance(self.length, bool) or not 1 <= self.length <= 1 << 20:
            raise ValueError("invalid literal pattern length")
        if len(self.value) != DIGEST_HEX or not re.fullmatch(r"[0-9a-f]+", self.value):
            raise ValueError("invalid literal pattern digest")
        if not re.fullmatch(r"[0-9a-f]+", self.sentinel_id):
            raise ValueError("invalid sentinel id")
        if self.artifact_class not in ARTIFACT_CLASSES:
            raise ValueError(f"unknown artifact class: {self.artifact_class!r}")
        if self.form not in ("raw", "base64", "hex", "reversed"):
            raise ValueError(f"unknown sentinel form: {self.form!r}")


@dataclass(frozen=True, slots=True)
class Fingerprints:
    """What the proxy is allowed to retain about the hidden artifacts."""

    task_id: str
    k: int
    kgrams: frozenset[int]
    literals: tuple[LiteralForm, ...]
    public_kgrams_removed: int = 0

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.task_id):
            raise ValueError("invalid task id")
        if isinstance(self.k, bool) or not 4 <= self.k <= 256:
            raise ValueError("invalid k-gram size")
        if not all(isinstance(h, int) and 0 <= h <= MASK for h in self.kgrams):
            raise ValueError("invalid k-gram hash")
        if not isinstance(self.literals, tuple) or not all(
            isinstance(item, LiteralForm) for item in self.literals
        ):
            raise ValueError("literals must be a tuple of LiteralForm")

    @property
    def holdback(self) -> int:
        longest = max((item.length for item in self.literals), default=0)
        return max(longest, self.k - 1)

    def to_data(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "k": self.k,
            "kgrams": [f"{value:016x}" for value in sorted(self.kgrams)],
            "public_kgrams_removed": self.public_kgrams_removed,
            "literals": [
                [item.length, item.value, item.sentinel_id, item.artifact_class, item.form]
                for item in self.literals
            ],
        }

    @classmethod
    def from_data(cls, data: Mapping[str, object]) -> "Fingerprints":
        raw_kgrams = data.get("kgrams")
        raw_literals = data.get("literals")
        if not isinstance(raw_kgrams, list) or not all(isinstance(v, str) for v in raw_kgrams):
            raise ValueError("invalid k-gram list")
        if not isinstance(raw_literals, list):
            raise ValueError("invalid literal list")
        kgrams = frozenset(int(value, 16) for value in raw_kgrams)
        literals = []
        for item in raw_literals:
            if not isinstance(item, list) or len(item) != 5:
                raise ValueError("invalid literal entry")
            length, value, sentinel_id, artifact_class, form = item
            if not all(isinstance(v, str) for v in (value, sentinel_id, artifact_class, form)):
                raise ValueError("invalid literal entry")
            if not isinstance(length, int) or isinstance(length, bool):
                raise ValueError("invalid literal length")
            literals.append(LiteralForm(length, value, sentinel_id, artifact_class, form))
        task_id = data.get("task_id")
        k = data.get("k")
        removed = data.get("public_kgrams_removed", 0)
        if not isinstance(task_id, str) or not isinstance(k, int) or isinstance(k, bool):
            raise ValueError("invalid fingerprint header")
        if not isinstance(removed, int) or isinstance(removed, bool) or removed < 0:
            raise ValueError("invalid public k-gram count")
        return cls(task_id, k, kgrams, tuple(literals), removed)


def rolling_hashes(data: bytes, k: int = KGRAM_SIZE) -> Iterable[int]:
    """Every k-gram hash of ``data`` as a 64-bit Rabin-Karp value."""
    if len(data) < k:
        return ()
    high = pow(BASE, k - 1, 1 << 64)
    value = 0
    for byte in data[:k]:
        value = (value * BASE + byte) & MASK
    out = [value]
    for index in range(k, len(data)):
        value = ((value - data[index - k] * high) * BASE + data[index]) & MASK
        out.append(value)
    return out


def kgram_set(data: bytes, k: int = KGRAM_SIZE) -> frozenset[int]:
    return frozenset(rolling_hashes(data, k))


def artifact_class(path: str) -> str:
    """Classify a hidden artifact by whole words in its lowercased path.

    Paths are split on ``/``, ``.``, ``_`` and ``-`` so only complete words count:
    ``seedless_test.py`` is a test, not a seed. When several words match, the first
    matching class in this documented precedence wins:

    1. expected: ``expected``, ``golden``, ``snapshot`` or ``snapshots``;
    2. seed: ``seed`` or ``seeds``;
    3. fixtures: ``fixture``, ``fixtures`` or ``data``;
    4. notes: the word ``note``/``notes``, or a ``.md``/``.txt`` extension;
    5. tests: everything else.

    The precedence means ``data/expected.json`` is expected and ``data/seed.json`` is
    seed, while ``fixtures/notes.md`` is fixtures because fixtures outrank notes.
    """
    lowered = path.lower()
    words = set(_PATH_WORDS.split(lowered))
    if words & _EXPECTED_WORDS:
        return ARTIFACT_EXPECTED
    if words & _SEED_WORDS:
        return ARTIFACT_SEED
    if words & _FIXTURE_WORDS:
        return ARTIFACT_FIXTURES
    if words & _NOTE_WORDS or lowered.endswith(_NOTE_SUFFIXES):
        return ARTIFACT_NOTES
    return ARTIFACT_TESTS


def _sentinel_tokens(data: bytes) -> set[bytes]:
    return {match.group(0) for match in _SENTINEL.finditer(data)}


def _base64_cores(token: bytes) -> tuple[bytes, ...]:
    """The base64 characters of ``token`` that do not depend on its neighbours.

    For each byte alignment ``p`` the token is encoded after ``p`` filler bytes; only
    the characters whose six bits lie wholly inside the token are kept, so the core
    occurs in the base64 of any larger blob that holds the token at that alignment.
    """
    cores = []
    for prefix in _BASE64_ALIGNMENTS:
        encoded = base64.b64encode(b"\0" * prefix + token)
        start = -(-8 * prefix // 6)
        end = 8 * (prefix + len(token)) // 6
        cores.append(encoded[start:end])
    return tuple(cores)


def _is_exposed(token: bytes, public: Sequence[bytes], lowered: Sequence[bytes]) -> bool:
    folded = (token.lower(), token[::-1].lower(), token.hex().encode())
    if any(form in surface for surface in lowered for form in folded):
        return True
    cores = _base64_cores(token)
    return any(core in surface for surface in public for core in cores)


def redact_sentinels(text: str) -> str:
    """Replace every sentinel marker in ``text``, in any letter case, with a digest."""
    return _SENTINEL_TEXT.sub(
        lambda match: f"<sentinel digest {digest(match.group(0).encode())}>", text
    )


def sentinel_problems(
    hidden: Sequence[tuple[str, bytes]],
    public: Sequence[bytes],
    minimum: int = MIN_SENTINELS_PER_CLASS,
) -> list[str]:
    """Refuse hidden suites that plant too few (or pre-exposed) sentinels.

    Each artifact class with hidden artifacts needs ``minimum`` distinct sentinel
    tokens that the projection does not already contain. ``public`` holds every
    agent-visible byte string, including projection file paths. A hidden token counts
    as exposed when any public surface contains it raw, reversed or as hex (all three
    compared case-insensitively), or as base64 at any of the three byte alignments
    (case-sensitive), also after removing whitespace so line-wrapped base64 counts.
    Boundary characters are trimmed from the base64 core, so a near-miss marker that
    differs only in those bits is refused too (fail closed). Matching is by substring,
    so a public ``...-alpha01X`` still reveals a hidden ``...-alpha01`` token. Only
    digests are reported.

    Protection boundary (accepted residual, ADR 0007): other reversible encodings
    (rot13, base32, url-encoding, compression, ...) and a marker split across several
    public files or paths are not detected.
    """
    tokens_by_class: dict[str, set[bytes]] = {cls: set() for cls in ARTIFACT_CLASSES}
    present: set[str] = set()
    for path, data in hidden:
        cls = artifact_class(path)
        present.add(cls)
        tokens_by_class[cls] |= _sentinel_tokens(data)
    all_tokens = set().union(*tokens_by_class.values())
    lowered = [surface.lower() for surface in public]
    # Line-wrapped base64 (MIME, PEM, ``base64`` CLI) splits a core across lines.
    compact = [_WHITESPACE.sub(b"", surface) for surface in public]
    exposed: set[bytes] = {
        token
        for token in all_tokens
        if _is_exposed(token, [*public, *compact], [*lowered, *(c.lower() for c in compact)])
    }

    problems: list[str] = []
    reported_exposed: set[bytes] = set()
    for cls in ARTIFACT_CLASSES:
        if cls not in present:
            continue
        tokens = tokens_by_class[cls]
        for token in sorted(tokens & exposed):
            if token not in reported_exposed:
                reported_exposed.add(token)
                problems.append(f"a sentinel is visible in the projection (digest {digest(token)})")
        private = tokens - exposed
        if len(private) < minimum:
            problems.append(
                f"artifact class {cls!r} has {len(private)} distinct sentinel token(s); "
                f"freeze requires at least {minimum}"
            )
    return problems


def sentinel_forms(data: bytes) -> tuple[tuple[str, str, str], ...]:
    """Harvest marker sentinels and return (sentinel_id, class, form) digests.

    The plaintext marker is not part of the result; only lengths and digests are.
    Artifact class is added by the caller, which knows the path.
    """
    items: list[tuple[str, str, str]] = []
    seen: set[bytes] = set()
    for match in _SENTINEL.finditer(data):
        token = match.group(0)
        if token in seen:
            continue
        seen.add(token)
        sentinel_id = digest(token)
        forms = {
            "raw": token,
            "base64": base64.b64encode(token),
            "hex": token.hex().encode(),
            "reversed": token[::-1],
        }
        for form, encoded in forms.items():
            items.append((sentinel_id, form, _window_digest(encoded) + f":{len(encoded)}"))
    return tuple(items)


def literal_forms(
    data: bytes, path: str, *, secret: bytes | None = None
) -> tuple[LiteralForm, ...]:
    """Sentinel patterns for one hidden artifact, digest-only.

    ``secret`` is accepted for callers that later want keyed sentinels; the MVP
    harvests explicit markers, so it does not change the result.
    """
    del secret
    cls = artifact_class(path)
    out = []
    for sentinel_id, form, packed in sentinel_forms(data):
        value, _, length_text = packed.partition(":")
        out.append(LiteralForm(int(length_text), value, sentinel_id, cls, form))
    return tuple(out)


def build_fingerprints(
    task_id: str,
    hidden: Sequence[tuple[str, bytes]],
    public: Sequence[bytes],
    *,
    k: int = KGRAM_SIZE,
) -> Fingerprints:
    """Reduce hidden artifacts to literal pattern and k-gram hashes.

    Public k-grams (anything the agent can already read) are removed before any
    selection step, because I7 forbids claiming winnowing once hashes are removed.
    """
    hidden_hashes: set[int] = set()
    literals: list[LiteralForm] = []
    for path, data in hidden:
        hidden_hashes |= kgram_set(data, k)
        literals.extend(literal_forms(data, path))
    public_hashes: set[int] = set()
    for data in public:
        public_hashes |= kgram_set(data, k)
    removed = len(hidden_hashes & public_hashes)
    hidden_hashes -= public_hashes
    return Fingerprints(
        task_id=task_id,
        k=k,
        kgrams=frozenset(hidden_hashes),
        literals=tuple(literals),
        public_kgrams_removed=removed,
    )


@dataclass(frozen=True, slots=True)
class ScanHit:
    rule: str
    fingerprint_id: str
    artifact_class: str = ""

    def __post_init__(self) -> None:
        if self.rule not in DLP_RULES:
            raise ValueError(f"unknown DLP rule: {self.rule!r}")


class DlpScanner:
    """Match literal forms and every k-gram of hidden content.

    Literal matching is exact (a per-length truncated hash of the window); k-gram
    matching uses the rolling hash. Both work across arbitrary chunk boundaries.
    """

    def __init__(self, fingerprints: Fingerprints) -> None:
        self.fingerprints = fingerprints
        self._k = fingerprints.k
        self._kgrams = fingerprints.kgrams
        self._literals: dict[int, dict[str, LiteralForm]] = {}
        for item in fingerprints.literals:
            self._literals.setdefault(item.length, {})[item.value] = item
        self._lengths = tuple(sorted(self._literals))
        self._longest = max((item.length for item in fingerprints.literals), default=0)

    @property
    def holdback(self) -> int:
        return max(self._longest, self._k - 1)

    def scan(self, data: bytes) -> ScanHit | None:
        """Scan a complete byte string."""
        hit = self._scan_literals(data)
        if hit is not None:
            return hit
        if len(data) >= self._k:
            for value in rolling_hashes(data, self._k):
                if value in self._kgrams:
                    return ScanHit("kgram", f"{value:016x}")
        return None

    def _scan_literals(self, data: bytes) -> ScanHit | None:
        for length in self._lengths:
            if length > len(data):
                continue
            table = self._literals[length]
            for start in range(0, len(data) - length + 1):
                item = table.get(_window_digest(data[start : start + length]))
                if item is not None:
                    return ScanHit("literal", item.sentinel_id, item.artifact_class)
        return None


@dataclass
class StreamScanner:
    """Forward only bytes already known clean, keeping the D16 tail back."""

    scanner: DlpScanner
    _pending: bytearray = field(default_factory=bytearray)
    _carry: bytes = b""
    _hit: ScanHit | None = None
    _done: bool = False

    @property
    def hit(self) -> ScanHit | None:
        return self._hit

    @property
    def holdback(self) -> int:
        return self.scanner.holdback

    def feed(self, chunk: bytes) -> bytes:
        """Consume one chunk and return the prefix that may be forwarded."""
        if self._done:
            return b""
        if not chunk:
            return self.close()
        combined = self._carry + chunk
        hit = self.scanner.scan(combined)
        if hit is not None:
            self._hit = hit
            self._pending.clear()
            self._carry = b""
            self._done = True
            return b""
        keep = max(self.scanner.holdback, 0)
        self._pending.extend(chunk)
        if keep == 0:
            out = bytes(self._pending)
            self._pending.clear()
            self._carry = b""
            return out
        if len(self._pending) <= keep:
            out = b""
        else:
            cut = len(self._pending) - keep
            out = bytes(self._pending[:cut])
            del self._pending[:cut]
        self._carry = combined[-keep:]
        return out

    def close(self) -> bytes:
        """End of stream: scan the real tail and release it only if clean."""
        if self._done:
            return b""
        self._done = True
        if self._pending:
            hit = self.scanner.scan(bytes(self._pending))
            if hit is not None:
                self._hit = hit
                self._pending.clear()
                return b""
        out = bytes(self._pending)
        self._pending.clear()
        self._carry = b""
        return out


class BodyScanner:
    """Per-message scanner: sniff the prefix, buffer archives, stream the rest.

    A tar header lives at byte 257, so the first bytes are held until the format is
    known. Archives are buffered whole (bounded) and released only after a clean
    recursive inspection; ordinary bodies use the D16 hold-back stream.
    """

    SNIFF_BYTES = 512

    def __init__(self, scanner: DlpScanner) -> None:
        self.scanner = scanner
        self._mode: str | None = None
        self._sniff = bytearray()
        self._stream: StreamScanner | None = None
        self._archive = bytearray()
        self._hit: ScanHit | None = None
        self._closed = False

    @property
    def hit(self) -> ScanHit | None:
        return self._hit

    def fail(self, rule: str = "protocol", fingerprint_id: str = "proxy-error") -> None:
        """Fail closed after an internal scanner error."""
        self._hit = ScanHit(rule, fingerprint_id)
        self._closed = True
        self._sniff.clear()
        self._archive.clear()

    def feed(self, chunk: bytes) -> bytes:
        if self._closed or self._hit is not None:
            return b""
        if not chunk:
            return self.close()
        if self._mode == "stream":
            if self._stream is None:  # pragma: no cover - mode implies stream
                self._stream = StreamScanner(self.scanner)
            return self._stream.feed(chunk)
        if self._mode == "archive":
            self._archive.extend(chunk)
            if len(self._archive) > ARCHIVE_INPUT_LIMIT:
                self._hit = ScanHit("archive", "archive-cap")
                self._archive.clear()
            return b""
        self._sniff.extend(chunk)
        if looks_archive(bytes(self._sniff)):
            self._mode = "archive"
            self._archive.extend(self._sniff)
            self._sniff.clear()
            if len(self._archive) > ARCHIVE_INPUT_LIMIT:
                self._hit = ScanHit("archive", "archive-cap")
                self._archive.clear()
            return b""
        if len(self._sniff) >= self.SNIFF_BYTES:
            self._mode = "stream"
            self._stream = StreamScanner(self.scanner)
            buffered = bytes(self._sniff)
            self._sniff.clear()
            out = self._stream.feed(buffered)
            if self._stream.hit is not None:
                self._hit = self._stream.hit
            return out
        return b""

    def close(self) -> bytes:
        if self._closed:
            return b""
        self._closed = True
        if self._hit is not None:
            return b""
        if self._mode == "archive":
            data = bytes(self._archive)
            self._archive.clear()
            inspection = inspect_archive(data, self.scanner)
            if not inspection.clean:
                self._hit = inspection.hit or ScanHit("archive", "archive-unsafe")
                return b""
            return data
        if self._mode == "stream":
            if self._stream is None:  # pragma: no cover
                return b""
            out = self._stream.close()
            if self._stream.hit is not None:
                self._hit = self._stream.hit
                return b""
            return out
        data = bytes(self._sniff)
        self._sniff.clear()
        if looks_archive(data):
            inspection = inspect_archive(data, self.scanner)
            if not inspection.clean:
                self._hit = inspection.hit or ScanHit("archive", "archive-unsafe")
                return b""
            return data
        hit = self.scanner.scan(data)
        if hit is not None:
            self._hit = hit
            return b""
        return data


# -- archives -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArchiveInspection:
    clean: bool
    hit: ScanHit | None = None
    reason: str = ""


@dataclass
class _ArchiveBudget:
    expanded: int = 0
    files: int = 0


_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_GZIP_MAGIC = (b"\x1f\x8b",)
_BZ2_MAGIC = (b"BZh",)
_XZ_MAGIC = (b"\xfd7zXZ\x00",)


def looks_archive(data: bytes) -> bool:
    if data.startswith(_ZIP_MAGIC) or data.startswith(_GZIP_MAGIC):
        return True
    if data.startswith(_BZ2_MAGIC) or data.startswith(_XZ_MAGIC):
        return True
    return len(data) >= 262 and data[257:262] == b"ustar"


def _unsafe_name(name: str) -> bool:
    if not name or name.startswith("/") or "\x00" in name:
        return True
    parts = name.replace("\\", "/").split("/")
    return ".." in parts


class _ArchiveBlocked(Exception):
    pass


def inspect_archive(
    data: bytes, scanner: DlpScanner, *, depth: int = 0, budget: _ArchiveBudget | None = None
) -> ArchiveInspection:
    """Recursively inspect an archive entirely in memory (D9, D16)."""
    if budget is None:
        budget = _ArchiveBudget()
    if depth > ARCHIVE_DEPTH_LIMIT:
        return ArchiveInspection(False, reason="archive nesting depth exceeded")
    if len(data) > ARCHIVE_INPUT_LIMIT:
        return ArchiveInspection(False, reason="archive exceeds the input cap")
    if depth == 0 and not looks_archive(data):
        hit = scanner.scan(data)
        return ArchiveInspection(hit is None, hit=hit)
    try:
        if data.startswith(_ZIP_MAGIC):
            return _inspect_zip(data, scanner, depth, budget)
        if data.startswith(_GZIP_MAGIC):
            return _inspect_inner(_decompress_gzip(data, budget), scanner, depth, budget)
        if data.startswith(_BZ2_MAGIC):
            return _inspect_inner(_decompress_bz2(data, budget), scanner, depth, budget)
        if data.startswith(_XZ_MAGIC):
            return _inspect_inner(_decompress_xz(data, budget), scanner, depth, budget)
        if len(data) >= 262 and data[257:262] == b"ustar":
            return _inspect_tar(data, scanner, depth, budget)
    except _ArchiveBlocked as exc:
        return ArchiveInspection(False, reason=str(exc))
    except (OSError, EOFError, ValueError, zipfile.BadZipFile, lzma.LZMAError):
        return ArchiveInspection(False, reason="archive could not be inspected")
    except Exception:  # noqa: BLE001 - fail closed on any parser surprise
        return ArchiveInspection(False, reason="archive could not be inspected")
    return ArchiveInspection(False, reason="archive format is not supported")


def _inspect_inner(
    data: bytes, scanner: DlpScanner, depth: int, budget: _ArchiveBudget
) -> ArchiveInspection:
    if looks_archive(data):
        return inspect_archive(data, scanner, depth=depth + 1, budget=budget)
    hit = scanner.scan(data)
    return ArchiveInspection(hit is None, hit=hit)


def _charge(budget: _ArchiveBudget, size: int, name: str) -> None:
    budget.files += 1
    budget.expanded += size
    if budget.files > ARCHIVE_FILE_LIMIT:
        raise _ArchiveBlocked("archive has too many files")
    if size > ARCHIVE_MEMBER_LIMIT:
        raise _ArchiveBlocked(f"archive member {name} is too large")
    if budget.expanded > ARCHIVE_EXPANDED_LIMIT:
        raise _ArchiveBlocked("archive expands beyond the cap")


def _inspect_zip(
    data: bytes, scanner: DlpScanner, depth: int, budget: _ArchiveBudget
) -> ArchiveInspection:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if _unsafe_name(info.filename):
                return ArchiveInspection(False, reason="archive member path is unsafe")
            if info.flag_bits & 0x1:
                return ArchiveInspection(False, reason="encrypted archive member")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                return ArchiveInspection(False, reason="archive member is a symlink")
            if info.file_size > ARCHIVE_MEMBER_LIMIT:
                return ArchiveInspection(False, reason="archive member is too large")
            with archive.open(info) as member:
                raw = member.read(ARCHIVE_MEMBER_LIMIT + 1)
            _charge(budget, len(raw), info.filename)
            nested = _inspect_inner(raw, scanner, depth, budget)
            if not nested.clean:
                return nested
    return ArchiveInspection(True)


def _inspect_tar(
    data: bytes, scanner: DlpScanner, depth: int, budget: _ArchiveBudget
) -> ArchiveInspection:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive:
            if member.isdir():
                continue
            if _unsafe_name(member.name):
                return ArchiveInspection(False, reason="archive member path is unsafe")
            if not member.isfile():
                return ArchiveInspection(False, reason="archive member is not a regular file")
            if member.size > ARCHIVE_MEMBER_LIMIT:
                return ArchiveInspection(False, reason="archive member is too large")
            handle = archive.extractfile(member)
            if handle is None:
                return ArchiveInspection(False, reason="archive member could not be read")
            raw = handle.read(ARCHIVE_MEMBER_LIMIT + 1)
            _charge(budget, len(raw), member.name)
            nested = _inspect_inner(raw, scanner, depth, budget)
            if not nested.clean:
                return nested
    return ArchiveInspection(True)


def _decompress_gzip(data: bytes, budget: _ArchiveBudget) -> bytes:
    inner = zlib.decompressobj(16 + zlib.MAX_WBITS)
    raw = inner.decompress(data, ARCHIVE_MEMBER_LIMIT + 1)
    over = len(raw) > ARCHIVE_MEMBER_LIMIT or bool(inner.unconsumed_tail) or not inner.eof
    _charge(budget, len(raw), "gzip")
    if over:
        raise _ArchiveBlocked("compressed stream expands beyond the cap")
    return raw


def _decompress_bz2(data: bytes, budget: _ArchiveBudget) -> bytes:
    inner = bz2.BZ2Decompressor()
    raw = inner.decompress(data, max_length=ARCHIVE_MEMBER_LIMIT + 1)
    over = len(raw) > ARCHIVE_MEMBER_LIMIT or not inner.eof
    _charge(budget, len(raw), "bzip2")
    if over:
        raise _ArchiveBlocked("compressed stream expands beyond the cap")
    return raw


def _decompress_xz(data: bytes, budget: _ArchiveBudget) -> bytes:
    inner = lzma.LZMADecompressor()
    raw = inner.decompress(data, max_length=ARCHIVE_MEMBER_LIMIT + 1)
    over = len(raw) > ARCHIVE_MEMBER_LIMIT or not inner.eof
    _charge(budget, len(raw), "xz")
    if over:
        raise _ArchiveBlocked("compressed stream expands beyond the cap")
    return raw


__all__ = [
    "ARCHIVE_INPUT_LIMIT",
    "ArchiveInspection",
    "BodyScanner",
    "DlpScanner",
    "Fingerprints",
    "KGRAM_SIZE",
    "LiteralForm",
    "MIN_SENTINELS_PER_CLASS",
    "ScanHit",
    "StreamScanner",
    "artifact_class",
    "build_fingerprints",
    "digest",
    "inspect_archive",
    "kgram_set",
    "literal_forms",
    "looks_archive",
    "redact_sentinels",
    "rolling_hashes",
    "sentinel_problems",
]
