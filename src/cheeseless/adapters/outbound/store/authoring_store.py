"""Authoring records in the trusted store (ADR 0009, stages d and f).

Mixed into ``FileStore``. Everything lives under ``authoring/``, created lazily so a
repository that never authors keeps today's layout. State and the reference snapshot
are keyed by lineage id (the task id changes with every tree); proposals and
crosschecks are keyed by the proposal id, which equals the future task id.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from cheeseless.adapters.outbound.store import _fs, codec
from cheeseless.domain import ids
from cheeseless.domain.authoring import (
    AuthoringError,
    AuthoringState,
    CrosscheckRecord,
    Proposal,
    StrengthReview,
    TaskPhase,
)
from cheeseless.ports.store import StoreError

_DIR = "authoring"


def _checked(check: Any, value: str, what: str) -> str:
    try:
        return str(check(value))
    except ValueError as exc:
        raise StoreError(f"invalid {what}: {exc}") from exc


def _lineage(value: str) -> str:
    return _checked(ids.lineage_id, value, "lineage id")


def _digest(value: str, what: str) -> str:
    return _checked(lambda v: ids.sha256_hex(v, what), value, what)


class AuthoringStoreMixin:
    _root: Path

    def _authoring_path(self, name: str) -> Path:
        return self._root / _DIR / name

    def _read_authoring(self, name: str) -> dict[str, Any] | None:
        raw = _fs.read_bytes(self._authoring_path(name))
        return None if raw is None else codec.loads(raw, name)

    def _write_authoring(self, name: str, data: dict[str, Any]) -> None:
        _fs.ensure_private_dir(self._root / _DIR)
        with _fs.locked(self._root / "store.lock"):
            _fs.write_atomic(self._authoring_path(name), codec.dumps(data))

    def _write_if_current(
        self, lineage: str, expected_version: int, name: str, data: dict[str, Any]
    ) -> None:
        """Publish ``data`` only while the lifecycle is still at ``expected_version``.

        The check and the write share the ``store.lock`` flock with the state CAS, so a
        reservation that lands in between makes the late writer lose."""
        _fs.ensure_private_dir(self._root / _DIR)
        with _fs.locked(self._root / "store.lock"):
            if self._stored_version(lineage) != expected_version:
                raise AuthoringError("the authoring state changed since it was read")
            _fs.write_atomic(self._authoring_path(name), codec.dumps(data))

    def _stored_version(self, lineage: str) -> int:
        data = self._read_authoring(f"state-{lineage}.json")
        if data is None:
            return 0
        try:
            return int(data["version"])
        except (KeyError, ValueError, TypeError) as exc:
            raise StoreError(f"corrupt authoring state for {lineage}: {exc}") from exc

    # -- authoring state -----------------------------------------------------

    def authoring_state(self, lineage_id: str, spec_digest: str) -> AuthoringState:
        lineage = _lineage(lineage_id)
        spec = _digest(spec_digest, "spec digest")
        data = self._read_authoring(f"state-{lineage}.json")
        if data is None:
            return AuthoringState(lineage, spec)
        try:
            stored = AuthoringState(
                lineage,
                str(data["spec_digest"]),
                TaskPhase(data["phase"]),
                int(data["reference_failures"]),
                int(data["retries_used"]),
                int(data["version"]),
            )
            return stored if stored.spec_digest == spec else stored.spec_edited(spec)
        except (KeyError, ValueError, TypeError, AuthoringError) as exc:
            raise StoreError(f"corrupt authoring state for {lineage}: {exc}") from exc

    def save_authoring_state(self, state: AuthoringState) -> AuthoringState:
        lineage = _lineage(state.lineage_id)
        _fs.ensure_private_dir(self._root / _DIR)
        with _fs.locked(self._root / "store.lock"):
            if self._stored_version(lineage) != state.version:
                raise AuthoringError("the authoring state changed since it was read")
            saved = replace(state, version=state.version + 1)
            _fs.write_atomic(
                self._authoring_path(f"state-{lineage}.json"),
                codec.dumps(
                    {
                        "spec_digest": saved.spec_digest,
                        "phase": saved.phase.value,
                        "reference_failures": saved.reference_failures,
                        "retries_used": saved.retries_used,
                        "version": saved.version,
                    }
                ),
            )
        return saved

    @contextmanager
    def hold_open(self, lineage_id: str, expected_version: int) -> Iterator[None]:
        lineage = _lineage(lineage_id)
        _fs.ensure_private_dir(self._root / _DIR)
        with _fs.locked(self._root / "store.lock"):
            if self._stored_version(lineage) != expected_version:
                raise AuthoringError("the authoring state changed since it was read")
            yield

    @contextmanager
    def approval_ownership(self, lineage_id: str) -> Iterator[None]:
        lineage = _lineage(lineage_id)
        _fs.ensure_private_dir(self._root / _DIR)
        with _fs.try_locked(self._authoring_path(f"approval-{lineage}.lock")) as owned:
            if not owned:
                raise AuthoringError("the approval ownership lock is held by a live approver")
            yield

    # -- proposals -----------------------------------------------------------

    def save_proposal(self, proposal: Proposal) -> None:
        pid = _digest(proposal.proposal_id, "proposal id")
        self._write_authoring(
            f"proposal-{pid}.json",
            {
                "lineage_id": proposal.lineage_id,
                "baseline_commit": proposal.baseline_commit,
                "acceptance_tree": proposal.acceptance_tree,
                "spec_digest": proposal.spec_digest,
            },
        )

    def load_proposal(self, proposal_id: str) -> Proposal | None:
        pid = _digest(proposal_id, "proposal id")
        data = self._read_authoring(f"proposal-{pid}.json")
        if data is None:
            return None
        try:
            return Proposal(
                pid,
                str(data["lineage_id"]),
                str(data["baseline_commit"]),
                str(data["acceptance_tree"]),
                str(data["spec_digest"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise StoreError(f"corrupt proposal {pid}: {exc}") from exc

    # -- reference snapshot --------------------------------------------------

    def save_reference(
        self, lineage_id: str, spec_digest: str, tree: str, *, expected_version: int
    ) -> None:
        lineage = _lineage(lineage_id)
        self._write_if_current(
            lineage,
            expected_version,
            f"reference-{lineage}.json",
            {
                "spec_digest": _digest(spec_digest, "spec digest"),
                "tree": _checked(ids.git_oid, tree, "reference tree"),
            },
        )

    def load_reference(self, lineage_id: str) -> tuple[str, str] | None:
        lineage = _lineage(lineage_id)
        data = self._read_authoring(f"reference-{lineage}.json")
        if data is None:
            return None
        try:
            return (
                _digest(str(data["spec_digest"]), "spec digest"),
                _checked(ids.git_oid, str(data["tree"]), "reference tree"),
            )
        except KeyError as exc:
            raise StoreError(f"corrupt reference record for {lineage}") from exc

    # -- author check --------------------------------------------------------

    def save_author_check(
        self, lineage_id: str, spec_digest: str, tree: str, *, expected_version: int
    ) -> None:
        lineage = _lineage(lineage_id)
        self._write_if_current(
            lineage,
            expected_version,
            f"author-check-{lineage}.json",
            {
                "spec_digest": _digest(spec_digest, "spec digest"),
                "tree": _checked(ids.git_oid, tree, "acceptance tree"),
            },
        )

    def load_author_check(self, lineage_id: str) -> tuple[str, str] | None:
        lineage = _lineage(lineage_id)
        data = self._read_authoring(f"author-check-{lineage}.json")
        if data is None:
            return None
        try:
            return (
                _digest(str(data["spec_digest"]), "spec digest"),
                _checked(ids.git_oid, str(data["tree"]), "acceptance tree"),
            )
        except KeyError as exc:
            raise StoreError(f"corrupt author check record for {lineage}") from exc

    # -- crosscheck ----------------------------------------------------------

    def save_crosscheck(
        self, proposal_id: str, lineage_id: str, record: CrosscheckRecord, *, expected_version: int
    ) -> None:
        pid = _digest(proposal_id, "proposal id")
        self._write_if_current(
            _lineage(lineage_id),
            expected_version,
            f"crosscheck-{pid}.json",
            {
                "acceptance_tree": record.acceptance_tree,
                "spec_digest": record.spec_digest,
                "baseline_commit": record.baseline_commit,
                "reference_tree": record.reference_tree,
                "green": record.green,
                "red_criteria": list(record.red_criteria),
            },
        )

    def load_crosscheck(self, proposal_id: str) -> CrosscheckRecord | None:
        pid = _digest(proposal_id, "proposal id")
        data = self._read_authoring(f"crosscheck-{pid}.json")
        if data is None:
            return None
        try:
            return CrosscheckRecord(
                str(data["acceptance_tree"]),
                str(data["spec_digest"]),
                str(data["baseline_commit"]),
                str(data["reference_tree"]),
                bool(data["green"]),
                tuple(str(c) for c in data["red_criteria"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise StoreError(f"corrupt crosscheck {pid}: {exc}") from exc

    # -- strength review -----------------------------------------------------

    def save_strength_review(
        self,
        proposal_id: str,
        lineage_id: str,
        record: StrengthReview,
        *,
        expected_version: int,
    ) -> None:
        pid = _digest(proposal_id, "proposal id")
        self._write_if_current(
            _lineage(lineage_id),
            expected_version,
            f"strength-review-{pid}.json",
            {
                "acceptance_tree": record.acceptance_tree,
                "spec_digest": record.spec_digest,
                "baseline_commit": record.baseline_commit,
                "green": record.green,
                "author_vendor": record.author_vendor,
                "reviewer_vendor": record.reviewer_vendor,
            },
        )

    def load_strength_review(self, proposal_id: str) -> StrengthReview | None:
        pid = _digest(proposal_id, "proposal id")
        data = self._read_authoring(f"strength-review-{pid}.json")
        if data is None:
            return None
        try:
            return StrengthReview(
                proposal_id=proposal_id,
                acceptance_tree=str(data["acceptance_tree"]),
                spec_digest=str(data["spec_digest"]),
                baseline_commit=str(data["baseline_commit"]),
                green=bool(data["green"]),
                author_vendor=str(data["author_vendor"]),
                reviewer_vendor=str(data["reviewer_vendor"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise StoreError(f"corrupt strength review {pid}: {exc}") from exc
