"""The observer's store has no write surface at runtime, not just by convention."""

from __future__ import annotations

import pytest

from cheeseless.adapters.outbound.store import dev_store, reference_store
from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.adapters.outbound.store.observe_store import ObserveStore

_WRITE_PREFIXES = ("save_", "transition_", "bind_", "file_")
_READ_API = {"dev_state", "reference_attempt", "reference_attempts", "disputes"}


def _own(cls: type) -> set[str]:
    return {name for name in vars(cls) if not name.startswith("__")}


def _write_names() -> set[str]:
    """Every name the write mixins add on top of their read-only bases."""
    names: set[str] = set()
    for write, read in (
        (dev_store.DevStoreMixin, dev_store.DevReadMixin),
        (reference_store.ReferenceStoreMixin, reference_store.ReferenceReadMixin),
    ):
        names |= _own(write) - _own(read)
    return names


def test_write_names_are_derived_from_the_write_mixins() -> None:
    names = _write_names()

    assert {"save_dev_state", "save_reference_attempt", "reference_log_path"} <= names
    assert {"transition_reference_attempt", "bind_reference_attempt"} <= names


@pytest.mark.parametrize("name", sorted(_write_names()))
def test_observe_store_does_not_expose_a_write_method(name: str) -> None:
    assert not hasattr(ObserveStore, name)


def test_observe_store_has_no_write_prefixed_name_from_the_file_store() -> None:
    risky = {
        name
        for name in dir(FileStore)
        if name.startswith(_WRITE_PREFIXES) and not name.startswith("__")
    }

    assert risky  # the guard itself must see the write API
    assert not {name for name in risky if hasattr(ObserveStore, name)}


def test_observe_store_public_surface_is_exactly_the_port() -> None:
    public = {name for name in dir(ObserveStore) if not name.startswith("_")}

    assert public == _READ_API
