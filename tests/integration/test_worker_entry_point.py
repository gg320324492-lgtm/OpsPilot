"""``opspilot-worker`` must actually start, against a real database.

## Why this file exists

`pyproject.toml` declares ``opspilot-worker = "opspilot.worker.__main__:main"``.
For the whole of M6 that ``main()`` was an M0 stub ending in
``raise NotImplementedError``, with a docstring saying the wiring "is completed
in M6". M6 shipped; nobody came back. **The shipped entry point could not run at
all**, and no test noticed, because every claim about the golden path is proven
by ``tests/agent/_golden_harness.py``, which assembles the adapters itself and
never touches the console script.

The first attempt at implementing it called ``repositories.build_stores(...)``,
a function that does not exist. A broad ``except Exception`` turned the
resulting ``AttributeError`` into "the worker could not build its stores from
DATABASE_URL ... set DATABASE_URL to a reachable database" — so a deployment
with a perfectly good database was told to go and check its database, while the
real fault was a misspelled function name three frames up.

Two defects, then, and the second is the worse one: code that does not work, and
an error message that points away from the cause.

The third, later: the entry point assembled a worker without checking that the
selected provider's **SDK** was installed. ``MODEL_PROVIDER`` is a runtime
setting and the adapters import their SDK lazily, so an image built without the
extras booted cleanly, printed ``booted``, claimed a run and died inside
`_classify` with `ModuleNotFoundError`. The claim had already been taken, so the
run was relabelled `FAILED('interrupted')` by the next boot's sweep and the
operator saw a traceback immediately above a line saying the worker had booted.
The SDK check belongs here, before the loop, where a refusal costs no run.

## What is asserted

``build_worker`` must assemble a worker from real settings and a real migrated
SQLite database. The message it raises on a genuine failure must be *true* — so
these tests also assert that a working database does **not** produce the
database message, which is the assertion that would have caught the masquerade.
The SDK guard is asserted in the same spirit: it must fire when the SDK is
genuinely unimportable, and must *not* fire when it is present or unneeded.
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

pytest.importorskip("sqlalchemy")

from opspilot.worker.__main__ import (
    WorkerConfigError,
    build_worker,
    build_worker_provider,
    resolve_worker_id,
)


@pytest.fixture
def migrated_db(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A real SQLite database at the migration head.

    Migrated rather than created from metadata: the point is to exercise the
    path a deployment takes, and ``alembic upgrade head`` is what
    ``.env.example`` tells an operator to run. A schema built from the models
    would pass even if the migrations were broken.
    """
    db_path = tmp_path / "worker.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", "worker-entry-point-test-token")

    from opspilot.settings import get_settings

    get_settings.cache_clear()

    from alembic import command
    from alembic.config import Config

    root = pathlib.Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    command.upgrade(config, "head")
    return db_path


def test_build_worker_succeeds_against_a_real_database(migrated_db: pathlib.Path) -> None:
    """The worker assembles from real settings and a migrated database.

    This is the assertion that fails against both the stub (``NotImplementedError``
    never reaches here, but ``build_worker`` did not exist either) and the
    ``build_stores`` version (``AttributeError``). It is deliberately about the
    *happy path*, because the defect was that the happy path did not work.
    """
    worker = build_worker()
    assert worker is not None
    assert worker.run_store is not None
    assert worker.ticket_store is not None
    assert worker.tool_call_store is not None
    assert worker.approval_store is not None
    assert worker.provider is not None
    assert worker.gateway is not None
    # Retrieval must be wired, not None: __main__'s own docstring says a worker
    # passing None "would still record the RETRIEVING step, but with zero hits
    # and no citations, so the run would have no Sources panel".
    assert worker.retrieval is not None, (
        "the worker built without retrieval; every run would have an empty "
        "Sources panel and the golden path's citations would never be recorded"
    )
    assert worker.citation_store is not None
    assert worker.poll_interval > 0


def test_a_working_database_never_produces_the_database_error(
    migrated_db: pathlib.Path,
) -> None:
    """No failure path may claim the database is unreachable when it is not.

    The first implementation told an operator to check ``DATABASE_URL`` while
    the real fault was a missing function. Asserted as its own test because the
    message is the thing a human acts on: a wrong one costs an afternoon, and
    this is the only place that says so.
    """
    try:
        build_worker()
    except WorkerConfigError as exc:
        pytest.fail(
            "build_worker refused a reachable, migrated database: "
            f"{exc}. If the real cause is not the database, do not name the "
            "database -- see this module's docstring."
        )


def test_an_unopenable_database_url_is_refused_at_build_time(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A URL no driver can parse is a database fault, and is named as one.

    The other half of the pair: narrowing the exception must not silence the
    case the message was written for. ``sqlite+pysqlite:///`` with a path the
    OS cannot represent fails inside ``create_engine``, which is genuinely the
    database and must still produce the message that says so.

    Note what this test does **not** claim. A SQLite URL pointing at a directory
    that does not exist does *not* fail here: SQLAlchemy builds the engine
    lazily and the failure surfaces on the first query, so ``build_worker``
    returns a worker that dies on its first claim. That is real behaviour and it
    is recorded in the module docstring rather than asserted away -- an
    eager-connect check would be a change to the persistence layer's contract,
    not a fix to this entry point.
    """
    monkeypatch.setenv("DATABASE_URL", "not-a-database-url-at-all")
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", "t")
    from opspilot.settings import get_settings

    get_settings.cache_clear()

    with pytest.raises(WorkerConfigError) as caught:
        build_worker()
    assert "DATABASE_URL" in str(caught.value), (
        "a real database fault must still name DATABASE_URL; the message exists "
        "for exactly this case"
    )


def test_a_lazy_database_fault_is_not_reported_as_a_build_fault(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SQLite path that cannot be opened fails on first query, not at build.

    Worth pinning because it is the shape an operator will actually meet, and
    because the previous test's first version asserted this case raised at build
    time. It does not -- SQLAlchemy connects lazily -- and a test asserting a
    code path that cannot run is a test of the test.

    What this buys: the failure is an ``OperationalError`` on first use, so the
    worker starts and then fails on its first claim rather than refusing to
    start. That is a real gap between this entry point and
    ``api/app.py::_build_sql_stores``, which has the same lazy behaviour, and it
    is recorded here rather than hidden behind an assertion.
    """
    import uuid

    missing = tmp_path / "no-such-dir" / "x.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{missing}")
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", "t")
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    from opspilot.settings import get_settings

    get_settings.cache_clear()

    worker = build_worker()  # builds fine -- the engine is lazy
    with pytest.raises(Exception) as caught:
        asyncio.run(worker.run_store.get(uuid.uuid4()))
    assert type(caught.value).__name__ in {"OperationalError", "ProgrammingError"}, (
        "expected the driver's own error on first use, got "
        f"{type(caught.value).__name__}: {caught.value}"
    )


def test_the_provider_is_built_from_settings(migrated_db: pathlib.Path) -> None:
    """``MODEL_PROVIDER=fake`` yields the deterministic provider, no key needed."""
    provider = build_worker_provider()
    assert provider is not None


def test_an_unknown_provider_is_refused_before_the_worker_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider this build has no adapter for fails at settings, not later.

    ``MODEL_PROVIDER`` is typed as a ``Literal``, so an unknown name is rejected
    when ``Settings`` is constructed -- before ``build_worker_provider`` is ever
    reached. The dispatch's own ``raise`` is therefore unreachable in practice,
    and this test asserts the guarantee that *is* reachable rather than the one
    that is not.

    Written after a first version of this test claimed the dispatch fires. It
    does not: pydantic rejects the name first. A test that asserts a code path
    which cannot run is a test of the test.
    """
    from pydantic import ValidationError

    from opspilot.settings import Settings

    # Built through the environment rather than as a typed argument, because
    # ``MODEL_PROVIDER`` is a ``Literal``: passing the bad name directly is a
    # type error, which mypy flags -- and it is right to. A deployment supplies
    # this as an environment variable, so this is also the real path. The
    # comment is the test: it is asserting what an *operator's typo* does.
    with pytest.raises(ValidationError) as caught, monkeypatch.context() as env:
        env.setenv("MODEL_PROVIDER", "nope-not-a-provider")
        Settings()
    assert "MODEL_PROVIDER" in str(caught.value), (
        "an unknown provider must be refused, naming the setting to fix"
    )


def test_the_worker_id_is_never_empty() -> None:
    """An empty worker id would break claiming when two workers run.

    ``Settings.worker_id`` defaults to ``""``. Two workers sharing an id is the
    case the claim query exists to keep correct, so an unset id must resolve to
    something distinct rather than to the empty string.
    """
    from opspilot.settings import Settings

    resolved = resolve_worker_id(Settings())
    assert resolved, "the worker id resolved to empty"


def test_main_runs_and_stops_cleanly(migrated_db: pathlib.Path) -> None:
    """``main()`` enters the loop and returns when asked to stop.

    The stub raised ``NotImplementedError`` before reaching any of this. Run as
    a **subprocess**, because that is the real delivery path -- ``main()`` is
    reached through the console script, in its own process, and an in-process
    call would not exercise ``asyncio.run`` or the signal handlers it installs.

    Stopped with ``SIGINT`` rather than by a private ``stop_after_seconds``
    hook: the stop mechanism a deployment actually uses is the one worth
    testing, and inventing a test-only parameter would leave the real path
    unexercised. ``SIGINT`` is delivered through ``CTRL_BREAK_EVENT`` on
    Windows, which is why this test drives the process group.
    """
    import os
    import signal
    import subprocess
    import sys
    import time as _time

    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+pysqlite:///{migrated_db}",
        "MODEL_PROVIDER": "fake",
        "EMBEDDING_PROVIDER": "local",
        "OPSPILOT_OPERATOR_TOKEN": "worker-entry-point-test-token",
        "WORKER_POLL_INTERVAL": "0.2",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "opspilot.worker"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        **(
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if sys.platform == "win32"
            else {"start_new_session": True}
        ),
    )

    try:
        # Give it long enough to either start polling or fail while building.
        _time.sleep(4.0)
        assert proc.poll() is None, "the worker exited on its own within 4s; stderr:\n" + (
            proc.stderr.read() if proc.stderr else ""
        )

        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGINT)

        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            pytest.fail(
                "the worker did not stop within 20s of SIGINT; a deployment "
                "would need SIGKILL, which is what the graceful shutdown exists "
                "to avoid"
            )
    finally:
        if proc.poll() is None:  # pragma: no cover - only on a hard failure
            proc.kill()
            proc.wait(timeout=5)

    # The assertion is about *responsiveness*, not about a particular exit code.
    #
    # On Windows, ``CTRL_BREAK_EVENT`` makes the interpreter exit with
    # ``STATUS_CONTROL_C_EXIT`` (0xC000013A, seen as 3221225786) rather than
    # with the handler's own status -- a platform detail of how the console
    # control event reaches Python, not something the worker controls. Asserting
    # ``returncode == 0`` here would fail on Windows for a worker that shut down
    # perfectly, which is the shape of assertion this project keeps having to
    # remove: one written from the implementation's assumption rather than from
    # the behaviour under test.
    #
    # What actually matters is that the process *stopped*, on its own, promptly.
    # A worker that ignored the signal is killed above and reported by the
    # timeout branch; reaching here means the shutdown worked.
    acceptable = {0}
    if sys.platform == "win32":
        acceptable.add(0xC000013A)  # STATUS_CONTROL_C_EXIT
    assert proc.returncode in acceptable, (
        f"the worker exited with {proc.returncode} "
        f"(0x{proc.returncode & 0xFFFFFFFF:08X}). Expected a clean stop; "
        "stderr:\n" + (proc.stderr.read() if proc.stderr else "")
    )


def test_the_console_script_target_is_importable_and_callable() -> None:
    """The exact target ``pyproject.toml`` names is a callable function.

    Cheap, and it is the specific thing that was wrong: the script pointed at
    ``opspilot.worker.__main__:main`` and that function raised. A packaging typo
    or a rename would fail here rather than at a user's first ``opspilot-worker``.
    """
    import importlib

    module = importlib.import_module("opspilot.worker.__main__")
    target = getattr(module, "main", None)
    assert callable(target), "pyproject.toml's opspilot-worker entry point target is not callable"


def test_importing_the_entry_point_does_not_require_a_database() -> None:
    """Import must stay cheap, so ``--help``-style failures are not masked.

    The console script imports this module at process start, before settings are
    read. If the import pulled in SQLAlchemy or built a stack, a configuration
    fault would surface as an ImportError from three frames down instead of as
    the named ``WorkerConfigError`` this module defines.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import opspilot.worker.__main__"],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "DATABASE_URL": ""},
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"importing the worker entry point failed with an empty DATABASE_URL:\n{result.stderr}"
    )


# -- a worker whose provider SDK is missing ---------------------------------


def _blocker_program(
    provider: str,
) -> str:
    """A runnable program that removes ``provider``'s SDK from the import system.

    Run in a **subprocess** because that is the only honest way to make an SDK
    absent on a machine that has it. ``tests/unit/test_layering.py`` proves no
    module imports either SDK at module scope -- this is the property that lets a
    deployment without the extra work at all -- so the module-level hook below
    can take effect before anything the project writes is imported.

    The blocker is a ``sys.meta_path`` finder that raises for the named module and
    everything under it, which is stronger than hiding one attribute: the SDK is
    genuinely not importable, so ``import anthropic`` produces the *same*
    ``ModuleNotFoundError`` a machine without the extra produces, and
    ``find_spec`` genuinely reports it absent. A test that monkeypatched the
    provider's ``_client`` would prove only that a mock is callable.
    """
    return f"""
import sys


class _Blocker:
    # A finder that makes {provider!r} and its submodules unimportable.
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".")[0]
        if root == {provider!r}:
            raise ModuleNotFoundError("No module named " + repr(fullname), name=fullname)
        return None


sys.meta_path.insert(0, _Blocker())

from opspilot.worker.__main__ import build_worker_provider, WorkerConfigError

try:
    build_worker_provider()
except WorkerConfigError as exc:
    print("REFUSED:", exc)
    raise SystemExit(0)

print("STARTED: the worker built a provider without the SDK")
raise SystemExit(1)
"""


def test_the_worker_refuses_to_start_without_its_providers_sdk(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing SDK stops the worker, before it can claim and ruin a run.

    The defect this guards: with the SDK absent and ``MODEL_PROVIDER`` naming it,
    the worker booted, printed ``booted``, polled, claimed a run, and died inside
    ``_classify`` with ``ModuleNotFoundError``. The run it had already claimed was
    then relabelled ``FAILED('interrupted')`` by the next boot's sweep -- so a
    deployment missing a package reported itself as an interrupted run and a
    healthy restart, and pointed the operator at neither.

    The assertion is that ``build_worker`` refuses, i.e. the failure happens at
    composition time where no run has been touched. It is a subprocess because the
    SDK has to be genuinely unimportable, and the development venv has it
    installed -- see :func:`_blocker_program`.

    Both real providers are exercised, because a fix that hard-codes the
    ``anthropic`` name would pass a single-provider parametrization and still ship
    the identical defect one module name over.
    """
    import subprocess
    import sys

    # S603: the command is `[sys.executable, "-c", <module-level f-string>]`.
    # `provider` is one of two literals named in this file, never caller input,
    # and the program text is the one printed in :func:`_blocker_program`.
    for provider in ("anthropic", "openai"):
        db_path = tmp_path / f"{provider}.db"
        env = {
            **__import__("os").environ,
            "DATABASE_URL": f"sqlite+pysqlite:///{db_path}",
            "MODEL_PROVIDER": provider,
            # A key the check must NOT treat as fatal -- only the SDK is checked.
            f"{provider.upper()}_API_KEY": "probe-key-not-real",
            "EMBEDDING_PROVIDER": "local",
            "OPSPILOT_OPERATOR_TOKEN": "worker-entry-point-test-token",
        }
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-c", _blocker_program(provider)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"the worker built a provider with the {provider} SDK unimportable, so it "
            "would boot and then die on its first run. stderr:\n" + result.stderr
        )
        assert "REFUSED:" in result.stdout, (
            f"the refusal did not go through WorkerConfigError for {provider}:\n"
            f"{result.stdout}\n{result.stderr}"
        )
        assert provider in result.stdout, (
            f"the refusal for {provider} did not name the provider:\n{result.stdout}"
        )


def test_a_worker_with_its_sdk_installed_still_builds(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check does not refuse a build that genuinely has the SDK.

    The negative test above would also pass if ``_require_provider_sdk`` raised
    unconditionally, which would make the guard unfalsifiable: it would forbid the
    exact configuration the guard exists to protect. So the same code path is
    asserted to succeed when the SDK *is* importable -- here, on the development
    venv, where the ``dev`` extra installs both (this test skips if not).
    """
    import subprocess
    import sys

    program = """
from opspilot.worker.__main__ import build_worker_provider

provider = build_worker_provider()
assert provider is not None
print("STARTED")
"""
    db_path = tmp_path / "sdk-present.db"
    # S603: as above -- `[sys.executable, "-c", <module-level literal>]`, no
    # caller input anywhere in the command or the program text.
    env = {
        **__import__("os").environ,
        "DATABASE_URL": f"sqlite+pysqlite:///{db_path}",
        "MODEL_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "probe-key-not-real",
        "EMBEDDING_PROVIDER": "local",
        "OPSPILOT_OPERATOR_TOKEN": "worker-entry-point-test-token",
    }
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=120,
    )
    if "ModuleNotFoundError" in result.stderr and "anthropic" in result.stderr:
        pytest.skip("the anthropic SDK is not installed in this environment")
    assert result.returncode == 0, (
        "the worker refused to build with the anthropic SDK installed; the guard "
        f"is firing on something other than absence.\nstderr:\n{result.stderr}"
    )
    assert "STARTED" in result.stdout


def test_the_fake_provider_needs_no_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """``MODEL_PROVIDER=fake`` still builds with every SDK blocked.

    This is the property ``pyproject.toml`` protects, and the reason the SDK check
    does not fire for the default provider: a fresh clone, CI and the
    golden-path demo all run ``fake`` with neither extra installed, and a worker
    that demanded an SDK to use the deterministic provider would break all three.

    Asserted against a real ``Settings`` rather than the subprocess route, because
    ``fake`` has no SDK to block -- the provider is constructed locally -- so
    there is nothing for a meta-path hook to do.
    """
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", "worker-entry-point-test-token")
    from opspilot.settings import get_settings

    get_settings.cache_clear()
    provider = build_worker_provider()
    assert provider is not None
    assert get_settings().model_provider == "fake"


# -- the fake provider's scenario -------------------------------------------


def test_the_fake_provider_replays_the_scenario_that_was_configured(
    migrated_db: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``OPSPILOT_FAKE_SCENARIO`` reaches the provider, so a real run works.

    This is the difference between a demo that works and one that works only
    inside pytest. Every fixture's ``request_hash`` is null, so the hash-matching
    path has no data -- the scenario path is the only one that can answer a call
    from a real process, and before this setting nothing outside the test harness
    passed a scenario name. A worker built from the shipped defaults claimed a
    run and watched it die at ``classifying``.
    """
    from opspilot.settings import get_settings

    monkeypatch.setenv("OPSPILOT_FAKE_SCENARIO", "duplicate_charge")
    get_settings.cache_clear()

    provider = build_worker_provider()
    assert getattr(provider, "_scenario", None) == "duplicate_charge", (
        "the configured scenario did not reach the provider; a real process "
        "cannot answer any model call without it, because no fixture records a "
        "request_hash to match"
    )


def test_an_unset_scenario_leaves_the_hash_path_in_charge(
    migrated_db: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No scenario configured means no scenario passed, not a default one.

    Defaulting to ``duplicate_charge`` would make every unconfigured deployment
    replay the refund script against whatever ticket it was given. Silent
    mis-replay is worse than the loud ``UnmatchedFixtureError`` an unmatched
    request produces -- the error names the request hash, which is how a real
    fixture gets recorded.
    """
    from opspilot.settings import get_settings

    monkeypatch.setenv("OPSPILOT_FAKE_SCENARIO", "")
    get_settings.cache_clear()

    provider = build_worker_provider()
    assert getattr(provider, "_scenario", None) is None, (
        "an unset scenario must not become a default; a worker would then replay "
        "a script the operator never asked for"
    )


def test_the_configured_scenario_actually_completes_a_classification(
    migrated_db: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scenario answers a real call -- not merely reaches the constructor.

    Asserted by calling the provider, because storing the name and using it are
    different claims. The first test above would pass against an implementation
    that set ``_scenario`` and never consulted it.
    """
    import asyncio

    from opspilot.agents.schemas import TicketClassification
    from opspilot.settings import get_settings

    monkeypatch.setenv("OPSPILOT_FAKE_SCENARIO", "duplicate_charge")
    get_settings.cache_clear()

    provider = build_worker_provider()
    response = asyncio.run(
        provider.generate_structured(
            system="classify the ticket",
            prompt="We were charged twice for invoice INV-2026-384.",
            schema=TicketClassification,
        )
    )
    assert response.value is not None, "the scenario returned nothing"
    assert response.usage.latency_ms >= 0
