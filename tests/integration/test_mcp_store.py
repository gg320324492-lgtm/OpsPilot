"""Contract tests for the shared JSON store (``mcp_servers/_store.py``).

The store is the demo's database, so the important properties here are the ones
that make a crash survivable: loading a seed, resetting to it, and writing
atomically. The atomicity tests are the reason this file exists -- a truncated
store would make the golden path unreproducible, and the failure would surface
somewhere far from the write that caused it.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from mcp_servers._store import Record, Store, StoreError, default_data_dir

_SEED = json.dumps(
    {
        "version": 1,
        "customers": [
            {"customer_id": "CUS-1001", "company": "ACME", "plan": "enterprise"},
            {"customer_id": "CUS-1002", "company": "Globex", "plan": "growth"},
        ],
        "widgets": [],
    }
)


@pytest.fixture
def seed_file(tmp_path: Path) -> Path:
    """A small committed-style seed in an isolated directory."""
    path = tmp_path / "seed.json"
    path.write_text(_SEED, encoding="utf-8")
    return path


@pytest.fixture
def store(seed_file: Path, tmp_path: Path) -> Store:
    """A store backed by ``seed_file``, persisting into ``tmp_path``."""
    return Store(seed_path=seed_file, filename="live.json", data_dir=tmp_path)


def test_loads_seed_into_memory(store: Store) -> None:
    assert set(store) == {"version", "customers", "widgets"}
    assert store.data["version"] == 1
    assert store.data["customers"][0]["company"] == "ACME"


def test_collection_returns_typed_records(store: Store) -> None:
    customers = store.collection("customers")
    assert len(customers) == 2
    assert all(isinstance(c, Record) for c in customers)
    assert customers[0].get("customer_id") == "CUS-1001"
    assert customers[0].get("company") == "ACME"


def test_records_are_immutable(store: Store) -> None:
    customer = store.collection("customers")[0]
    assert customer.model_config.get("frozen") is True
    with pytest.raises(ValidationError):
        # Assigning a field the seed defines is refused because the model is
        # frozen, so a read tool cannot mutate the store's state by accident.
        # `type: ignore[misc]` is needed only because the field is defined by
        # the seed rather than by the class and mypy cannot see it.
        customer.company = "Nope"  # type: ignore[attr-defined]


def test_missing_collection_is_empty_not_an_error(store: Store) -> None:
    assert store.collection("does_not_exist") == ()


def test_find_returns_match_or_none(store: Store) -> None:
    found = store.find("customers", customer_id="CUS-1001")
    assert found is not None
    assert found.get("company") == "ACME"
    assert store.find("customers", customer_id="NOPE") is None


def test_find_requires_all_fields_to_match(store: Store) -> None:
    # Correct id but wrong company -> no match.
    assert store.find("customers", customer_id="CUS-1001", company="Globex") is None


def test_reset_restores_after_mutation(store: Store) -> None:
    store.data["customers"][0]["company"] = "MUTATED"
    mutated = store.find("customers", customer_id="CUS-1001")
    assert mutated is not None
    assert mutated.get("company") == "MUTATED"

    store.reset()

    restored = store.find("customers", customer_id="CUS-1001")
    assert restored is not None
    assert restored.get("company") == "ACME"


def test_save_writes_valid_json_matching_memory(store: Store) -> None:
    store.data["customers"][0]["company"] = "Renamed"
    store.save()

    assert store.path.exists()
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk == store.data
    assert on_disk["customers"][0]["company"] == "Renamed"


def test_save_leaves_no_temp_file_behind(store: Store) -> None:
    store.save()
    leftovers = [p for p in store.path.parent.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_failed_save_does_not_corrupt_existing_file(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash mid-write must leave the previous complete document in place.

    The atomic replace is what gives this: the new content is staged in a temp
    file, and only a successful write is renamed over the real file. Here the
    write is made to fail after it begins (fsync raises), which models a crash
    between writing and committing.
    """
    store.data["customers"][0]["company"] = "First write"
    store.save()
    good = store.path.read_text(encoding="utf-8")

    def _boom(_fd: int) -> None:
        msg = "simulated crash during fsync"
        raise OSError(msg)

    monkeypatch.setattr(os, "fsync", _boom)

    store.data["customers"][0]["company"] = "Second write that must not land"
    with pytest.raises(StoreError):
        store.save()

    monkeypatch.undo()

    # The original file is byte-for-byte intact and still valid JSON.
    assert store.path.read_text(encoding="utf-8") == good
    assert json.loads(store.path.read_text(encoding="utf-8"))["customers"][0]["company"] == (
        "First write"
    )
    # And the failed attempt did not leave a temp file in the directory.
    assert [p for p in store.path.parent.iterdir() if p.name.endswith(".tmp")] == []


def test_missing_seed_file_raises_documented_error(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="seed file not found"):
        Store(seed_path=tmp_path / "missing.json")


def test_malformed_seed_file_raises_documented_error(tmp_path: Path) -> None:
    bad = tmp_path / "seed.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreError, match="not valid JSON"):
        Store(seed_path=bad)


def test_non_object_seed_raises_documented_error(tmp_path: Path) -> None:
    bad = tmp_path / "seed.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(StoreError, match="must contain a JSON object"):
        Store(seed_path=bad)


def test_default_data_dir_honours_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "isolated"
    monkeypatch.setenv("OPSPILOT_MCP_DATA_DIR", str(target))
    assert default_data_dir(tmp_path / "seed.json") == target.resolve()


def test_default_data_dir_is_outside_the_seed_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With nothing configured, the store must not write beside the seed file.

    The fallback used to be the seed's own directory, and the consequence was a
    live database appearing inside the repository: `mcp_servers/billing/
    billing.json` turned up holding a refund and `issues.json` holding two
    created issues, written by whichever call forgot to pass `data_dir`. The
    committed seed is the demo's database, so a mutable copy beside it makes the
    demo unreproducible and leaks state from one run into the next.

    Asserted as "not inside the seed's directory" rather than "equals some exact
    path", because the exact location is an implementation detail; what the
    contract is about is that it is not in the tree.
    """
    monkeypatch.delenv("OPSPILOT_MCP_DATA_DIR", raising=False)
    seed = tmp_path / "some-server" / "seed.json"
    resolved = default_data_dir(seed)

    assert resolved != seed.parent.resolve(), (
        f"the default data directory is the seed's own directory ({resolved}); a "
        f"store writing there puts a mutable database inside the source tree"
    )
    assert seed.parent.resolve() not in resolved.parents, (
        f"the default data directory {resolved} is inside the seed's directory tree"
    )
    assert resolved.name == seed.parent.name, (
        "the default directory should be named after the seed's parent so two "
        "servers never resolve to the same file"
    )


def test_store_file_is_isolated_from_the_seed(store: Store) -> None:
    """Saving must not overwrite the committed seed -- reset depends on it."""
    seed_before = store.seed_path.read_text(encoding="utf-8")
    store.data["customers"][0]["company"] = "Changed"
    store.save()
    assert store.path != store.seed_path
    assert store.seed_path.read_text(encoding="utf-8") == seed_before


# ---------------------------------------------------------------------------
# The in-process write lock (``Store.writing``)
#
# The SDK runs sync tool functions on worker threads, so a mutating tool's
# check-then-write needs the lock to be atomic against its own process. These
# tests pin the lock's semantics directly, deterministically, without racing:
# mutual exclusion (one writer inside at a time, the rest blocked), hand-off on
# exit, and release on exception. A store without the lock fails every one of
# them, which is exactly what makes them the C2 guarantee's contract.
# ---------------------------------------------------------------------------


def test_writing_admits_one_thread_at_a_time(seed_file: Path, tmp_path: Path) -> None:
    """A second thread reaching the section waits until the first has left.

    Two threads enter ``writing()`` against the same store. The first holds a
    gate open inside the section; the second must still be blocked while the
    gate is held, and only get through after the first releases it. With no
    lock, both threads are inside at once and the second's check fails
    immediately -- which is the double-write window this lock exists to close.
    """
    probe = Store(seed_path=seed_file, filename="probe.json", data_dir=tmp_path)
    first_is_inside = threading.Event()
    first_may_leave = threading.Event()
    observations: list[str] = []
    errors: list[BaseException] = []

    def writer(name: str, hold_gate: bool) -> None:
        try:
            with probe.writing():
                observations.append(f"{name}:inside")
                if hold_gate:
                    first_is_inside.set()
                    assert first_may_leave.wait(30), "the gate was never released"
                # A second thread that is correctly blocked cannot append
                # while the first is still inside.
                observations.append(f"{name}:done")
        except BaseException as exc:  # pragma: no cover - only on a broken run
            errors.append(exc)

    first = threading.Thread(target=writer, args=("first", True))
    second = threading.Thread(target=writer, args=("second", False))
    first.start()
    assert first_is_inside.wait(30), "the first writer never entered the section"

    second.start()
    # Give the second thread a real chance to barge in. If the lock works, it
    # stays blocked for the whole wait; if it does not, it appends within
    # microseconds and this catches it.
    time.sleep(0.2)
    assert observations == ["first:inside"], (
        f"a second writer entered the section while the first held it: {observations}"
    )

    first_may_leave.set()
    first.join(30)
    second.join(30)
    assert not first.is_alive() and not second.is_alive(), "a writer hung"
    assert not errors, f"a writer crashed: {errors!r}"
    assert observations == ["first:inside", "first:done", "second:inside", "second:done"], (
        f"unexpected interleaving: {observations}"
    )


def test_writing_releases_the_lock_when_the_body_raises(seed_file: Path, tmp_path: Path) -> None:
    """An exception inside the section must not poison the lock for ever.

    A tool that raises mid-write must not leave every later write blocked for
    the life of the process: the lock has to be released on the way out, or one
    failed refund would take the whole server's write path down with it.
    """
    probe = Store(seed_path=seed_file, filename="probe.json", data_dir=tmp_path)
    with pytest.raises(RuntimeError, match="boom"), probe.writing():
        raise RuntimeError("boom")

    # The lock was released: another thread (or this one) can enter again.
    entered = threading.Event()

    def reenter() -> None:
        with probe.writing():
            entered.set()

    thread = threading.Thread(target=reenter)
    thread.start()
    assert entered.wait(30), "the lock was not released after the body raised"
    thread.join(30)
    assert not thread.is_alive()


def test_writing_is_per_store_not_global(seed_file: Path, tmp_path: Path) -> None:
    """Two stores over one file do not share a lock -- and that is the boundary.

    Each ``Store`` holds its own lock, so two independent stores (the
    two-process case, modelled in one process) can both be inside their write
    sections at once. The contract says this case is *not* covered -- the
    atomic replace keeps one whole document and discards the other -- so the
    test pins the boundary rather than the guarantee: one store's lock must not
    silently become a global lock that would only flatter the cross-process
    story.
    """
    store_a = Store(seed_path=seed_file, filename="live.json", data_dir=tmp_path)
    store_b = Store(seed_path=seed_file, filename="live.json", data_dir=tmp_path)

    a_inside = threading.Event()
    both_inside = threading.Event()
    errors: list[BaseException] = []

    def writer(store: Store, announce: threading.Event, confirm: threading.Event | None) -> None:
        try:
            with store.writing():
                announce.set()
                if confirm is not None:
                    assert confirm.wait(30), "the other store never entered"
        except BaseException as exc:  # pragma: no cover - only on a broken run
            errors.append(exc)

    thread_a = threading.Thread(target=writer, args=(store_a, a_inside, both_inside))
    thread_b = threading.Thread(target=writer, args=(store_b, both_inside, None))
    thread_a.start()
    assert a_inside.wait(30)
    thread_b.start()
    # Store B's lock is its own: it enters while A still holds A's.
    assert both_inside.wait(30), "store B could not enter while store A held its own lock"
    thread_a.join(30)
    thread_b.join(30)
    assert not errors, f"a writer crashed: {errors!r}"
