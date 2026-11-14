"""DLP scanners: every chunk split, literal forms, archives (Q46, D16, I7)."""

import base64
import gzip
import hashlib
import io
import tarfile
import zipfile

import pytest

from cheeseless.domain import dlp

TASK = "a" * 64
SENTINEL = b"CHEESELESS-SENTINEL-ab12cd34ef"
HIDDEN = (
    b"def test_secret():\n"
    b"    assert answer == 42  # " + SENTINEL + b"\n"
    b"    assert sorted([3, 1, 2]) == [1, 2, 3]\n"
)


def fingerprints(
    hidden=HIDDEN, public=(b"def test_public():\n    assert 1\n",)
) -> dlp.Fingerprints:
    return dlp.build_fingerprints(TASK, [("acceptance/hidden/test_x.py", hidden)], list(public))


def split_everywhere(payload: bytes, point: int) -> list[bytes]:
    return [payload[:point], payload[point:]]


def test_fingerprints_contain_no_plaintext():
    prints = fingerprints()
    data = repr(prints.to_data())
    assert "CHEESELESS" not in data
    assert "test_secret" not in data
    assert "answer" not in data


def test_every_split_detects_hidden_kgram_and_sentinel():
    prints = fingerprints()
    scanner = dlp.DlpScanner(prints)
    for point in range(1, len(HIDDEN) + 1):
        stream = dlp.StreamScanner(scanner)
        hit = None
        forwarded = b""
        for chunk in split_everywhere(HIDDEN, point):
            forwarded += stream.feed(chunk)
            hit = hit or stream.hit
        forwarded += stream.close()
        hit = hit or stream.hit
        assert hit is not None, point
        assert SENTINEL not in forwarded


def test_literal_forms_are_hashed_and_detected():
    prints = fingerprints()
    forms = {item.form for item in prints.literals}
    assert {"raw", "base64", "hex", "reversed"} <= forms
    scanner = dlp.DlpScanner(prints)
    token = SENTINEL
    assert scanner.scan(b"prefix " + token + b" suffix") is not None
    assert scanner.scan(b"prefix " + token[::-1] + b" suffix") is not None
    assert scanner.scan(b"prefix " + token.hex().encode() + b" suffix") is not None
    import base64

    assert scanner.scan(b"prefix " + base64.b64encode(token) + b" suffix") is not None


def test_clean_stream_is_forwarded_whole():
    prints = fingerprints()
    scanner = dlp.StreamScanner(dlp.DlpScanner(prints))
    payload = b"x" * 1000
    out = b"".join(scanner.feed(payload[i : i + 7]) for i in range(0, len(payload), 7))
    out += scanner.close()
    assert out == payload
    assert scanner.hit is None


def test_holdback_covers_longest_pattern_and_k_minus_one():
    prints = fingerprints()
    scanner = dlp.DlpScanner(prints)
    longest = max(item.length for item in prints.literals)
    assert scanner.holdback >= max(longest, prints.k - 1)
    assert prints.holdback == scanner.holdback


def test_public_kgrams_are_removed_not_claimed():
    shared = b"shared public fragment " * 3
    prints = dlp.build_fingerprints(
        TASK, [("acceptance/hidden/test_x.py", shared + SENTINEL)], [shared]
    )
    assert prints.public_kgrams_removed > 0
    assert dlp.DlpScanner(prints).scan(shared) is None
    assert dlp.DlpScanner(prints).scan(shared + SENTINEL) is not None


def test_archive_detection_uses_magic_not_content_type():
    payload = gzip.compress(HIDDEN)
    assert dlp.looks_archive(payload)
    assert not dlp.looks_archive(b"text/plain")


def test_zip_with_canary_is_blocked_and_clean_zip_passes():
    scanner = dlp.DlpScanner(fingerprints())
    dirty = io.BytesIO()
    with zipfile.ZipFile(dirty, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("payload.txt", b"x" * 100 + SENTINEL)
    result = dlp.inspect_archive(dirty.getvalue(), scanner)
    assert not result.clean
    assert result.hit is not None

    clean = io.BytesIO()
    with zipfile.ZipFile(clean, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("payload.txt", b"nothing to see")
    assert dlp.inspect_archive(clean.getvalue(), scanner).clean


def test_tar_and_gzip_are_inspected_recursively():
    scanner = dlp.DlpScanner(fingerprints())
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("inner.txt", SENTINEL)
    tarred = io.BytesIO()
    with tarfile.open(fileobj=tarred, mode="w") as archive:
        info = tarfile.TarInfo("nested.zip")
        blob = nested.getvalue()
        info.size = len(blob)
        archive.addfile(info, io.BytesIO(blob))
        clean_info = tarfile.TarInfo("clean.txt")
        clean_info.size = 3
        archive.addfile(clean_info, io.BytesIO(b"abc"))
    assert not dlp.inspect_archive(tarred.getvalue(), scanner).clean
    assert not dlp.inspect_archive(gzip.compress(tarred.getvalue()), scanner).clean


def test_archive_unsafe_paths_symlinks_and_bombs_are_blocked():
    scanner = dlp.DlpScanner(fingerprints())
    traversal = io.BytesIO()
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../escape.txt", b"x")
    assert not dlp.inspect_archive(traversal.getvalue(), scanner).clean

    symlink = io.BytesIO()
    with zipfile.ZipFile(symlink, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        archive.writestr(info, b"target")
    assert not dlp.inspect_archive(symlink.getvalue(), scanner).clean

    many = io.BytesIO()
    with zipfile.ZipFile(many, "w") as archive:
        for index in range(dlp.ARCHIVE_FILE_LIMIT + 1):
            archive.writestr(f"f{index}", b"")
    blocked = dlp.inspect_archive(many.getvalue(), scanner)
    assert not blocked.clean
    assert "too many" in blocked.reason


def test_encrypted_zip_member_is_blocked():
    clean = io.BytesIO()
    with zipfile.ZipFile(clean, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("secret.txt", b"encrypted payload")
    raw = bytearray(clean.getvalue())
    for signature in (b"PK\x03\x04", b"PK\x01\x02"):
        start = 0
        while True:
            index = raw.find(signature, start)
            if index < 0:
                break
            raw[index + (6 if signature.startswith(b"PK\x03") else 8)] |= 0x01
            start = index + 4
    result = dlp.inspect_archive(bytes(raw), dlp.DlpScanner(fingerprints()))
    assert not result.clean
    assert "encrypted" in result.reason


def test_archive_input_cap_and_depth():
    scanner = dlp.DlpScanner(fingerprints())
    assert not dlp.inspect_archive(b"x" * (dlp.ARCHIVE_INPUT_LIMIT + 1), scanner).clean
    blob = b"bottom"
    for _ in range(dlp.ARCHIVE_DEPTH_LIMIT + 2):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as archive:
            archive.writestr("n", blob)
        blob = inner.getvalue()
    assert not dlp.inspect_archive(blob, scanner).clean


def test_fingerprint_round_trip_is_validated():
    prints = fingerprints()
    assert dlp.Fingerprints.from_data(prints.to_data()) == prints
    with pytest.raises(ValueError):
        dlp.Fingerprints.from_data({"task_id": "x", "k": 24, "kgrams": [], "literals": []})


def test_kgram_hash_matches_full_scan():
    data = hashlib.sha256(b"x").digest() * 4
    assert set(dlp.rolling_hashes(data)) == set(dlp.kgram_set(data))


def test_body_scanner_holds_first_bytes_until_format_is_known():
    scanner = dlp.DlpScanner(fingerprints())
    body = dlp.BodyScanner(scanner)
    payload = b"data: " + SENTINEL + b"\n\n"
    out = b""
    for index in range(0, len(payload), 3):
        out += body.feed(payload[index : index + 3])
    out += body.close()
    assert body.hit is not None
    assert SENTINEL not in out


def test_body_scanner_streams_clean_sse_across_chunks():
    scanner = dlp.DlpScanner(fingerprints())
    body = dlp.BodyScanner(scanner)
    payload = (b"data: clean line\n\n" * 100)[:2000]
    out = b""
    for index in range(0, len(payload), 17):
        out += body.feed(payload[index : index + 17])
    out += body.close()
    assert out == payload
    assert body.hit is None


def test_body_scanner_buffers_archives_until_clean():
    scanner = dlp.DlpScanner(fingerprints())
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("x.txt", b"clean")
    raw = archive.getvalue()
    body = dlp.BodyScanner(scanner)
    out = b""
    for index in range(0, len(raw), 5):
        out += body.feed(raw[index : index + 5])
        assert out == b""
    out += body.close()
    assert out == raw
    assert body.hit is None


TOKEN_A = b"CHEESELESS-SENTINEL-alpha01"
TOKEN_B = b"CHEESELESS-SENTINEL-beta02"
TOKEN_C = b"CHEESELESS-SENTINEL-gamma03"


def test_sentinel_problems_flags_a_class_below_the_minimum():
    hidden = [("acceptance/hidden/test_a.py", b"# " + TOKEN_A)]
    problems = dlp.sentinel_problems(hidden, [])
    assert len(problems) == 1
    assert "tests" in problems[0]
    assert "1" in problems[0]
    assert str(dlp.MIN_SENTINELS_PER_CLASS) in problems[0]
    assert TOKEN_A.decode() not in problems[0]


def test_sentinel_problems_accepts_exactly_the_minimum():
    hidden = [("acceptance/hidden/test_a.py", TOKEN_A + b" " + TOKEN_B)]
    assert dlp.sentinel_problems(hidden, []) == []


def test_sentinel_problems_counts_a_duplicate_token_once():
    hidden = [
        ("acceptance/hidden/test_a.py", TOKEN_A),
        ("acceptance/hidden/test_b.py", TOKEN_A + b" " + TOKEN_A),
    ]
    problems = dlp.sentinel_problems(hidden, [])
    assert len(problems) == 1
    assert "1" in problems[0]


def test_sentinel_problems_does_not_count_a_publicly_visible_token():
    hidden = [("acceptance/hidden/test_a.py", TOKEN_A + b" " + TOKEN_B)]
    problems = dlp.sentinel_problems(hidden, [b"projection leaks " + TOKEN_B])
    text = " ".join(problems)
    assert any("visible in the projection" in problem for problem in problems)
    assert dlp.digest(TOKEN_B) in text
    assert any("tests" in problem and "1" in problem for problem in problems)
    assert TOKEN_B.decode() not in text


def test_sentinel_problems_ignores_classes_without_hidden_artifacts():
    hidden = [("acceptance/hidden/test_a.py", TOKEN_A + b" " + TOKEN_B)]
    assert dlp.sentinel_problems(hidden, []) == []


def test_sentinel_problems_never_puts_a_token_in_a_message():
    hidden = [("acceptance/hidden/a_fixture.py", TOKEN_C)]
    problems = dlp.sentinel_problems(hidden, [TOKEN_C])
    text = " ".join(problems)
    assert problems
    assert TOKEN_C.decode() not in text


def test_sentinel_problems_detects_a_suffixed_public_copy():
    hidden = [("acceptance/hidden/test_a.py", TOKEN_A + b" " + TOKEN_B)]
    problems = dlp.sentinel_problems(hidden, [b"copy " + TOKEN_A + b"X"])
    text = " ".join(problems)
    assert any("visible in the projection" in problem for problem in problems)
    assert dlp.digest(TOKEN_A) in text
    assert TOKEN_A.decode() not in text


def test_sentinel_problems_detects_a_prefixed_public_copy():
    hidden = [("acceptance/hidden/test_a.py", TOKEN_A + b" " + TOKEN_B)]
    problems = dlp.sentinel_problems(hidden, [b"prefix" + TOKEN_A])
    text = " ".join(problems)
    assert any("visible in the projection" in problem for problem in problems)
    assert TOKEN_A.decode() not in text


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("acceptance/hidden/test_x.py", dlp.ARTIFACT_TESTS),
        ("acceptance/hidden/data/fixtures.json", dlp.ARTIFACT_FIXTURES),
        ("acceptance/hidden/expected/out.json", dlp.ARTIFACT_EXPECTED),
        ("acceptance/hidden/golden/out.txt", dlp.ARTIFACT_EXPECTED),
        ("acceptance/hidden/snapshot/out.json", dlp.ARTIFACT_EXPECTED),
        ("acceptance/hidden/seed.json", dlp.ARTIFACT_SEED),
        ("acceptance/hidden/notes.md", dlp.ARTIFACT_NOTES),
        ("acceptance/hidden/readme.md", dlp.ARTIFACT_NOTES),
        ("acceptance/hidden/data/expected.json", dlp.ARTIFACT_EXPECTED),
        ("acceptance/hidden/data/seed.json", dlp.ARTIFACT_SEED),
        ("acceptance/hidden/fixtures/notes.md", dlp.ARTIFACT_FIXTURES),
        ("acceptance/hidden/seedless_test.py", dlp.ARTIFACT_TESTS),
    ],
)
def test_artifact_class_matches_whole_path_words_with_precedence(path, expected):
    assert dlp.artifact_class(path) == expected


def _base64_at(token: bytes, prefix_len: int) -> bytes:
    """A longer base64 blob in which ``token`` starts at the given byte alignment."""
    return base64.b64encode(b"q" * (prefix_len + 3) + token + b"!trailing bytes")


@pytest.mark.parametrize(
    "encode",
    [
        pytest.param(lambda t: t.lower(), id="lowercase"),
        pytest.param(lambda t: t[::-1], id="reversed"),
        pytest.param(lambda t: t[::-1].lower(), id="reversed-lowercase"),
        pytest.param(lambda t: t.hex().encode(), id="hex"),
        pytest.param(lambda t: t.hex().upper().encode(), id="hex-upper"),
        pytest.param(lambda t: _base64_at(t, 0), id="base64-align0"),
        pytest.param(lambda t: _base64_at(t, 1), id="base64-align1"),
        pytest.param(lambda t: _base64_at(t, 2), id="base64-align2"),
    ],
)
def test_sentinel_problems_detects_an_encoded_public_copy(encode):
    hidden = [("acceptance/hidden/test_a.py", TOKEN_A + b" " + TOKEN_B + b" " + TOKEN_C)]
    problems = dlp.sentinel_problems(hidden, [b"blob: " + encode(TOKEN_A) + b" end"])
    text = " ".join(problems)
    assert any("visible in the projection" in problem for problem in problems)
    assert dlp.digest(TOKEN_A) in text
    assert TOKEN_A.decode() not in text


def test_sentinel_problems_keeps_base64_case_sensitive():
    hidden = [("acceptance/hidden/test_a.py", TOKEN_A + b" " + TOKEN_B)]
    swapped = base64.b64encode(TOKEN_A).swapcase()
    assert dlp.sentinel_problems(hidden, [swapped]) == []


def test_sentinel_forms_are_unchanged_by_exposure_forms():
    forms = {form: packed for _, form, packed in dlp.sentinel_forms(TOKEN_A)}
    assert set(forms) == {"raw", "base64", "hex", "reversed"}
    assert forms["base64"].endswith(f":{len(base64.b64encode(TOKEN_A))}")


def test_redact_sentinels_replaces_each_marker_with_its_digest():
    text = f"hidden/test_x.py::test_y[{TOKEN_A.decode()}] and {TOKEN_B.decode()}"
    redacted = dlp.redact_sentinels(text)
    assert TOKEN_A.decode() not in redacted
    assert TOKEN_B.decode() not in redacted
    assert f"<sentinel digest {dlp.digest(TOKEN_A)}>" in redacted
    assert f"<sentinel digest {dlp.digest(TOKEN_B)}>" in redacted
    assert redacted.startswith("hidden/test_x.py::test_y[<sentinel digest ")


def test_redact_sentinels_leaves_marker_free_text_alone():
    assert dlp.redact_sentinels("criterion normalize needs at least 2 tests") == (
        "criterion normalize needs at least 2 tests"
    )


@pytest.mark.parametrize("line_end", [b"\n", b"\r\n", b" "])
def test_sentinel_problems_detects_line_wrapped_base64(line_end):
    hidden = [("acceptance/hidden/test_a.py", TOKEN_A + b" " + TOKEN_B + b" " + TOKEN_C)]
    wrapped = base64.encodebytes(b"x" * 48 + TOKEN_A + b"!tail").replace(b"\n", line_end)
    problems = dlp.sentinel_problems(hidden, [wrapped])
    assert any(dlp.digest(TOKEN_A) in problem for problem in problems)


def test_sentinel_problems_refuses_a_base64_near_miss_conservatively():
    # Boundary sextets are trimmed, so a public marker differing only in its last bits
    # still counts as exposing the hidden one. Freeze fails closed; the author renames.
    hidden = [("acceptance/hidden/test_a.py", TOKEN_A + b" " + TOKEN_B + b" " + TOKEN_C)]
    near_miss = base64.b64encode(b"z" + TOKEN_A[:-1] + b"0")
    problems = dlp.sentinel_problems(hidden, [near_miss])
    assert any(dlp.digest(TOKEN_A) in problem for problem in problems)


@pytest.mark.parametrize(
    "variant",
    [TOKEN_A.decode().lower(), "Cheeseless-Sentinel-alpha01", "cheeseless-canary-xyz123"],
)
def test_redact_sentinels_ignores_case(variant):
    redacted = dlp.redact_sentinels(f"skipped: {variant}")
    assert variant not in redacted
    assert "<sentinel digest " in redacted
    assert "cheeseless-" not in redacted.lower()
