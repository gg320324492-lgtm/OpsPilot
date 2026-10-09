"""``LOG_LEVEL`` must reach a logger, or it is a knob that turns nothing.

## Why this file exists

``settings.py`` has defined ``log_level`` with a normalising validator since the
setting was written, and ``docker-compose.yml`` passes it to both the api and the
worker. Nothing read it. There was no ``basicConfig``, no ``dictConfig`` and no
``setLevel`` anywhere under ``src/`` -- the tree relied on ``logging.lastResort``
printing ``WARNING`` and above to stderr, which meant an operator who set
``LOG_LEVEL=DEBUG`` to diagnose a broken deployment got exactly the same output
as before, and one who set ``LOG_LEVEL=WARNING`` also got exactly the same output.

``opspilot.observability.apply_log_level`` is the fix, and these tests pin the two
properties that make it one:

- **It is applied.** Each process entry point calls it with the configured level.
  A test drives ``main()`` with ``build_worker``/``create_app`` refused, so the
  call is observed *before* the process would have gone on to do anything -- the
  order matters, because a worker that refuses to start still owes its operator
  the log line explaining why.
- **It is a function, not an import.** Nothing in ``src/opspilot`` configures
  logging at module scope. ``pytest``'s ``caplog`` installs its own handler on
  the root logger, and a module that replaced or re-levelled it at import time
  would break every test asserting on a log line. The default target is the root
  logger, so these tests pass a throwaway logger unless they are specifically
  asserting the root behaviour, and restore whatever they touch.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from typing import NoReturn

import pytest

from opspilot.observability import UnknownLogLevel, apply_log_level

# A logger that exists only for this file. Configuring a real one would leak the
# level (and any handler) into whatever test runs next.
_PROBE = "opspilot.test.log_level_probe"


class _Recorder:
    """Stands in for ``apply_log_level`` and records the level it was handed."""

    def __init__(self) -> None:
        self.applied: list[str] = []

    def __call__(self, level: str) -> int:
        self.applied.append(level)
        return logging.INFO


@pytest.fixture
def probe() -> Iterator[logging.Logger]:
    """A throwaway logger, restored to pristine state afterwards.

    Both the level and the handler list are restored: a logger left at ``DEBUG``
    with a handler attached would make every later test in the session emit to a
    stream nobody reads, and a leaked handler survives a ``caplog`` fixture.
    """
    logger = logging.getLogger(_PROBE)
    saved_level = logger.level
    saved_handlers = list(logger.handlers)
    saved_propagate = logger.propagate
    try:
        yield logger
    finally:
        logger.setLevel(saved_level)
        logger.handlers[:] = saved_handlers
        logger.propagate = saved_propagate


# ---------------------------------------------------------------------------
# The function itself
# ---------------------------------------------------------------------------


def test_the_setting_is_applied_to_the_logger(probe: logging.Logger) -> None:
    """``LOG_LEVEL=DEBUG`` results in a logger at ``DEBUG``, by name."""
    assert apply_log_level("debug", logger=probe) == logging.DEBUG
    assert probe.level == logging.DEBUG


def test_the_level_name_is_case_insensitive(probe: logging.Logger) -> None:
    """``settings`` upper-cases the value, and so does this.

    Assumed rather than relied upon: the two normalisations are in different
    modules, and a deployment that sets the level some other way (a test, a
    scripting caller) should not need to know which one does it.
    """
    assert apply_log_level("warning", logger=probe) == logging.WARNING
    assert probe.level == logging.WARNING


def test_an_unknown_level_is_refused(probe: logging.Logger) -> None:
    """A typo is a configuration error, not a silent fall back to ``INFO``.

    The whole defect this module fixes is a setting that silently did nothing;
    answering ``LOG_LEVEL=INFOO`` with "fine, INFO it is" would be the same
    failure wearing the fix's clothes.
    """
    with pytest.raises(UnknownLogLevel, match="INFOO"):
        apply_log_level("INFOO", logger=probe)
    assert probe.level == logging.NOTSET, "a refused level must leave the logger alone"


def test_a_logger_with_no_handler_gets_one_on_stderr(probe: logging.Logger) -> None:
    """Setting a level alone is not enough -- there has to be something to emit to.

    With no handler, ``logging`` falls back to ``lastResort``: a ``WARNING``
    threshold stream handler. A logger at ``INFO`` with nothing attached would
    enable INFO records and then drop every one of them, so ``LOG_LEVEL=INFO``
    would still print nothing and the setting would remain a knob that turns
    nothing. The handler is on stderr because that is the stream a container
    runtime collects (``PYTHONUNBUFFERED=1``, ``docker compose logs``).
    """
    assert not probe.handlers, "the probe logger must start with no handler"

    apply_log_level("INFO", logger=probe)

    (handler,) = probe.handlers
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stderr
    assert handler.level == logging.NOTSET, "the logger's level is the filter, not the handler's"
    assert handler.formatter is not None, "an unformatted line names no logger"


def test_a_logger_that_already_has_a_handler_is_left_alone(probe: logging.Logger) -> None:
    """A second call must not add a second handler -- records would be emitted twice.

    The in-process case: the root logger has handlers by the time the app runs
    under uvicorn (its own ``dictConfig``) and under pytest (``caplog``). Adding
    to a logger that is already writing is how one line becomes two, and a
    doubling bug in the API's logging is a doubling bug in every log it has.
    """
    apply_log_level("INFO", logger=probe)
    apply_log_level("INFO", logger=probe)

    assert len(probe.handlers) == 1


def test_applying_a_level_twice_changes_it_in_place(probe: logging.Logger) -> None:
    """Idempotent in the sense that matters: the second call wins, nothing accumulates.

    An entry point that applied the level on every call -- a restart hook, a
    reload -- must not end up with two handlers and a level from the first call.
    """
    apply_log_level("DEBUG", logger=probe)
    apply_log_level("WARNING", logger=probe)

    assert probe.level == logging.WARNING
    assert len(probe.handlers) == 1


def test_the_default_target_is_the_root_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    """``logger=None`` configures the process root, which is what an entry point wants.

    Every ``opspilot.*`` logger is ``NOTSET`` and inherits from the root, so one
    call governs the whole application. The levels are saved and restored: the
    root logger is shared with every library in the process, and a test that left
    it at ``DEBUG`` would change what the rest of the session logs.
    """
    root = logging.getLogger()
    application = logging.getLogger("opspilot")
    saved_root, saved_application = root.level, application.level
    saved_handlers = list(root.handlers)
    try:
        assert apply_log_level("debug") == logging.DEBUG
        assert root.level == logging.DEBUG
        # The application's own namespace is set directly rather than inherited:
        # ``alembic``'s ``fileConfig`` and ``uvicorn``'s ``dictConfig`` both
        # reconfigure the root in-process, and the first disables every logger it
        # does not name. This is what stops a framework turning our records off.
        assert application.level == logging.DEBUG
    finally:
        root.setLevel(saved_root)
        application.setLevel(saved_application)
        root.handlers[:] = saved_handlers


def test_an_explicit_logger_leaves_the_root_alone(probe: logging.Logger) -> None:
    """The other half of the parameter: a scoped call must not reach the process root.

    Without this, the parameter would only be half-defined -- it would let a
    caller aim at a subtree and quietly configure the whole process anyway. The
    probe fixture's own logger is the subtree, so it is also cleaned up.
    """
    root = logging.getLogger()
    application = logging.getLogger("opspilot")
    saved_root, saved_application = root.level, application.level
    try:
        apply_log_level("DEBUG", logger=probe)
        assert probe.level == logging.DEBUG
        assert root.level == saved_root
        assert application.level == saved_application
    finally:
        root.setLevel(saved_root)
        application.setLevel(saved_application)


# ---------------------------------------------------------------------------
# The entry points actually call it
# ---------------------------------------------------------------------------


def _refusing_build_worker() -> NoReturn:
    """Stand in for ``build_worker``, failing the way a misconfigured one does.

    It has to raise rather than return, because the point of the test is that the
    level is applied *before* anything else happens -- a worker that cannot build
    still configures logging, because the refusal it is about to print is a log
    line like any other.
    """
    from opspilot.worker.__main__ import WorkerConfigError

    raise WorkerConfigError("no database", how_to_fix="set DATABASE_URL")


def test_the_worker_entry_point_applies_the_configured_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``opspilot-worker`` applies ``LOG_LEVEL`` before it builds anything.

    The call being deleted is exactly the defect this module fixes, and nothing
    else would notice: the worker would boot, run and log exactly as it did
    before. So the ordering is asserted directly -- ``apply_log_level`` records
    the level it was handed, ``build_worker`` refuses, and the assertion is that
    the level was recorded *anyway*.
    """
    from opspilot.worker import __main__ as worker_entry

    recorder = _Recorder()
    monkeypatch.setattr(worker_entry, "apply_log_level", recorder)
    monkeypatch.setattr(worker_entry, "build_worker", _refusing_build_worker)
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.delenv("OPSPILOT_OPERATOR_TOKEN", raising=False)
    from opspilot.settings import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as refused:
            worker_entry.main()
        assert refused.value.code == 1
    finally:
        get_settings.cache_clear()

    assert recorder.applied == ["DEBUG"], (
        "the worker entry point did not apply LOG_LEVEL; a deployment that sets it "
        f"would get the default verbosity (applied: {recorder.applied})"
    )


def test_the_api_entry_point_applies_the_configured_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``opspilot-api`` applies ``LOG_LEVEL`` before the app is built.

    Same argument as the worker's, and it has to be a separate test: the two entry
    points are two processes with two composition roots, and one of them keeping
    the call while the other dropped it would leave a deployment where the worker
    honours the setting and the API does not.
    """
    from opspilot.api import __main__ as api_entry
    from opspilot.settings import get_settings

    recorder = _Recorder()

    def _refuse() -> NoReturn:
        raise RuntimeError("stop before uvicorn binds a socket")

    monkeypatch.setattr(api_entry, "apply_log_level", recorder)
    monkeypatch.setattr(api_entry, "create_app", _refuse)
    monkeypatch.setenv("LOG_LEVEL", "warning")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="stop before uvicorn"):
            api_entry.main()
    finally:
        get_settings.cache_clear()

    assert recorder.applied == ["WARNING"], (
        "the api entry point did not apply LOG_LEVEL; a deployment that sets it "
        f"would get the default verbosity (applied: {recorder.applied})"
    )


def test_no_module_configures_logging_at_import_time() -> None:
    """Nothing under ``src/opspilot`` calls ``basicConfig``/``dictConfig`` at module scope.

    The static half of the guarantee, and the reason the two tests above are
    behavioural: an import-time configuration in a library module would break
    ``caplog`` for the whole suite, so the rule is stated as a scan rather than
    trusted to the entry points being the only callers. ``fileConfig`` is named
    too -- ``migrations/env.py`` uses it, which is the hazard
    ``test_the_runtime_logger_survives_an_in_process_migration`` pins.
    """
    import ast
    import pathlib

    source_root = pathlib.Path(__file__).resolve().parents[2] / "src" / "opspilot"
    scanned = sorted(source_root.rglob("*.py"))
    assert scanned, "the scan found no modules; the guard below inspected nothing"
    configured_at_module_scope: list[str] = []
    for path in scanned:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in {"basicConfig", "dictConfig", "fileConfig"}:
                configured_at_module_scope.append(f"{path.relative_to(source_root.parent.parent)}")

    assert not configured_at_module_scope, (
        "these modules configure logging at import time, which dictates policy to "
        "whatever imports them (pytest's caplog, another process, a REPL): "
        f"{configured_at_module_scope}"
    )
