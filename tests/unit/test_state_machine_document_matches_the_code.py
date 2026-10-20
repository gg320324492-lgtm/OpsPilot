"""``docs/agent-state-machine.md`` is the specification; the code must match it.

## Why this file exists

``src/opspilot/domain/runs.py`` says, in a comment on the table it defines:

    # From docs/agent-state-machine.md §2. This table is the specification: every
    # edge below is justified in §2.1, and COMPLETED/FAILED deliberately have empty
    # successor sets (COMPLETED -> EXECUTING is illegal and has a test).

**This table is the specification.** So the document is the authority and the
Python is a copy of it -- and nothing checked that the copy was faithful.

That is the same shape as the defect ``tests/integration/test_mcp_contract_names.py``
was written for, and the same shape this repository has now found twice:

- the MCP servers registered their tools under bare function names, both
  servers' own tests passed, and only the *contract document* knew the
  names were wrong;
- ``TOOL_PERMISSION`` in the dashboard was missing three of the nine
  registered tools, and the file's own docstring -- which claimed every tool
  was in it -- was the only thing that noticed, far too late.

Here the copy is the *other* direction: the document is written first, the
table is transcribed into Python, and a transcription error would be invisible
to every test in the repository. ``tests/unit/test_run_state.py`` derives all
of its cases from ``ALLOWED_TRANSITIONS`` -- the table checking itself -- so it
would pass just as happily with a table that had drifted from the document.

## What this file asserts

1. **The states.** §1 carries the enum verbatim; the code's ``RunStatus`` must
   have exactly those names with exactly those values, in both directions.
2. **The edges.** §2 carries ``ALLOWED_TRANSITIONS`` verbatim; the code's table
   must be exactly the document's table, in both directions. An edge the
   document does not have is an edge the workflow can take but nobody
   justified; an edge the document has and the code does not is a documented
   transition that raises ``IllegalTransition``.
3. **Which states are terminal.** §1's table has an "Is it terminal?" column;
   the code's ``TERMINAL`` must agree with it. This is the column a reader
   would check to decide whether a run is finished, and it is the one place
   where a transcription slip changes what the code does rather than only
   what it says.

## How the document is read

The Python blocks are parsed with ``ast`` rather than pattern-matched, so a
reformatted block is still read, and a *name* that the document mentions
outside the table is not mistaken for an edge. The names in §2 are bare
(``RECEIVED``, not ``RunStatus.RECEIVED``) because that is how the document
writes them; they are resolved against the code's own enum, which means the
comparison cannot pass on a name neither side defines.
"""

from __future__ import annotations

import ast
import pathlib
import re

from opspilot.domain.runs import ALLOWED_TRANSITIONS, TERMINAL, RunStatus

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCUMENT = REPO_ROOT / "docs" / "agent-state-machine.md"


def _section(text: str, heading: str) -> str:
    """The body of a ``## <heading>`` section, up to the next ``##``.

    Sliced by heading rather than by line number, so that adding a section to
    the document does not silently move the boundaries this file reads.
    """
    start = text.index(f"## {heading}")
    rest = text[start + len(f"## {heading}") :]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def _python_blocks(text: str) -> list[str]:
    """Every fenced ``python`` block in the text."""
    return re.findall(r"```python\n(.*?)```", text, re.DOTALL)


def _states_in_the_document(text: str) -> dict[str, str]:
    """The enum §1 declares: ``NAME`` -> ``"value"``.

    Read out of the block by parsing it, so that a comment inside the block
    cannot be mistaken for a member and a member written in another order is
    still found.
    """
    blocks = _python_blocks(_section(text, "1. States"))
    assert blocks, "§1 no longer carries a python block; the document changed shape"

    tree = ast.parse(blocks[0])
    class_def = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "RunStatus"
    )
    members: dict[str, str] = {}
    for statement in class_def.body:
        if not isinstance(statement, ast.Assign):
            continue
        target = statement.targets[0]
        if not isinstance(target, ast.Name) or not isinstance(statement.value, ast.Constant):
            continue
        value = statement.value.value
        if isinstance(value, str):
            members[target.id] = value
    return members


def _transitions_in_the_document(text: str) -> dict[str, frozenset[str]]:
    """The table §2 declares: ``NAME`` -> the names it may move to.

    The names are bare in the document, so they are resolved against the
    code's enum. A name in the document that the enum does not define is a
    failure here rather than a name that quietly drops out of the comparison.
    """
    blocks = _python_blocks(_section(text, "2. Allowed transitions"))
    assert blocks, "§2 no longer carries a python block; the document changed shape"

    tree = ast.parse(blocks[0])
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    )
    table = assignment.value
    assert isinstance(table, ast.Dict), "§2's block no longer assigns a dict literal"

    known = {status.name for status in RunStatus}
    transitions: dict[str, frozenset[str]] = {}
    for key, value in zip(table.keys, table.values, strict=True):
        assert isinstance(key, ast.Name), f"§2 has a non-name key: {key!r}"
        if key.id not in known:
            raise AssertionError(
                f"§2 names a state the code does not define: {key.id}. "
                f"The code's states are {sorted(known)}."
            )
        successors: set[str] = set()
        # The document writes ``frozenset({A, B})`` -- and, for the two
        # terminal states, ``frozenset()``. So the set of names is either the
        # call's single argument or, for an empty one, nothing at all.
        elements = (value.args[0] if value.args else None) if isinstance(value, ast.Call) else value
        names = elements.elts if isinstance(elements, ast.Set) else []
        for element in names:
            assert isinstance(element, ast.Name), f"§2 has a non-name successor: {element!r}"
            if element.id not in known:
                raise AssertionError(
                    f"§2 names a successor the code does not define: {element.id}. "
                    f"The code's states are {sorted(known)}."
                )
            successors.add(element.id)
        transitions[key.id] = frozenset(successors)
    return transitions


def _terminal_column_in_the_document(text: str) -> dict[str, bool]:
    """§1's "Is it terminal?" column, read from the markdown table.

    The cell is ``yes`` in bold for the terminal states and ``no`` otherwise;
    the bold markers are stripped before the answer is read, because the
    emphasis is the document's way of marking the same answer.
    """
    section = _section(text, "1. States")
    rows = [line for line in section.splitlines() if line.startswith("| `") and "|" in line[1:]]
    terminal: dict[str, bool] = {}
    for row in rows:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        name = cells[0].strip("`")
        if name not in {status.name for status in RunStatus}:
            continue
        answer = cells[2].replace("**", "").strip().lower()
        terminal[name] = answer == "yes"
    return terminal


def test_the_document_declares_exactly_the_states_the_code_has() -> None:
    """§1's enum and ``RunStatus`` are the same set of states and values.

    A state in the code that the document does not declare is a state
    somebody will meet in a row and in no diagram; a state in the document
    that the code does not have is a diagram of a workflow that cannot run.
    """
    text = DOCUMENT.read_text(encoding="utf-8")
    documented = _states_in_the_document(text)
    code = {status.name: status.value for status in RunStatus}

    assert documented, "§1's enum block yielded no states"

    missing = sorted(set(code) - set(documented))
    assert not missing, (
        f"the code has states the document does not declare: {missing}. "
        "A state nobody documented is a state nobody can reason about."
    )

    extra = sorted(set(documented) - set(code))
    assert not extra, (
        f"the document declares states the code does not have: {extra}. "
        "The document is the specification, so this is a code defect."
    )

    wrong = {
        name: (code[name], documented[name]) for name in code if documented.get(name) != code[name]
    }
    assert not wrong, f"these states have the wrong value in the document (code, document): {wrong}"


def test_the_document_declares_exactly_the_transitions_the_code_allows() -> None:
    """§2's table and ``ALLOWED_TRANSITIONS`` are the same table.

    Both directions, because they fail differently: an edge the code has and
    the document does not is an unjustified transition, and an edge the
    document has and the code does not is a transition that raises
    ``IllegalTransition`` when a workflow tries to take it.
    """
    text = DOCUMENT.read_text(encoding="utf-8")
    documented = _transitions_in_the_document(text)
    code = {
        status.name: frozenset(successor.name for successor in successors)
        for status, successors in ALLOWED_TRANSITIONS.items()
    }

    assert documented, "§2's table yielded no transitions"

    missing = sorted(set(code) - set(documented))
    assert not missing, (
        f"the code allows transitions the document does not list: {missing}. "
        "§2 is where every edge is justified; an unlisted edge is unjustified."
    )

    extra = sorted(set(documented) - set(code))
    assert not extra, (
        f"the document lists transitions the code does not allow: {extra}. "
        "A workflow that took one of these would raise IllegalTransition."
    )

    different = {
        status: (
            sorted(code[status]),
            sorted(documented[status]),
        )
        for status in code
        if code[status] != documented[status]
    }
    assert not different, (
        "these states allow different successors in the code and in the document "
        "(code, document): "
        f"{different}"
    )


def test_the_document_and_the_code_agree_on_which_states_are_terminal() -> None:
    """§1's "Is it terminal?" column and ``TERMINAL`` are the same set.

    The column is the one a reader consults to decide whether a run is
    finished, and it decides behaviour elsewhere: a terminal run is not
    claimed, and a run that is not terminal is. A transcription slip in this
    column would tell an operator one thing and the scheduler another.
    """
    text = DOCUMENT.read_text(encoding="utf-8")
    documented = _terminal_column_in_the_document(text)
    code = {status.name for status in TERMINAL}

    assert set(documented) == {status.name for status in RunStatus}, (
        f"§1's table does not have a terminal column for every state; it has {sorted(documented)}"
    )

    wrong = sorted(name for name in code if documented.get(name) != (name in code))
    assert not wrong, (
        "these states are terminal in one of the two and not in the other "
        "(the document is the specification): "
        f"{wrong}. §1's column says { {name: documented.get(name) for name in wrong} } "
        f"and TERMINAL says { {name: name in code for name in wrong} }"
    )
