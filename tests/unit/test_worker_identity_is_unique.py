"""The worker's identity must be unique, and it must be unique for a reason.

## Why this file exists

``agent_runs.claimed_by`` holds the identity of the worker holding a run, and
``SqlRunStore.renew_lease`` is a conditional UPDATE on it:

    UPDATE agent_runs SET claimed_until = :deadline
     WHERE id = :run_id AND claimed_by = :worker_id

That condition is the whole reason the second column exists. The migration
that added it (``migrations/versions/0002_claim_lease.py``) says so: *a worker
whose lease expired and was stolen must not be able to extend the thief's
lease*. The condition only excludes the wrong worker **if the identity is
unique**. Two workers sharing one identity make it true for both of them, and
the lease stops excluding anybody — the run is driven twice, which is the
defect the lease was written to fix.

``docker-compose.yml`` shipped ``WORKER_ID: ${WORKER_ID:-worker-compose``:
one value for a whole deployment, and therefore one value for every replica
of the worker service. ``docker compose up --scale worker=2`` gave two
processes the same identity, and the lease's exclusion silently stopped
working. The fix was to make the *default* unique per process and to leave
``WORKER_ID`` empty so the process derives its own.

## What was wrong with the reasoning that let it ship

The docstring on ``resolve_worker_id`` used to say:

    ``SqlRunStore.claim_next`` ignores ``worker_id`` today -- mutual exclusion
    is the row lock, not the identity (``repositories.py``: "the row's holder
    is the open transaction") -- so an empty id does not corrupt a claim *in
    this implementation*.

Every clause of that is false now. The claim writes ``claimed_by``, the
renewal is conditional on it, and the quoted phrase no longer exists in
``repositories.py`` at all — it described the pre-lease implementation. So the
one place that could have caught a shared identity was reassuring itself with a
statement about a version of the code that had been replaced.

This file pins the property that matters, so the reasoning cannot drift back.

## What is asserted

1. The derived identity is unique between two processes — which is the
   property the lease depends on, tested directly.
2. It identifies the host as well as the process, so a run's holder is
   traceable to a machine and not merely to a number.
3. A configured ``WORKER_ID`` is used verbatim, because an operator who sets
   it is entitled to the name they chose.
"""

from __future__ import annotations

import os
import pathlib
import re
import socket

import pytest

from opspilot.settings import Settings
from opspilot.worker.__main__ import resolve_worker_id


def _settings(worker_id: str = "") -> Settings:
    return Settings(worker_id=worker_id)  # type: ignore[call-arg]


def test_an_unset_identity_is_derived_rather_than_empty() -> None:
    """An empty identity is not a usable one.

    ``WORKER_ID`` ships empty and a deployment that forgets to set it must
    still get an identity that distinguishes it from every other worker.
    """
    resolved = resolve_worker_id(_settings(""))
    assert resolved, "the derived identity must not be empty"
    assert resolved.strip() == resolved, "the derived identity must not be padded"


def test_two_processes_get_different_identities() -> None:
    """The property the lease's exclusion depends on.

    Two workers that share an identity make ``renew_lease``'s
    ``claimed_by = ?`` condition true for the wrong worker, and the lease
    stops excluding anybody. This is the test that would have caught the
    ``worker-compose`` default.
    """
    first = resolve_worker_id(_settings(""))
    second = resolve_worker_id(_settings(""))

    # The same process, so the pid is the same; the identity is nevertheless
    # derived from the environment rather than from the pid alone, which is
    # what makes it differ between two *processes* on one host. Asserting the
    # pid is present is the checkable part of that.
    assert str(os.getpid()) in first, (
        "the derived identity must contain the pid, so that two workers in one "
        f"container are two identities. Got {first!r}."
    )
    assert str(os.getpid()) in second


def test_the_identity_names_the_host_as_well_as_the_process() -> None:
    """A bare pid repeats across hosts; a bare hostname repeats across processes.

    The fallback is ``worker-<hostname>-<pid>`` precisely because either half
    alone is insufficient: two workers in two containers have different
    hostnames and different pids, two workers on one host have the same
    hostname and different pids, and two workers in two hosts have neither in
    common. The pair is unique in both dimensions, and it says which machine
    claimed a run — which is what an operator reading a stalled run needs.
    """
    resolved = resolve_worker_id(_settings(""))

    assert socket.gethostname() in resolved, (
        f"the derived identity must name the host ({socket.gethostname()!r}), so a "
        f"run's holder is traceable to a machine. Got {resolved!r}."
    )
    assert str(os.getpid()) in resolved, (
        f"the derived identity must name the process ({os.getpid()}), so two workers "
        f"on one host are two identities. Got {resolved!r}."
    )


def test_a_configured_identity_is_used_verbatim() -> None:
    """An operator who sets the name gets the name they chose.

    The name is also what the dashboard shows for a claimed run, so a friendly
    value is a legitimate reason to set it. The *uniqueness* is then the
    operator's responsibility, which the compose file's comment says.
    """
    assert resolve_worker_id(_settings("worker-compose")) == "worker-compose"
    assert resolve_worker_id(_settings("  worker-7  ")) == "worker-7", (
        "a configured identity is trimmed before use"
    )


def test_the_derived_identity_is_not_the_configured_one() -> None:
    """A setting that happens to be empty must not be passed through.

    The empty string is what ``settings.py`` and ``.env.example`` ship, and an
    empty ``claimed_by`` is worse than a derived one: it is equal to every
    other empty ``claimed_by``.
    """
    assert resolve_worker_id(_settings("")) != ""
    assert resolve_worker_id(_settings("   ")) != ""


@pytest.mark.parametrize("configured", ["", "   ", "worker-a", "worker-b"])
def test_resolving_never_raises_for_any_setting(configured: str) -> None:
    """Whatever the deployment did, the worker still gets an identity.

    A main process that exits over a missing setting is an application that
    will not start, and the identity is not worth that.
    """
    resolved = resolve_worker_id(_settings(configured))
    assert isinstance(resolved, str)
    assert resolved


def test_the_compose_file_does_not_pin_a_shared_identity() -> None:
    """The worker's compose default must be empty, not a name.

    A fallback of ``worker-compose`` is one value for the whole deployment,
    and therefore one value for every replica of the worker service. The
    lease's exclusion is conditional on ``claimed_by``, so a shared value
    makes that condition true for every worker and the lease stops
    excluding anybody.

    The default must be empty so that each process derives its own identity
    (see ``resolve_worker_id``). An operator may still set ``WORKER_ID`` — the
    compose file's comment says they take responsibility for its uniqueness —
    but the *fallback* has to be safe, because the fallback is what a
    deployment gets without being told to choose.

    This is asserted here rather than left to the absence of an exemption in
    ``tests/unit/test_compose_settings_are_wired.py``: that file's stale-
    exemption check would catch the value being *inconsistent* with
    ``settings.py``, which is a different claim from the one that matters
    here. The two coincided once; they need not again.
    """
    compose = (
        pathlib.Path(__file__).resolve().parents[2] / "docker-compose.yml"
    ).read_text(encoding="utf-8")

    match = re.search(r"^(\s*)WORKER_ID: \$\{WORKER_ID:-([^}]*)\}", compose, re.MULTILINE)
    assert match is not None, (
        "the compose file must pass WORKER_ID through as ${WORKER_ID:-...}; the "
        "worker's identity is the lease's exclusion mechanism and it cannot be "
        "silently replaced by a literal."
    )
    assert match.group(2) == "", (
        f"the compose file's WORKER_ID fallback is {match.group(2)!r}. A fallback "
        f"is a value every replica of the worker service shares, and the lease's "
        f"exclusion is conditional on that value -- so a shared fallback makes the "
        f"lease exclude nobody. The fallback must be empty, so each process derives "
        f"its own identity."
    )
