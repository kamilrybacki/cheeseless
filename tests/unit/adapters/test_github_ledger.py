"""GitHub ledger transport: request shapes, status mapping, no token leaks (section 3)."""

from __future__ import annotations

import base64
import http.server
import json
import threading
import urllib.request
from collections.abc import Iterator

import pytest

from cheeseless.adapters.outbound.ci.github_ledger import (
    LedgerMalformed,
    TransportError,
    UpdateResult,
    UrllibLedgerTransport,
    default_send,
)
from cheeseless.domain.budget_ledger import RECORD_FILE

REPO = "example-owner/cheeseless-ledger"
TOKEN = "ghs_FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE"  # noqa: S105 - fake
BRANCH = "cheeseless/budget/toy-task"
API = "https://api.example.test"
C1, C2, TREE, BLOB = "1" * 40, "2" * 40, "3" * 40, "4" * 40


class FakeSend:
    """Scripted responses keyed by (method, path); records every request."""

    def __init__(self, routes: dict[tuple[str, str], tuple[int, object]]) -> None:
        self.routes = routes
        self.requests: list[urllib.request.Request] = []

    def __call__(self, req: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
        self.requests.append(req)
        path = req.full_url.removeprefix(f"{API}/repos/{REPO}/")
        status, body = self.routes[(req.get_method(), path)]
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return status, raw

    def body(self, i: int) -> dict[str, object]:
        data = self.requests[i].data
        assert isinstance(data, bytes)
        return json.loads(data)


def _transport(send: FakeSend) -> UrllibLedgerTransport:
    return UrllibLedgerTransport(REPO, TOKEN, api_url=API, send=send)


def _commit_routes(*, message: str = "msg", tree: object = None, blob: object = None) -> dict:
    tree = (
        tree
        if tree is not None
        else {
            "sha": TREE,
            "truncated": False,
            "tree": [{"path": RECORD_FILE, "type": "blob", "sha": BLOB, "mode": "100644"}],
        }
    )
    blob = (
        blob
        if blob is not None
        else {
            "sha": BLOB,
            "encoding": "base64",
            "content": base64.b64encode(b"record").decode(),
        }
    )
    return {
        ("GET", f"git/commits/{C2}"): (
            200,
            {"sha": C2, "message": message, "tree": {"sha": TREE}, "parents": [{"sha": C1}]},
        ),
        ("GET", f"git/trees/{TREE}"): (200, tree),
        ("GET", f"git/blobs/{BLOB}"): (200, blob),
    }


# -- get_ref ----------------------------------------------------------------------


def test_get_ref_returns_the_tip_with_auth_headers() -> None:
    send = FakeSend({("GET", f"git/ref/heads/{BRANCH}"): (200, {"object": {"sha": C1}})})
    assert _transport(send).get_ref(BRANCH) == C1
    req = send.requests[0]
    assert req.get_header("Authorization") == f"Bearer {TOKEN}"
    assert req.get_header("Accept") == "application/vnd.github+json"
    assert req.get_header("X-github-api-version") == "2022-11-28"


def test_get_ref_maps_404_to_missing() -> None:
    send = FakeSend({("GET", f"git/ref/heads/{BRANCH}"): (404, {"message": "Not Found"})})
    assert _transport(send).get_ref(BRANCH) is None


@pytest.mark.parametrize("status", [401, 403, 500, 502])
def test_get_ref_other_statuses_are_transport_errors(status: int) -> None:
    send = FakeSend({("GET", f"git/ref/heads/{BRANCH}"): (status, {"message": TOKEN})})
    with pytest.raises(TransportError) as err:
        _transport(send).get_ref(BRANCH)
    assert TOKEN not in str(err.value)


@pytest.mark.parametrize("body", [b"not json", {"object": {}}, {"object": {"sha": "zz"}}, []])
def test_get_ref_malformed_responses_are_refused(body: object) -> None:
    send = FakeSend({("GET", f"git/ref/heads/{BRANCH}"): (200, body)})
    with pytest.raises(LedgerMalformed):
        _transport(send).get_ref(BRANCH)


# -- get_commit -------------------------------------------------------------------


def test_get_commit_reads_commit_tree_and_blob() -> None:
    send = FakeSend(_commit_routes())
    commit = _transport(send).get_commit(C2)
    assert commit.sha == C2
    assert commit.parents == (C1,)
    assert commit.message == b"msg"
    assert dict(commit.files) == {RECORD_FILE: b"record"}


@pytest.mark.parametrize(
    "tree",
    [
        {"sha": TREE, "truncated": True, "tree": []},
        {"sha": TREE, "truncated": False, "tree": []},
        {
            "sha": TREE,
            "truncated": False,
            "tree": [{"path": RECORD_FILE, "type": "tree", "sha": BLOB, "mode": "040000"}],
        },
        {"sha": "5" * 40, "truncated": False, "tree": []},
        {"sha": TREE},
    ],
)
def test_get_commit_refuses_odd_trees(tree: object) -> None:
    with pytest.raises(LedgerMalformed):
        _transport(FakeSend(_commit_routes(tree=tree))).get_commit(C2)


def test_get_commit_keeps_both_entries_of_a_two_file_tree_for_the_codec() -> None:
    tree = {
        "sha": TREE,
        "truncated": False,
        "tree": [
            {"path": RECORD_FILE, "type": "blob", "sha": BLOB, "mode": "100644"},
            {"path": "extra", "type": "blob", "sha": BLOB, "mode": "100644"},
        ],
    }
    commit = _transport(FakeSend(_commit_routes(tree=tree))).get_commit(C2)
    assert set(commit.files) == {RECORD_FILE, "extra"}


@pytest.mark.parametrize(
    "blob",
    [
        {"sha": BLOB, "encoding": "utf-8", "content": "record"},
        {"sha": BLOB, "encoding": "base64", "content": "!!!"},
        {"sha": "6" * 40, "encoding": "base64", "content": ""},
    ],
)
def test_get_commit_refuses_odd_blobs(blob: object) -> None:
    with pytest.raises(LedgerMalformed):
        _transport(FakeSend(_commit_routes(blob=blob))).get_commit(C2)


def test_get_commit_refuses_a_commit_under_another_sha() -> None:
    routes = _commit_routes()
    routes[("GET", f"git/commits/{C2}")] = (
        200,
        {"sha": C1, "message": "m", "tree": {"sha": TREE}, "parents": []},
    )
    with pytest.raises(LedgerMalformed):
        _transport(FakeSend(routes)).get_commit(C2)


def test_get_commit_404_is_a_transport_error() -> None:
    send = FakeSend({("GET", f"git/commits/{C2}"): (404, {})})
    with pytest.raises(TransportError):
        _transport(send).get_commit(C2)


def test_get_commit_refuses_a_bad_sha_before_any_request() -> None:
    send = FakeSend({})
    with pytest.raises(LedgerMalformed):
        _transport(send).get_commit("../../etc")
    assert send.requests == []


# -- write_commit -----------------------------------------------------------------


def test_write_commit_creates_blob_tree_and_commit() -> None:
    send = FakeSend(
        {
            ("POST", "git/blobs"): (201, {"sha": BLOB}),
            ("POST", "git/trees"): (201, {"sha": TREE}),
            ("POST", "git/commits"): (201, {"sha": C2}),
        }
    )
    sha = _transport(send).write_commit(parent=C1, message=b"rec", files={RECORD_FILE: b"rec"})
    assert sha == C2
    assert send.body(0) == {"content": base64.b64encode(b"rec").decode(), "encoding": "base64"}
    assert send.body(1) == {
        "tree": [{"path": RECORD_FILE, "mode": "100644", "type": "blob", "sha": BLOB}]
    }
    assert send.body(2) == {"message": "rec", "tree": TREE, "parents": [C1]}
    assert send.requests[0].get_header("Content-type") == "application/json"


def test_write_commit_failure_is_a_transport_error() -> None:
    send = FakeSend({("POST", "git/blobs"): (500, {})})
    with pytest.raises(TransportError):
        _transport(send).write_commit(parent=C1, message=b"r", files={RECORD_FILE: b"r"})


# -- update_ref -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "result"), [(200, UpdateResult.UPDATED), (422, UpdateResult.REJECTED)]
)
def test_update_ref_is_a_non_force_fast_forward(status: int, result: UpdateResult) -> None:
    send = FakeSend({("PATCH", f"git/refs/heads/{BRANCH}"): (status, {})})
    assert _transport(send).update_ref(BRANCH, C2) is result
    assert send.body(0) == {"sha": C2, "force": False}


@pytest.mark.parametrize("status", [404, 409, 500, 503])
def test_update_ref_other_statuses_are_ambiguous(status: int) -> None:
    send = FakeSend({("PATCH", f"git/refs/heads/{BRANCH}"): (status, {})})
    with pytest.raises(TransportError):
        _transport(send).update_ref(BRANCH, C2)


# -- configuration and secrecy ----------------------------------------------------


@pytest.mark.parametrize(
    ("repo", "token", "api"),
    [
        ("not a repo", TOKEN, API),
        (REPO, "", API),
        (REPO, TOKEN, "http://api.example.test"),
        (REPO, "bad\ntoken", API),
    ],
)
def test_bad_configuration_is_refused(repo: str, token: str, api: str) -> None:
    with pytest.raises(ValueError) as err:
        UrllibLedgerTransport(repo, token, api_url=api, send=FakeSend({}))
    assert TOKEN not in str(err.value)


def test_repr_hides_the_token() -> None:
    assert TOKEN not in repr(_transport(FakeSend({})))


def test_send_errors_never_carry_the_token() -> None:
    def broken(req: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
        raise OSError(f"boom {req.get_header('Authorization')}")

    with pytest.raises(TransportError) as err:
        UrllibLedgerTransport(REPO, TOKEN, api_url=API, send=broken).get_ref(BRANCH)
    assert TOKEN not in str(err.value)
    assert err.value.__cause__ is None


# -- default sender ---------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()
        elif self.path == "/big":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * (2 << 20))
        elif self.path == "/missing":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"message":"Not Found"}')
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@pytest.fixture
def server() -> Iterator[str]:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def _local(url: str) -> urllib.request.Request:
    return urllib.request.Request(url)  # noqa: S310 - loopback test server


def test_default_send_returns_status_and_body(server: str) -> None:
    assert default_send(_local(f"{server}/ok"), 5.0) == (200, b'{"ok":true}')
    assert default_send(_local(f"{server}/missing"), 5.0)[0] == 404


def test_default_send_never_follows_redirects(server: str) -> None:
    status, _ = default_send(_local(f"{server}/redirect"), 5.0)
    assert status == 302


def test_default_send_caps_the_response(server: str) -> None:
    with pytest.raises(TransportError):
        default_send(_local(f"{server}/big"), 5.0)


def test_default_send_maps_connection_failures() -> None:
    with pytest.raises(TransportError):
        default_send(urllib.request.Request("http://127.0.0.1:1/"), 2.0)


# -- seeding: write_root and create_ref -------------------------------------------


def test_write_root_creates_a_parentless_commit() -> None:
    send = FakeSend(
        {
            ("POST", "git/blobs"): (201, {"sha": BLOB}),
            ("POST", "git/trees"): (201, {"sha": TREE}),
            ("POST", "git/commits"): (201, {"sha": C1}),
        }
    )
    sha = _transport(send).write_root(message=b"seed", files={RECORD_FILE: b"seed"})
    assert sha == C1
    assert send.body(2) == {"message": "seed", "tree": TREE, "parents": []}


@pytest.mark.parametrize(
    ("status", "result"), [(201, UpdateResult.UPDATED), (422, UpdateResult.REJECTED)]
)
def test_create_ref_posts_a_new_branch(status: int, result: UpdateResult) -> None:
    send = FakeSend({("POST", "git/refs"): (status, {})})
    assert _transport(send).create_ref(BRANCH, C1) is result
    assert send.body(0) == {"ref": f"refs/heads/{BRANCH}", "sha": C1}


@pytest.mark.parametrize("status", [403, 404, 409, 500])
def test_create_ref_other_statuses_are_transport_errors(status: int) -> None:
    send = FakeSend({("POST", "git/refs"): (status, {})})
    with pytest.raises(TransportError):
        _transport(send).create_ref(BRANCH, C1)


def test_create_ref_refuses_a_bad_sha_before_any_request() -> None:
    send = FakeSend({})
    with pytest.raises(LedgerMalformed):
        _transport(send).create_ref(BRANCH, "nope")
    assert send.requests == []
