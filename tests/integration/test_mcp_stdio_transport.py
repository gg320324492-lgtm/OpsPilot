"""The stdio transport: the ``MCP_*_COMMAND`` settings must actually be read.

## Why this file exists

``MCP_CRM_COMMAND``, ``MCP_BILLING_COMMAND`` and ``MCP_ISSUES_COMMAND`` were
declared in ``settings.py`` and documented in ``.env.example`` from M2, and no
code read them. ``grep -rn mcp_crm_command src/`` returned the declaration and
nothing else. ``docs/mcp-contracts.md`` then described the servers as "sibling
processes in Compose", which the code also did not do.

A setting that changes nothing is worse than no setting: it is a claim in an
operator-facing file that an operator will reasonably act on. This file is the
guard against that class of regression, and it deliberately tests the
*production* wiring rather than a gateway constructed directly in a test -- a
gateway built in a test with a hand-written factory would prove nothing about
whether the deployment reads its configuration.

## What is asserted, and what is deliberately not

**Parsed without a shell** is a unit assertion on :func:`parse_command`. It is
the assertion that would fail if someone "simplified" the spawn to
``subprocess(shell=True)``, which in this path is a command-injection hole: a
``.env`` value must be able to name a server and nothing else.

**The spawn failure is named** is a unit assertion too, and offline: it does not
start a process to prove a process cannot start.

**A live call over stdio** is the one test here that spawns anything. It is
hermetic (its own ``OPSPILOT_MCP_DATA_DIR`` in ``tmp_path``), read-only
(``crm.get_customer`` is a pure read), and bounded by a timeout, because a
transport that cannot spawn is exactly the failure this file exists to catch and
a green suite over a dead path would be worse than a red one. It is the only
test in the repository that proves the gateway can reach a real MCP server over
a real pipe rather than in this process.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from mcp.types import CallToolResult

from opspilot.adapters.tools.mcp_gateway import (
    MCPToolGateway,
    build_stdio_servers_from_settings,
)
from opspilot.adapters.tools.mcp_stdio import (
    MCPServerSpawnError,
    StdioServerProcess,
    parse_command,
)
from opspilot.ports.tool_gateway import ToolResult
from opspilot.settings import Settings, default_mcp_server_command

REPO_ROOT = Path(__file__).resolve().parents[2]


def _payload(result: ToolResult) -> dict[str, Any]:
    """The result payload as a typed dict.

    ``ToolResult.result`` is declared ``dict[str, object]``, so every subscript is
    an ``object`` to a type checker and every assertion on the value below it is
    an error. The alternative -- asserting with ``cast`` at each of a dozen use
    sites -- says nothing; this says once, at the boundary, that these tests are
    reading a structured payload the servers produced.
    """
    assert result.result is not None, f"expected a payload, got {result.error!r}"
    payload: dict[str, Any] = result.result
    return payload


# ---------------------------------------------------------------------------
# Parsing: a command string becomes an argv, and never a shell line
# ---------------------------------------------------------------------------


def test_parse_command_splits_a_command_into_argv() -> None:
    """The default command splits into an interpreter and its two arguments."""
    executable, args = parse_command("python -m mcp_servers.crm.server")

    assert executable == "python"
    assert args == ["-m", "mcp_servers.crm.server"]


def test_parse_command_supports_arguments() -> None:
    """``--reset`` reaches the child as its own argument.

    The servers take ``--reset`` (``mcp_servers/crm/server.py::main``), which
    restores their store from the committed seed before serving. A parser that
    could only produce ``[executable, module]`` would silently drop it, so this
    is asserted rather than assumed.
    """
    executable, args = parse_command("python -m mcp_servers.crm.server --reset")

    assert executable == "python"
    assert args == ["-m", "mcp_servers.crm.server", "--reset"]


def test_parse_command_handles_a_quoted_path_containing_spaces() -> None:
    """A Windows interpreter path is one token, quotes stripped.

    ``"C:\\Program Files\\Python\\312\\python.exe" -m x`` is the shape an operator
    writes when their interpreter is not on ``PATH``. The backslashes must
    survive too: under posix shlex rules a backslash is an escape character, and
    without disabling that, ``C:\\Python\\python.exe`` becomes ``C:Pythonpython.exe``.
    """
    executable, args = parse_command('"C:\\Program Files\\Python\\312\\python.exe" -m x')

    assert executable == "C:\\Program Files\\Python\\312\\python.exe"
    assert args == ["-m", "x"]


def test_parse_command_leaves_an_unquoted_backslash_path_intact() -> None:
    """An unquoted Windows path is not mangled by escape processing."""
    executable, _ = parse_command("C:\\Python312\\python.exe -m x")

    assert executable == "C:\\Python312\\python.exe"


@pytest.mark.parametrize(
    ("spec", "expected_args"),
    [
        pytest.param("python -m x; rm -rf /", ["-m", "x;", "rm", "-rf", "/"], id="semicolon"),
        pytest.param("python -m x && whoami", ["-m", "x", "&&", "whoami"], id="and"),
        pytest.param("python -m x | cat", ["-m", "x", "|", "cat"], id="pipe"),
        pytest.param("python -m $(whoami)", ["-m", "$(whoami)"], id="command_substitution"),
        pytest.param("python -m `id`", ["-m", "`id`"], id="backticks"),
    ],
)
def test_parse_command_does_not_interpret_shell_metacharacters(
    spec: str, expected_args: list[str]
) -> None:
    """Shell syntax is data, never structure.

    This is the assertion that fails if the spawn is ever "simplified" to
    ``shell=True``. Under a shell, ``python -m x; rm -rf /`` runs *two* commands
    and the second is invisible in the setting; parsed as argv it is one process
    whose remaining arguments are literal text, which the server rejects as
    unrecognised arguments. The failure is loud and the operator sees their own
    string -- which is the entire reason there is no shell here.

    The expected argv is written out per case rather than derived from the input,
    so the test states exactly what a shell would *not* have produced.
    """
    executable, args = parse_command(spec)

    assert executable == "python"
    assert args == expected_args
    # The equality is the whole assertion: ``$(whoami)`` and `` `id` `` reach the
    # child as those literal strings, not as the output of a command.


def test_parse_command_rejects_an_empty_value() -> None:
    """An empty setting is a configuration error, not an empty argv.

    An empty string would otherwise become ``command=""`` and surface as a bare
    ``FileNotFoundError`` from the spawn, naming nothing.
    """
    with pytest.raises(MCPServerSpawnError, match="empty"):
        parse_command("   ")


def test_parse_command_rejects_unbalanced_quotes() -> None:
    """A quote that never closes is reported, not swallowed."""
    with pytest.raises(MCPServerSpawnError, match="not a valid command line"):
        parse_command('python -m "unterminated')


def test_the_default_command_uses_this_interpreter(tmp_path: Path) -> None:
    """The default is ``sys.executable``, not a bare ``python``.

    A bare ``python`` is whatever is first on ``PATH``, which in a virtualenv
    checkout is frequently the *system* interpreter -- whose site-packages has no
    ``mcp``. The server then dies with ``ModuleNotFoundError: No module named
    'mcp'``, which reads like a broken install and is not one.
    """
    settings = Settings(MCP_TRANSPORT="stdio")

    assert settings.mcp_crm_command == default_mcp_server_command("mcp_servers.crm.server")
    executable, args = parse_command(settings.mcp_crm_command)
    assert executable == sys.executable
    assert args == ["-m", "mcp_servers.crm.server"]


# ---------------------------------------------------------------------------
# A spawn failure is named, and surfaces as a result
# ---------------------------------------------------------------------------


async def test_a_missing_module_is_reported_by_name() -> None:
    """A bad command says which command, and what the server said.

    Asserted on the message rather than merely on "it raised", because the whole
    point is the diagnosis. Left unhandled, the SDK surfaces a missing module as
    ``ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)`` from
    three frames below the gateway -- identical for a missing module, a bad flag
    and a server that crashed on startup, and naming neither the server nor the
    command. The child's own stderr is what distinguishes them.
    """
    server = StdioServerProcess("crm", f'"{sys.executable}" -m mcp_servers.nope.server')

    with pytest.raises(MCPServerSpawnError) as caught:
        await server.call_tool("crm.get_customer", {"customer_id": "CUS-1001"})

    message = str(caught.value)
    assert "'crm'" in message
    assert "mcp_servers.nope.server" in message
    # The child's stderr is quoted back, which is the part that says *why*.
    assert "No module named" in message


async def test_a_missing_interpreter_is_reported_by_name() -> None:
    """An executable that does not exist names the command that named it."""
    server = StdioServerProcess("billing", "definitely-not-a-real-interpreter -m billing")

    with pytest.raises(MCPServerSpawnError) as caught:
        await server.call_tool("billing.get_invoice", {"invoice_id": "INV-2026-384"})

    assert "'billing'" in str(caught.value)
    assert "definitely-not-a-real-interpreter" in str(caught.value)


async def test_a_spawn_failure_becomes_a_result_not_an_exception() -> None:
    """Through the gateway, a dead server is ``mcp_unavailable``.

    ``docs/mcp-contracts.md`` §5 and the runtime's ``FAILED(mcp_unavailable)``
    mapping depend on this: an exception escaping ``call_tool`` would propagate
    out of ``_gate_and_execute`` as a transport error, and the runtime is written
    to branch on the result's error code rather than on an exception type.
    """
    settings = Settings(
        MCP_TRANSPORT="stdio",
        MCP_CRM_COMMAND=f'"{sys.executable}" -m mcp_servers.nope.server',
    )
    gateway = MCPToolGateway(server_factory=lambda: build_stdio_servers_from_settings(settings))

    result = await gateway.call_tool("crm.get_customer", {"customer_id": "CUS-1001"})

    assert result.ok is False
    assert result.error == "mcp_unavailable"
    assert "mcp_servers.nope.server" in str(result.result)
    await gateway.aclose()


async def test_a_failed_spawn_does_not_hang_the_next_call() -> None:
    """A second call after a failed spawn returns the same named error.

    The failure mode this guards is a *hang*: a caller that queued a call behind
    a server that never came up waits on a future nobody will ever complete. That
    is the one behaviour that would make a stdio deployment worse than no stdio
    deployment at all, so it is asserted rather than assumed.
    """
    server = StdioServerProcess("crm", f'"{sys.executable}" -m mcp_servers.nope.server')

    for _ in range(2):
        with pytest.raises(MCPServerSpawnError, match="No module named"):
            await server.call_tool("crm.get_customer", {"customer_id": "CUS-1001"})

    await server.aclose()


# ---------------------------------------------------------------------------
# The setting is read on the production path
# ---------------------------------------------------------------------------


def test_build_worker_gateway_builds_in_process_by_default() -> None:
    """With ``MCP_TRANSPORT`` unset, the worker's gateway is unchanged.

    This is the assertion that the change did not alter what the suite
    exercises: the default must still be a gateway with no servers pre-built and
    no subprocess configured, which is what every other test in the repository
    depends on.
    """
    from opspilot.worker.__main__ import build_worker_gateway

    gateway = build_worker_gateway(Settings())
    assert isinstance(gateway, MCPToolGateway)

    servers = gateway._server_factory()
    # The default factory builds in-process servers: it must not read the
    # MCP_*_COMMAND settings at all.
    servers = gateway._server_factory()
    assert set(servers) == {"crm", "billing", "issues"}
    assert not any(isinstance(s, StdioServerProcess) for s in servers.values())


def test_build_worker_gateway_selects_stdio_from_the_settings() -> None:
    """``MCP_TRANSPORT=stdio`` makes the worker's gateway read the commands.

    This is the production-path assertion. It calls ``build_worker_gateway`` --
    the function ``build_worker`` calls -- rather than constructing a gateway in
    the test, because a gateway built here with a hand-written factory would pass
    even if nothing in the deployment ever read the setting.
    """
    from opspilot.worker.__main__ import build_worker_gateway

    settings = Settings(MCP_TRANSPORT="stdio")
    gateway = build_worker_gateway(settings)
    concrete = cast(MCPToolGateway, gateway)
    servers = concrete._server_factory()

    assert set(servers) == {"crm", "billing", "issues"}
    for name, server in servers.items():
        assert isinstance(server, StdioServerProcess)
        assert server.name == name
        assert server._spec == getattr(settings, f"mcp_{name}_command")


def test_a_blank_command_falls_back_to_the_default() -> None:
    """An empty ``MCP_CRM_COMMAND=`` means "do the obvious thing".

    ``.env.example`` ships all three as empty, because an operator with no
    opinion writes an empty line. ``pydantic-settings`` substitutes the field
    default only for a variable that is *absent*; a variable present and blank
    arrives as ``""``, and passing that to the spawn would turn a deliberate
    blank into ``MCPServerSpawnError: the value is empty``. So blank is resolved
    here, to the same rule ``retrieval_min_score`` and ``worker_id`` follow.
    """
    settings = Settings(MCP_CRM_COMMAND="")
    servers = build_stdio_servers_from_settings(settings)

    assert servers["crm"]._spec == default_mcp_server_command("mcp_servers.crm.server")


def test_an_explicit_command_is_used_verbatim() -> None:
    """A configured command is what gets spawned -- the point of the setting."""
    settings = Settings(MCP_CRM_COMMAND="some-other-python -m some.other.server --reset")
    servers = build_stdio_servers_from_settings(settings)

    assert servers["crm"]._spec == "some-other-python -m some.other.server --reset"
    assert servers["crm"]._command == "some-other-python"
    assert servers["crm"]._args == ["-m", "some.other.server", "--reset"]


def test_an_unknown_transport_is_refused_when_settings_are_parsed() -> None:
    """An unrecognised ``MCP_TRANSPORT`` cannot become a ``Settings`` at all.

    The ``Literal`` on the field rejects it during validation, which is the
    better place than a dispatch fall-through: the process refuses to start
    rather than starting with a transport it cannot honour. This is why
    ``build_worker_gateway``'s own raise is unreachable today -- it is the guard
    for the day a third transport is added to the ``Literal`` and this dispatch
    is forgotten, exactly as ``build_worker_provider``'s is for its provider
    names, and it is tested by driving the dispatch directly below.
    """
    import pydantic

    # Bypassing the Literal is the point: the value is invalid *on purpose*, and
    # that is exactly what the field is supposed to reject.
    bad: Any = "carrier-pigeon"
    with pytest.raises(pydantic.ValidationError, match="MCP_TRANSPORT"):
        Settings(MCP_TRANSPORT=bad)


def test_build_worker_gateway_rejects_a_transport_it_has_no_branch_for() -> None:
    """The dispatch's own guard fires when the validation cannot catch it.

    Reached by bypassing the ``Literal`` rather than by constructing a valid
    ``Settings``, which is the only way to exercise a fall-through that exists to
    stop a future edit from shipping a worker that starts and cannot reach its
    tools.
    """
    from opspilot.worker.__main__ import WorkerConfigError, build_worker_gateway

    settings = Settings()
    bad: Any = "carrier-pigeon"
    object.__setattr__(settings, "mcp_transport", bad)

    with pytest.raises(WorkerConfigError, match="MCP_TRANSPORT"):
        build_worker_gateway(settings)


# ---------------------------------------------------------------------------
# The live path: a real subprocess, a real pipe
# ---------------------------------------------------------------------------


@pytest.fixture
def stdio_crm_gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MCPToolGateway:
    """A gateway whose ``crm`` server is a real child process.

    ``OPSPILOT_MCP_DATA_DIR`` points at ``tmp_path`` so the server's store cannot
    touch the repository or leak into another test. ``crm`` is the server chosen
    because it is read-only: this test must not be able to mutate anything, so
    that a failure here is a transport failure and never a data one.
    """
    monkeypatch.setenv("OPSPILOT_MCP_DATA_DIR", str(tmp_path))
    settings = Settings(MCP_TRANSPORT="stdio")
    return MCPToolGateway(server_factory=lambda: build_stdio_servers_from_settings(settings))


def _crm_server_pids() -> set[int]:
    """PIDs of every live process whose command line names the ``crm`` server.

    Used to prove the gateway really spawned a child and really reaped it. An
    assertion about the gateway's internals -- that ``_task`` is set, that a
    handle object exists -- would pass just as well if the spawn quietly never
    happened. Counting operating-system processes is the only way to check the
    claim the documentation actually makes.

    Filtered to this process's *descendants*, so a sibling test's server -- which
    may still be shutting down -- cannot be mistaken for this one's. Without the
    filter, tests that assert on counts interfere with each other through
    scheduling rather than through anything they are testing.

    Synchronous, not async: it is a test assertion helper that runs once or
    twice per test, it blocks for a few hundred milliseconds, and making it async
    would only mean adding an ``await`` that awaits nothing. ASYNC221's concern
    -- a blocking call inside a coroutine -- is real but bounded here, and the
    alternative (threading it) would add a thread to a helper that reads a
    process list.

    Enumerated through the WMI command line rather than ``psutil``, which is not
    a dependency of this project. On a platform with no CIM the test skips rather
    than passing vacuously: a green assertion that never looked is worse than no
    assertion.
    """
    import subprocess

    # $PID inside `powershell -Command` is *PowerShell's* PID, not the test
    # process's, so this process's id is passed in as a literal. Using
    # $PID here would silently filter on the wrong parent and find no servers
    # ever -- a check that always reports "nothing spawned".
    parent = str(os.getpid())
    script = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.CommandLine -like '*mcp_servers.crm.server*' -and "
        f"$_.ParentProcessId -eq {parent} }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        # S603: the only input here is `parent`, which is `str(os.getpid())` --
        # this process's own numeric id, formatted above and derived from
        # nothing a test or caller supplied.
        # S607: `"powershell"` is a fixed argv element rather than a PATH
        # lookup, so no executable is resolved from user input.
        result = subprocess.run(  # noqa: S603
            ["powershell", "-NoProfile", "-Command", script],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - non-Windows
        pytest.skip("process enumeration by command line is unavailable here")

    if result.returncode != 0:  # pragma: no cover - non-Windows
        pytest.skip("process enumeration by command line is unavailable here")

    return {int(line) for line in result.stdout.split() if line.isdigit()}


async def test_a_read_only_tool_is_served_by_a_real_subprocess(
    stdio_crm_gateway: MCPToolGateway,
) -> None:
    """The whole point: ``crm.get_customer`` answered over a real stdio pipe.

    ``crm.get_customer`` is chosen deliberately -- it is ``READ``, it has no
    side effects, and it returns a value the committed seed pins exactly, so the
    assertion is about the transport having worked and not about the data.
    """
    before = _crm_server_pids()

    result = await stdio_crm_gateway.call_tool("crm.get_customer", {"customer_id": "CUS-1001"})

    assert result.ok is True, f"the stdio call failed: {result.result}"
    assert result.error is None
    payload = _payload(result)
    customer = payload["customer"]
    assert customer is not None
    assert customer["customer_id"] == "CUS-1001"
    assert customer["company"] == "ACME"
    assert customer["plan"] == "enterprise"

    during = _crm_server_pids()
    assert during - before, (
        "no crm server process appeared while the call was in flight, so this "
        "did not actually go over stdio -- the gateway served it in-process and "
        "this test proved nothing about the transport"
    )


async def test_one_server_is_spawned_once_and_reused(
    stdio_crm_gateway: MCPToolGateway,
) -> None:
    """Two calls share one child, not one child each.

    A gateway that respawned per call would be correct and useless: every tool
    call would pay an interpreter boot, and the mutation state a subprocess owns
    would not survive between the calls in a single run.

    Asserted on the *count* of live servers rather than on PID identity: earlier
    tests in this file leave servers that exit at their own pace, so a specific
    PID is not stable, but "a second call started one more process" is.
    """
    before = _crm_server_pids()

    first = await stdio_crm_gateway.call_tool("crm.get_customer", {"customer_id": "CUS-1001"})
    after_first = _crm_server_pids()

    second = await stdio_crm_gateway.call_tool("crm.get_customer", {"customer_id": "CUS-1002"})
    after_second = _crm_server_pids()

    assert first.ok is True and second.ok is True
    assert len(after_first) == len(before) + 1, (
        "the first call did not start exactly one crm server"
    )
    assert len(after_second) == len(after_first), (
        "a second call spawned another crm server; the spawn must happen once "
        "per gateway, not once per tool call"
    )
    assert _payload(second)["customer"]["customer_id"] == "CUS-1002"


async def test_closing_the_gateway_reaps_the_child_process(
    stdio_crm_gateway: MCPToolGateway,
) -> None:
    """``aclose`` leaves no server running.

    A stdio deployment that leaked a child per run would exhaust a container long
    before anyone read the logs. This is the assertion that shutdown is real, and
    it is checked at the operating-system level because that is the level at
    which the leak would matter.

    Counted rather than matched on PIDs, for the reason the sibling test gives:
    other tests' servers come and go, so identity is unstable and count is not.
    """
    before = _crm_server_pids()
    assert (await stdio_crm_gateway.call_tool("crm.get_customer", {"customer_id": "CUS-1001"})).ok
    during = _crm_server_pids()
    assert len(during) == len(before) + 1

    await stdio_crm_gateway.aclose()

    assert len(_crm_server_pids()) == len(before), (
        "the gateway was closed but its crm server is still running"
    )


async def test_closing_a_gateway_that_never_spawned_anything_is_safe() -> None:
    """Closing before any call is a no-op, not an error.

    A worker whose gateway was never dispatched to -- every run refused before
    reaching a tool -- must still shut down cleanly.
    """
    settings = Settings(MCP_TRANSPORT="stdio")
    gateway = MCPToolGateway(server_factory=lambda: build_stdio_servers_from_settings(settings))

    await gateway.aclose()
    await gateway.aclose()  # idempotent


async def test_a_call_after_close_fails_rather_than_hanging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A call arriving after shutdown gets an error, not silence.

    The failure this guards is the one that would make a stdio deployment worse
    than none: the owner task has exited, so nothing will ever read the queue,
    and a caller awaiting its future would block for the life of the worker.
    Both a call issued after ``aclose`` and one that was already queued when the
    stop sentinel went past are covered -- the sentinel is a stop *order*, not a
    promise to answer what is behind it.
    """
    monkeypatch.setenv("OPSPILOT_MCP_DATA_DIR", str(tmp_path))
    server = StdioServerProcess("crm", f'"{sys.executable}" -m mcp_servers.crm.server')
    served = await server.call_tool("crm.get_customer", {"customer_id": "CUS-1001"})
    assert isinstance(served, CallToolResult)
    assert served.is_error is False

    await server.aclose()

    with pytest.raises(Exception, match="not available"):
        await server.call_tool("crm.get_customer", {"customer_id": "CUS-1001"})


async def test_a_structured_refusal_survives_the_wire(
    stdio_crm_gateway: MCPToolGateway,
) -> None:
    """A tool's own ``validation_error`` arrives as a *result*, not a fault.

    ``docs/mcp-contracts.md`` §5 rule 2, and the reason the "already refunded ->
    do not refund again" scenario works: the agent must be able to tell "you
    asked badly" from "the server is unreachable". Over a wire an exception would
    be flattened into a transport error and that distinction lost -- so this
    asserts the code survives serialisation.

    Asserted in the same shape the in-process transport produces, which is the
    property that matters: switching ``MCP_TRANSPORT`` must not change what the
    agent sees. The ``code`` lives under ``result["error"]["code"]`` rather than
    at the top level because the server's own result model is what carries it
    (the model is the contract; see ``docs/mcp-contracts.md`` §1), and the
    in-process gateway reads the identical payload.
    """
    result = await stdio_crm_gateway.call_tool("crm.get_customer", {})

    assert result.ok is True, "a tool's own refusal is a successful call that reports a refusal"
    payload = _payload(result)
    assert payload["error"]["code"] == "validation_error"
    assert payload["customer"] is None


async def test_the_stdio_path_runs_the_same_gateway_contract(
    stdio_crm_gateway: MCPToolGateway,
) -> None:
    """Tools the registry sends to ``crm`` resolve; unknown tools are refused.

    The three assertions ``tests/integration/test_mcp_gateway.py`` makes about
    the in-process transport, re-made here so that switching transport cannot
    quietly change dispatch. In particular ``knowledge.search`` must still be
    refused: it is ``server="internal"`` and is not an MCP server at all, so a
    stdio server appearing for it would be a new bug rather than a new capability.
    """
    specs = await stdio_crm_gateway.list_tools()
    assert "crm.get_customer" in {spec.name for spec in specs}

    unknown = await stdio_crm_gateway.call_tool("no.such.tool", {})
    assert unknown.ok is False
    assert unknown.error == "unknown_tool"

    internal = await stdio_crm_gateway.call_tool("knowledge.search", {"query": "refund policy"})
    assert internal.ok is False
    assert internal.error == "server_unavailable"


async def test_a_reset_argument_reaches_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--reset`` in the setting is passed through to the server.

    The servers use it to restore their store from the committed seed, which is
    how the demo is made reproducible. If the argument were dropped in parsing,
    this would still pass on a fresh ``tmp_path`` and fail on a second run -- so
    it is asserted directly against what the handle will spawn.
    """
    monkeypatch.setenv("OPSPILOT_MCP_DATA_DIR", str(tmp_path))
    server = StdioServerProcess("crm", f'"{sys.executable}" -m mcp_servers.crm.server --reset')

    assert server._args == ["-m", "mcp_servers.crm.server", "--reset"]

    await server.aclose()


def test_the_working_directory_is_the_one_holding_mcp_servers() -> None:
    """A spawned server runs where ``mcp_servers`` is importable.

    ``python -m mcp_servers.crm.server`` resolves that package relative to the
    working directory, and it is not importable from anywhere else -- the servers
    import ``mcp_servers._store`` by that name. Deriving it from ``opspilot``'s
    location rather than from ``os.getcwd()`` is what makes a console script
    started from an arbitrary directory work at all.
    """
    from opspilot.adapters.tools.mcp_stdio import server_working_directory

    cwd = server_working_directory()

    assert (cwd / "mcp_servers" / "crm" / "server.py").is_file()


async def test_only_the_data_dir_is_forwarded_to_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server subprocess does not inherit the parent's environment.

    The SDK's default child environment is already minimal, and this keeps it
    that way by forwarding exactly one variable. Passing the whole environment
    would hand the operator token, the database URL and any model-provider key
    to a process whose only job is to read a JSON file -- and the one variable
    that genuinely has to cross is ``OPSPILOT_MCP_DATA_DIR``, which is what
    points the stores at the volume Compose mounts.
    """
    from opspilot.adapters.tools.mcp_stdio import _child_environment

    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", "a-secret-that-must-not-be-forwarded")
    monkeypatch.setenv("OPSPILOT_MCP_DATA_DIR", str(tmp_path))

    forwarded = _child_environment()

    assert forwarded == {"OPSPILOT_MCP_DATA_DIR": str(tmp_path)}
    assert "OPSPILOT_OPERATOR_TOKEN" not in forwarded


def test_no_environment_is_forwarded_when_the_data_dir_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset means empty, which lets the SDK use its own minimal default."""
    from opspilot.adapters.tools.mcp_stdio import _child_environment

    monkeypatch.delenv("OPSPILOT_MCP_DATA_DIR", raising=False)

    assert _child_environment() == {}


async def test_a_server_that_dies_mid_run_is_a_result_not_a_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server dying after serving is ``mcp_unavailable``, never an exception.

    The gateway's contract is that ``call_tool`` returns a ``ToolResult``; a
    closed pipe that escaped as ``BrokenPipeError`` or ``MCPError`` would violate
    it and would reach ``agents/runtime.py`` as a raised exception instead of the
    ``mcp_unavailable`` code that maps to ``FAILED``.

    The child is killed rather than allowed to crash on its own, because it is
    the same condition and this is deterministic. What is asserted is the code
    and the fact that the call returned at all -- not the wording of the message,
    which is a diagnostic rather than a contract.
    """
    monkeypatch.setenv("OPSPILOT_MCP_DATA_DIR", str(tmp_path))
    settings = Settings(MCP_TRANSPORT="stdio")
    gateway = MCPToolGateway(server_factory=lambda: build_stdio_servers_from_settings(settings))

    assert (await gateway.call_tool("crm.get_customer", {"customer_id": "CUS-1001"})).ok is True

    servers = gateway._servers
    assert servers is not None
    handle = servers["crm"]
    assert isinstance(handle, StdioServerProcess)
    assert handle._task is not None
    handle._task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await handle._task

    result = await gateway.call_tool("crm.get_customer", {"customer_id": "CUS-1001"})

    assert result.ok is False
    assert result.error == "mcp_unavailable"
    assert _payload(result)["code"] == "mcp_unavailable"

    await gateway.aclose()
