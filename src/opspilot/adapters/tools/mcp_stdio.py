"""Spawning the MCP servers as real stdio subprocesses.

Responsibility: turn a *command string* -- what ``MCP_CRM_COMMAND`` and its
siblings hold -- into a live MCP server this process can call tools on, and take
that server down again when the process is finished with it.

Layer: ``adapters``. Implements the ``DispatchableServer`` protocol declared in
:mod:`opspilot.adapters.tools.mcp_gateway`, which is the same one shape the
in-process ``MCPServer`` satisfies. The gateway cannot tell the two apart, which
is the point: ``docs/architecture.md`` §8 says the transport is invisible above
this layer, and an opt-in transport that forced a second dispatch path would be
the transport leaking.

Why this exists at all
----------------------

``MCP_CRM_COMMAND``, ``MCP_BILLING_COMMAND`` and ``MCP_ISSUES_COMMAND`` were
declared in ``settings.py`` and documented in ``.env.example`` from M2, and no
code read them. A setting that changes nothing is a lie told to an operator, so
this module is the code that reads them -- or, if these were deleted, the code
that would have read them.

Opt-in, not opt-out
-------------------

The in-process path stays the default (``MCP_TRANSPORT=inprocess``), and this
path is selected by ``MCP_TRANSPORT=stdio``. Three reasons, in order of weight:

1. **It must not change what the tests exercise.** Every test in the suite
   builds the gateway in-process; flipping the default would silently replace
   the thing most of the suite is testing with a subprocess, which is exactly the
   "alters what the existing tests exercise" change that would make the suite
   pass for the wrong reason.
2. **Hermeticity.** A subprocess server owns its own on-disk store, so a test
   that issues a refund mutates a file shared with the next test unless every
   test gets a private ``OPSPILOT_MCP_DATA_DIR``.
3. **Cost.** Each subprocess start is interpreter boot plus an MCP handshake --
   tens to hundreds of milliseconds. The contract suite would pay it per test.

The reason to *want* this path is that it covers what in-process cannot: stdio
framing, the subprocess handshake, and the wire serialisation. That is a claim
about the transport, and it is only a claim if something actually does it.

Why the server lives in its own task
------------------------------------

``stdio_client`` is an anyio task group, and an anyio task group must be exited
in the task that entered it -- exiting from another one raises ``RuntimeError:
Attempted to exit cancel scope in a different task than it was entered in`` and
leaves the child process running. The gateway, meanwhile, is called from
whichever task happens to be draining a run, and ``aclose`` is called from
whichever task is shutting the process down. Neither is stable, so neither can
own the context.

So a spawned server owns a dedicated task that enters ``stdio_client`` and stays
inside it for the process's lifetime. Callers hand it a request over a queue and
await a future; the owner task performs the call and completes the future. That
is what makes "spawn once, reuse across calls, shut down cleanly" true: the
context is entered once, in one task, and exited in that same task.

One owner per server also serialises that server's calls, which is what the MCP
client session expects anyway -- a session is a single ordered stream, and
interleaving two requests on it would be a protocol bug rather than a
concurrency win. Calls to *different* servers stay concurrent, because each has
its own owner task.

Why the command is parsed and never shelled out
-----------------------------------------------

``MCP_*_COMMAND`` is a string an operator writes in a ``.env`` file, and it names
a process plus its arguments. It is split with :mod:`shlex` into an explicit
argv list and handed to the SDK, which spawns that argv directly. ``shell=True``
is not a tempting shortcut here, it is a command-injection hole: a ``.env`` file
is operator-supplied text that ends up in an argv, and a value like
``python -m server; rm -rf /`` must be a *literal argument* or a startup failure,
never a second command.

With no shell in the path, shell metacharacters are consequently inert. They are
not stripped -- that would silently accept a command that does not do what it
says -- they become ordinary argv entries, and the server rejects them as
unrecognised arguments. The failure is loud and the operator sees their own
string.

Posix mode is used on every platform, with backslash escaping switched off. The
alternative, ``posix=False``, leaves the surrounding quote characters *in* the
token (``'"C:\\Program Files\\python.exe"'``), which is not a path any ``exec``
will accept. Disabling escape handling is what keeps a Windows path intact:
under posix rules a bare ``C:\\Python\\python.exe`` has its backslashes eaten as
escape characters and becomes ``C:Pythonpython.exe``.

Only ``OPSPILOT_MCP_DATA_DIR`` is forwarded to the child
-------------------------------------------------------

The SDK's default child environment is a *minimal* one (``PATH``, ``TEMP`` and a
handful of platform variables) -- not the parent's. That is the right default
and this module keeps it, because forwarding the whole parent environment would
hand the operator token, the database URL and any model-provider API key to a
process that only ever needs to read a JSON file.

One variable has to be passed explicitly or the deployment loses data:
``OPSPILOT_MCP_DATA_DIR``. It is the only environment variable any server reads
(``mcp_servers/_store.py::default_data_dir``), and it is what points the servers'
JSON stores at the volume ``docker-compose.yml`` mounts. A child that did not
inherit it would write its store to a path under the working directory instead,
outside the volume -- and for ``billing`` that file *is* the refund-idempotency
record, so the duplicate-refund defence would be reading a different store after
a container replacement.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shlex
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO, cast

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

if TYPE_CHECKING:
    from opspilot.adapters.tools.mcp_gateway import CallToolReturn

__all__ = [
    "MCPServerSpawnError",
    "StdioServerProcess",
    "build_stdio_servers",
    "parse_command",
]

logger = logging.getLogger(__name__)

# How long a spawned server has to complete its MCP handshake before it is called
# broken. Without it, a command that starts a process which never speaks the
# protocol hangs the worker forever instead of producing the named error below --
# and a worker that hangs on a bad ``.env`` value is indistinguishable from one
# that is stuck on a run.
STARTUP_TIMEOUT_SECONDS = 30.0

# The environment variable the servers read, and the only one forwarded. See the
# module docstring: forwarding the parent environment would leak the operator
# token and the provider keys to a subprocess.
FORWARDED_ENV_VARS = ("OPSPILOT_MCP_DATA_DIR",)

# How much of a failed server's stderr is quoted back in the error. Enough to
# show ``ModuleNotFoundError: No module named 'mcp_servers.crm'``; short enough
# that the message stays readable in a terminal.
STDERR_TAIL_CHARS = 600


class MCPServerSpawnError(RuntimeError):
    """A configured ``MCP_*_COMMAND`` did not yield a working MCP server.

    Named, and raised only at *spawn* time, because the failure an operator has
    to act on is a configuration value and the diagnostic has to name the value.
    The alternative -- letting the SDK's exception propagate -- produces
    ``ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)`` from
    inside a task group three frames below the gateway, which says nothing about
    which of three servers failed or what command was run.
    """

    def __init__(self, server: str, command: str, cwd: Path, reason: str) -> None:
        super().__init__(
            f"the {_server_phrase(server)} did not start from "
            f"{_command_setting(server, command)} (working directory {cwd}): "
            f"{reason}"
        )
        self.server = server
        self.command = command


class MCPServerClosed(RuntimeError):
    """A spawned MCP server went away, or could not be started.

    Two conditions, one exception, because from the caller's side they are the
    same event: there is no longer a server to answer this call. The message
    differs, so the gateway's ``mcp_unavailable`` result carries the detail an
    operator needs to tell "your command is wrong" from "your server crashed".
    """

    def __init__(self, server: str, message: str) -> None:
        super().__init__(f"the {server!r} MCP server is not available: {message}")
        self.server = server


@dataclass(frozen=True)
class _Call:
    """One tool call handed to a server's owner task."""

    name: str
    arguments: dict[str, Any]
    future: asyncio.Future[CallToolReturn]


def parse_command(spec: str, *, server: str = "") -> tuple[str, list[str]]:
    """Split a ``MCP_*_COMMAND`` value into ``(executable, argv)``.

    The setting is a string because ``.env`` files hold strings. It is split
    here, into an explicit argv list, because that is the only form the OS can
    execute without a shell -- and no shell is involved, deliberately (see the
    module docstring).

    Arguments are supported and are the reason this is more than
    ``value.split()``: the servers take ``--reset``, so
    ``"python -m mcp_servers.crm.server --reset"`` has to reach the child as two
    separate arguments.

    Quoting follows shell conventions for quotes only: ``"C:\\Program Files\\...``
    is one token, ``'...`` is one token, and a backslash is a literal character
    rather than an escape. See the module docstring for why backslash escapes are
    switched off.

    Args:
        spec: The raw setting value.
        server: The server the value came from, used **only** to name the
            setting in an error. Optional because this function is callable on
            its own -- :class:`StdioServerProcess` is what passes it, so the
            production path names the actual ``MCP_<NAME>_COMMAND``. Left unset
            the message says the value is unparseable without claiming to know
            which server it belonged to; see :func:`_command_setting`.

    Returns:
        ``(executable, args)``, both non-empty.

    Raises:
        MCPServerSpawnError: If the value is empty or has no executable, or if
            its quotes do not balance.
    """
    lexer = shlex.shlex(spec, posix=True)
    lexer.whitespace_split = True
    # No comment character and no backslash escapes: a `#` or a `\` in a Windows
    # path is a literal, and both are common in values an operator must be able
    # to write without a shell quoting lesson.
    lexer.commenters = ""
    lexer.escape = ""
    try:
        parts = list(lexer)
    except ValueError as exc:
        raise MCPServerSpawnError(
            server, spec, Path.cwd(), f"the value is not a valid command line ({exc})"
        ) from exc

    if not parts:
        raise MCPServerSpawnError(server, spec, Path.cwd(), "the value is empty")
    return parts[0], parts[1:]


def server_working_directory() -> Path:
    """The directory a spawned server must run in.

    ``python -m mcp_servers.crm.server`` resolves ``mcp_servers`` as a package
    relative to the current working directory, and that package is not importable
    from anywhere else -- the install maps ``crm``/``billing``/``issues`` as
    top-level packages, and the servers themselves import ``mcp_servers._store``
    by that name. So the child needs the directory that holds ``mcp_servers/``,
    which is the source root in a checkout (``/app`` in the image, which is where
    the Dockerfile puts it and which is the image's ``WORKDIR``).

    Derived from ``opspilot``'s own location rather than from
    ``os.getcwd()``: a console script may be started from anywhere, and a server
    spawned from the wrong directory fails with a ``ModuleNotFoundError`` that
    reads like a broken install rather than a wrong ``.env``.
    """
    import opspilot

    return Path(opspilot.__file__).resolve().parents[2]


class StdioServerProcess:
    """One MCP server, running as a child process and owned by its own task.

    Structurally satisfies the gateway's ``DispatchableServer``: it has
    ``call_tool``. Everything else here is lifecycle.

    A handle is created synchronously and does nothing until its first
    ``call_tool``, which is what lets the gateway build its server map inside a
    synchronous factory (the seam ``server_factory`` is) while the spawn itself
    happens in the event loop that is about to use it.
    """

    def __init__(self, name: str, spec: str, *, cwd: Path | None = None) -> None:
        """Bind a handle to a command. Nothing is spawned here.

        Args:
            name: The server key (``crm``/``billing``/``issues``), used in errors.
            spec: The raw ``MCP_<NAME>_COMMAND`` value.
            cwd: Working directory for the child. Defaults to
                :func:`server_working_directory`.

        Raises:
            MCPServerSpawnError: If ``spec`` cannot be parsed. Raised eagerly
                rather than on first call so that a malformed setting fails at
                configuration time with the setting named.
        """
        self._name = name
        self._spec = spec
        self._cwd = cwd if cwd is not None else server_working_directory()
        # The name is passed on so a malformed value is reported against the
        # setting it came from (``MCP_CRM_COMMAND=``) rather than as an
        # anonymous parse failure. See ``parse_command``'s ``server`` argument.
        self._command, self._args = parse_command(spec, server=name)

        self._task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[_Call | None] = asyncio.Queue()
        self._start_lock = asyncio.Lock()
        self._stderr: TextIO | None = None
        # Completed by the owner task once the server is up, or failed with the
        # reason it is not. A handshake rather than polling ``task.done()``:
        # "the task has not finished yet" is not "the server is ready", and
        # treating the first as the second is a race that only shows up as an
        # occasional hang on a loaded machine.
        self._ready: asyncio.Future[None] | None = None
        # Set once the owner task has failed to come up, so a later call reports
        # the original fault instead of trying to spawn a second child.
        self._failure: str | None = None

    @property
    def name(self) -> str:
        return self._name

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolReturn:
        """Call a tool on the spawned server, spawning it on first use.

        Args:
            name: The fully-qualified tool name, e.g. ``crm.get_customer``.
            arguments: The tool's arguments.

        Returns:
            Whatever the server returned over the wire -- a ``CallToolResult`` for
            every OpsPilot tool.

        Raises:
            MCPServerSpawnError: If the server could not be started.
            MCPServerClosed: If the server died, or died while this call was in
                flight. Both are exceptions by design here and are converted to
                ``ToolResult(ok=False, error="mcp_unavailable")`` by the gateway,
                which owns the "errors are results" contract.
        """
        await self._ensure_started()
        call = _Call(
            name=name,
            arguments=dict(arguments),
            future=asyncio.get_running_loop().create_future(),
        )
        await self._queue.put(call)
        return await call.future

    async def aclose(self) -> None:
        """Terminate the child, if one was ever started.

        Idempotent, and safe on a handle that was never called -- the common case
        for a gateway whose tools were never dispatched. The owner task is sent a
        stop sentinel, which makes it leave its ``async with``; the SDK's shutdown
        path then closes the pipes, terminates the process and reaps it, so this
        returns only once the child is gone.

        The sentinel is sent rather than the task cancelled because cancellation
        would unwind the ``async with`` from the *cancelling* task, and an anyio
        task group must be exited by the task that entered it. That is the same
        reason the owner task exists at all.

        A call that was already queued is failed rather than abandoned: the
        sentinel is a stop *order*, not a promise to answer what is behind it, so
        anything still waiting is released with the "shut down" fault instead of
        waiting on a queue that will never be read again.
        """
        task = self._task
        if task is None or task.done():
            self._task = None
            self._fail("the server was shut down")
            self._close_stderr()
            return
        await self._queue.put(None)
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self._task = None
        self._fail("the server was shut down")
        self._close_stderr()

    # -- internals -------------------------------------------------------

    async def _ensure_started(self) -> None:
        """Start the owner task once, wait for the handshake, surface any failure.

        The lock is what makes two concurrent first-calls spawn one child rather
        than two. The handshake future is what makes every caller wait for the
        server to be genuinely ready -- and what turns a failed spawn into a
        raised error in *every* waiting caller, rather than a queue that nobody
        will ever read from and a future nobody will ever complete. A later call
        after a failed spawn reports the first failure without re-spawning,
        because re-spawning would hide a bad command behind an identical second
        error and double the startup delay on a broken deployment.

        Raises:
            MCPServerSpawnError: If the server could not be started.
            MCPServerClosed: If it started and has since been shut down. Distinct
                from the spawn failure because the diagnosis differs -- "your
                command is wrong" versus "this server is gone" -- and the gateway
                maps both to ``mcp_unavailable``, so the split is for whoever is
                reading the log, not for the caller.
        """
        async with self._start_lock:
            if self._failure is not None and self._task is None:
                # Closed or never-started-failed after a previous life. Reported
                # as closed, not as a spawn failure: nothing was spawned, so a
                # spawn error would name a command that demonstrably worked.
                raise MCPServerClosed(self._name, self._failure)
            if self._task is None:
                self._ready = asyncio.get_running_loop().create_future()
                self._task = asyncio.create_task(self._serve())
            ready = self._ready

        assert ready is not None
        await ready

        if self._failure is not None:
            if self._task.done() and self._task.cancelled():
                raise MCPServerClosed(self._name, self._failure)
            raise MCPServerSpawnError(self._name, self._spec, self._cwd, self._failure)

    async def _serve(self) -> None:
        """Own the ``stdio_client`` context for this server's whole lifetime.

        Everything that can fail before the handshake -- the spawn itself, the
        handshake, the child's immediate exit -- is recorded in ``self._failure``
        as a sentence an operator can act on, and released to whoever is waiting
        for the server. Everything after it is a normal tool result, or an
        exception on the in-flight call's future.
        """
        # A real file rather than a pipe: the SDK pumps the child's stderr into
        # it from its own task, and a file can be read back without racing a
        # reader against a writer. On POSIX ``TemporaryFile`` is unlinked at
        # creation, so the name never exists on disk at all. Its lifetime is this
        # task's, which is the lifetime of the server it describes -- hence the
        # ``with`` around everything below rather than a close in ``aclose``.
        with cast(
            TextIO,
            tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace"),
        ) as opened:
            self._stderr = opened
            params = StdioServerParameters(
                command=self._command,
                args=self._args,
                cwd=self._cwd,
                env=_child_environment(),
                encoding="utf-8",
                # A server that prints a non-UTF-8 byte on stderr must not kill
                # the reader task; the output is diagnostic text and a
                # replacement character in it is worth more than a dead
                # connection.
                encoding_error_handler="replace",
            )
            try:
                async with (
                    stdio_client(params, errlog=opened) as streams,
                    ClientSession(*streams) as session,
                ):
                    await asyncio.wait_for(session.initialize(), STARTUP_TIMEOUT_SECONDS)
                    self._mark_ready()
                    await self._pump(session)
            except asyncio.CancelledError:
                # A cancelled task is a deliberate shutdown, not a fault, so it
                # must not be recorded as one -- but anyone still waiting for the
                # server has to be released or they wait forever.
                self._fail("the server was shut down before it was ready")
                raise
            except Exception as exc:
                self._fail(exc)
            finally:
                self._mark_ready()

    async def _pump(self, session: ClientSession) -> None:
        """Serve tool calls until asked to stop or the connection breaks.

        Sequential by design: an MCP client session is one ordered stream, so
        overlapping requests on it would interleave their responses.
        """
        while True:
            call = await self._queue.get()
            if call is None:
                return
            if self._failure is not None:
                # A previous call killed the session; this one never will.
                _set_exception(call.future, MCPServerClosed(self._name, self._failure))
                continue
            try:
                result = await session.call_tool(call.name, call.arguments)
            except Exception as exc:
                self._fail(exc)
                _set_exception(call.future, MCPServerClosed(self._name, self._failure or str(exc)))
                return
            if not call.future.done():
                call.future.set_result(result)

    def _mark_ready(self) -> None:
        """Release every caller waiting on the handshake.

        Called from the owner's ``finally`` rather than only on success, so a
        spawn that fails still wakes its waiters -- that is the difference
        between a named error and a call that never returns.
        """
        if self._ready is not None and not self._ready.done():
            self._ready.set_result(None)

    def _fail(self, reason: str | BaseException) -> None:
        """Record why this server is unusable, and fail anything still queued.

        The reason prefers the child's own stderr. A missing module, a bad flag
        and a crash-on-startup all surface from the SDK as ``Connection closed``
        inside an ``ExceptionGroup`` -- identical for three completely different
        faults -- while the child's stderr says which one it was.
        """
        if self._failure is not None:
            return
        if isinstance(reason, BaseException):
            described = _describe(reason)
            detail = _stderr_tail(self._stderr)
            self._failure = f"{described}; the server said: {detail}" if detail else described
        else:
            self._failure = reason
        # A call queued behind a session that just died will never be answered by
        # the pump loop, because the pump has returned. Fail it here, so the
        # caller's ``await`` ends in a result instead of hanging.
        self._drain_queue()

    def _close_stderr(self) -> None:
        """Drop the stderr handle once its owner task has finished.

        The file itself is closed by the ``with`` block in :meth:`_serve`, which
        owns it; this only clears the reference so a later call cannot read a
        file whose task is gone. Calling ``aclose`` twice is therefore safe.
        """
        self._stderr = None

    def _drain_queue(self) -> None:
        """Fail every call already queued for a server that will not answer.

        Without this, a caller that queued a call just as the session died waits
        on a future nobody will ever complete -- which is the one failure mode
        this whole module exists to avoid, since a worker that hangs on a dead
        server is indistinguishable from one that is stuck on a run.
        """
        reason = self._failure or "the server was shut down"
        while not self._queue.empty():
            call = self._queue.get_nowait()
            if call is None:
                continue
            _set_exception(call.future, MCPServerClosed(self._name, reason))


def build_stdio_servers(commands: Mapping[str, str]) -> dict[str, StdioServerProcess]:
    """Build one lazily-started handle per configured server.

    The factory shape is the gateway's existing ``server_factory`` seam, so
    selecting stdio changes which objects the gateway dispatches to and nothing
    about how it dispatches.

    Args:
        commands: Server key to raw ``MCP_<NAME>_COMMAND`` value.

    Returns:
        A map the gateway can use in place of ``build_in_process_servers``'s.

    Raises:
        MCPServerSpawnError: If any command cannot be parsed. Parsing is eager so
            that a typo in one of the three settings is reported at startup rather
            than on whichever tool happens to be called first -- and so the
            message can name the setting, which only this function knows.
    """
    return {name: StdioServerProcess(name, spec) for name, spec in commands.items()}


# -- module helpers -------------------------------------------------------


def _server_phrase(server: str) -> str:
    """How to refer to a server in an error, given whatever name it arrived with.

    Every call site passes a real name -- ``crm``, ``billing``, ``issues`` --
    except :func:`parse_command`, which is handed only the raw setting value and
    has no idea which of the three it came from. It used to format that as
    ``the '' MCP server ... MCP__COMMAND=``, so the one failure that fires
    *before* any server name exists reported a setting called ``MCP__COMMAND``,
    which is not a setting an operator can find in ``.env.example``. An operator
    reading that would go looking for a fourth command.

    So an unnamed failure names the value it could not parse and says plainly
    that it does not know which server it belongs to, instead of inventing an
    empty name that looks like a bug.
    """
    if server:
        return f"{server!r} MCP server"
    return "MCP server command"


def _command_setting(server: str, command: str) -> str:
    """The ``MCP_<NAME>_COMMAND=<value>`` a reader needs to see to act.

    Named from the server when there is one. When :func:`parse_command` raises
    there is not -- the value is quoted on its own, which is enough to find the
    line in ``.env`` (the three values are distinguishable at a glance), and
    saying ``MCP__COMMAND=`` was strictly worse than saying nothing because it
    names a variable that does not exist.
    """
    return f"MCP_{server.upper()}_COMMAND={command!r}" if server else repr(command)


def _child_environment() -> dict[str, str]:
    """The subset of this process's environment a server subprocess needs.

    Empty when ``OPSPILOT_MCP_DATA_DIR`` is unset, which tells the SDK to use its
    own default environment -- the right thing, since the servers read nothing
    else and inheriting the parent's variables would hand a subprocess the
    operator token.
    """
    import os

    return {name: os.environ[name] for name in FORWARDED_ENV_VARS if name in os.environ}


def _describe(exc: BaseException) -> str:
    """A one-line description of a startup failure, unwrapping task groups.

    The SDK reports most spawn problems as an ``ExceptionGroup`` whose message is
    ``unhandled errors in a TaskGroup (1 sub-exception)``. That is a description
    of the SDK's plumbing, not of the fault, so the leaves are unwrapped and the
    deepest concrete one is used.
    """
    leaves: list[BaseException] = []
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
        else:
            leaves.append(current)
    text = "; ".join(f"{type(leaf).__name__}: {leaf}" for leaf in leaves if str(leaf))
    return text or type(exc).__name__


def _stderr_tail(stderr_file: TextIO | None) -> str:
    """The last of a dead server's stderr, or ``""`` if it wrote none."""
    if stderr_file is None:
        return ""
    with contextlib.suppress(OSError, ValueError):
        stderr_file.seek(0)
        text = stderr_file.read().strip()
        if text:
            return text[-STDERR_TAIL_CHARS:]
    return ""


def _set_exception(future: asyncio.Future[CallToolReturn], exc: BaseException) -> None:
    """Complete a call future, unless its caller already went away."""
    if not future.done():
        future.set_exception(exc)
