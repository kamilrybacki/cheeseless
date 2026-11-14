"""Installation-token minting for the judge App (RS256 JWT, no network until used)."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from tests.live.github_client import ClientError, GitHubClient, Token


class TokenMinter(Protocol):
    def actor_name(self) -> str: ...

    def mint(
        self,
        repo: str,
        *,
        permissions: Mapping[str, str] | None = None,
        repositories: Sequence[str] | None = None,
    ) -> Token: ...


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def build_jwt(app_id: str, key_pem: bytes, *, now: int) -> str:
    """A short-lived RS256 App JWT. The caller keeps the key bytes out of every message."""
    key = serialization.load_pem_private_key(key_pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ClientError("the App key is not an RSA private key")
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id}).encode())
    signature = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{_b64(signature)}"


class AppTokenMinter:
    """Mints installation tokens and revokes every one of them in :meth:`close`."""

    def __init__(
        self,
        client: GitHubClient,
        *,
        app_id: str,
        key_file: Path,
        installations: Mapping[str, int],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._app_id = app_id
        self._key_file = key_file
        self._installations = dict(installations)
        self._clock = clock
        self._minted: list[Token] = []

    def _jwt(self) -> Token:
        try:
            pem = self._key_file.read_bytes()
        except OSError:
            raise ClientError("the App key file cannot be read") from None
        try:
            return Token(build_jwt(self._app_id, pem, now=int(self._clock())), "app-jwt")
        except ValueError:
            raise ClientError("the App key file is not a PEM private key") from None

    def actor_name(self) -> str:
        resp = self._client.request("GET", "app", token=self._jwt())
        slug = resp.get("slug")
        if resp.status != 200 or not isinstance(slug, str) or not slug:
            raise ClientError("cannot read authenticated App identity")
        return f"{slug}[bot]"

    def _installation_id(self, repo: str, jwt: Token) -> int:
        if repo in self._installations:
            return self._installations[repo]
        resp = self._client.request("GET", f"repos/{repo}/installation", token=jwt)
        found = resp.get("id")
        if resp.status != 200 or not isinstance(found, int):
            raise ClientError(f"the App is not installed on {repo} ({resp.meta()})")
        self._installations[repo] = found
        return found

    def mint(
        self,
        repo: str,
        *,
        permissions: Mapping[str, str] | None = None,
        repositories: Sequence[str] | None = None,
    ) -> Token:
        jwt = self._jwt()
        body: dict[str, object] = {}
        if permissions:
            body["permissions"] = dict(permissions)
        if repositories:
            body["repositories"] = list(repositories)
        resp = self._client.request(
            "POST",
            f"app/installations/{self._installation_id(repo, jwt)}/access_tokens",
            token=jwt,
            json=body or None,
        )
        value = resp.get("token")
        if resp.status != 201 or not isinstance(value, str):
            raise ClientError(f"cannot mint an installation token ({resp.meta()})")
        token = Token(value, "installation")
        self._minted.append(token)
        return token

    def close(self) -> None:
        """Revoke every minted token; a failed revoke is ignored, tokens expire anyway."""
        for token in self._minted:
            try:
                self._client.request("DELETE", "installation/token", token=token)
            except ClientError:
                continue
        self._minted.clear()
