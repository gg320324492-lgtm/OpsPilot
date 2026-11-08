"""A Client Component must only ask for the catalogue sections it was handed.

## The hole this file closes

The dashboard's four Client Components -- ``decision-actions``, ``locale-switcher``,
``reindex-button`` and ``ticket-form`` -- cannot read the locale cookie, so the
Server Component above them resolves the locale and hands down *a slice* of the
catalogue. ``createScopedT`` (``web/src/i18n/format.ts``) turns that slice into a
``t``.

The slice is the whole safety mechanism, and it is enforced in exactly one
place: by whether the key a component looks up happens to be inside it.

* **A key inside the slice** resolves, and the operator sees the sentence.
* **A key outside the slice** makes ``format.ts::resolve`` throw
  ``i18n: no message for "<key>" in this catalogue.`` -- and that throw happens
  *inside a click handler*, on the one screen where an operator is deciding
  whether money moves. The approval buttons die; the failure panel that is
  supposed to explain a failed decision is itself the thing that fails.

``web/src/i18n/format.ts`` says the missing key is "the last line of defence",
and it is -- but only for the *whole* catalogue. A scoped ``t`` is not the whole
catalogue, and the last line of defence does not apply to it.

## Why TypeScript does not catch this

``TFunction`` is ``(key: MessageKey, params?) => string``, where ``MessageKey``
is derived from the **English catalogue in full**. ``createScopedT`` returns
exactly that type, so a Client Component may name any key in the catalogue and
the compiler says nothing.

The type is right for the *server*: a Server Component's ``t`` really is the
whole catalogue. It is wrong for the *client*, where the runtime dictionary is
the slice. ``getCopy``'s return type narrows the *prop* -- a page that handed
``DecisionActions`` fewer sections than its ``DecisionCopy`` demands is a
compile error -- but nothing narrows the *key space* of the ``t`` that
``createScopedT`` builds from that prop.

So the mismatch is invisible to ``npm run typecheck``, invisible to
``npm run lint``, and invisible to every Python test in this repository, and it
is invisible until an operator clicks Approve.

## What this file asserts

For each Client Component in ``web/src``:

1. **The scope.** The catalogue sections named in its ``Pick<typeof en, ...>``
   copy type -- the sections its Server Component hands it.
2. **The demand.** Every catalogue key the component can reach: the keys it
   looks up itself, plus the keys reached through the modules it imports. A
   module's demand is computed **per exported function**, because a module may
   export a Server-Component-only helper alongside one the client uses -- which
   is exactly the case in ``web/src/lib/format.ts``, where ``formatRelative``
   reads ``format.*`` and ``formatTimestamp`` reads nothing.
3. **The gap.** Every demanded key must fall inside a section the scope names.

A fourth assertion keeps the second honest: ``web/src/lib/format.ts`` must
export exactly one function that reads the catalogue, and that function must
not be called from a Client Component. Without it, a second ``t``-using export
could be added to that module and quietly widen the demand.

## What is deliberately not asserted

**That a Client Component uses every section it was handed.** Passing a section
a component never reads is a slightly larger browser bundle and nothing else.

**That the Server Component above passes exactly the sections the client
needs.** ``getCopy``'s return type already makes "too few" a compile error at
the page, and "too many" is not an error.

**Anything about the API.** The catalogue's keys are checked against the
backend enums in ``tests/unit/test_the_i18n_catalogue_is_complete.py``; this
file is about the *scope* a key is looked up in.
"""

from __future__ import annotations

import json
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WEB_SRC = REPO_ROOT / "web" / "src"

#: The `"use client"` directive, as the first line of a file.
_CLIENT_DIRECTIVE = re.compile(r"""^\s*["']use client["']\s*;?\s*$""")

#: The copy type a Client Component declares: `Pick<typeof en, "a" | "b">`.
_COPY_PICK = re.compile(r"Pick<\s*typeof\s+en\s*,\s*(?P<sections>[^>]+)>")

#: A section name inside that type.
_SECTION_NAME = re.compile(r"""["']([^"']+)["']""")

#: A literal catalogue lookup: `t("some.key")`.
_T_CALL = re.compile(r"""\bt\(\s*(?P<quote>["'])(?P<key>[^"']+)(?P=quote)""")

#: An import of a module inside this app: `from "@/lib/format"`.
_IMPORT_FROM = re.compile(r"""from\s+["'](?P<path>@/[^"']+)["']""")

#: An exported function, whose body may look keys up.
_EXPORTED_FUNCTION = re.compile(r"export\s+(?:async\s+)?function\s+(?P<name>\w+)")

#: A call to a name -- how a Client Component says which exports it uses.
_CALL = re.compile(r"\b(?P<name>[A-Za-z_$][\w$]*)\s*\(")

#: The module that resolves a status enum to a label. It looks its key up
#: dynamically (`status.${group}.${value}`), so the literal-call scan cannot
#: see it and the `status` section is demanded by the *import* instead.
_ENUMS_MODULE = "@/i18n/enums"


def _strip_comments(source: str) -> str:
    """The source with its comments blanked out, same length.

    **Comments are data here.** Every file in this app documents its own
    behaviour at length, and a docstring that writes ``t("nav.runs")`` -- as
    ``web/src/i18n/format.ts`` does -- is not a catalogue lookup. Scanning the
    raw text would count it as one.

    String literals are *kept*: the argument of a ``t()`` call is a string, and
    blanking it would delete the very thing being looked for. The scanner
    therefore tracks string state, so a ``//`` inside a URL is not mistaken for
    the start of a line comment.
    """
    out = list(source)
    index = 0
    length = len(source)
    while index < length:
        character = source[index]
        if character == "/" and index + 1 < length and source[index + 1] == "/":
            end = source.find("\n", index)
            end = length if end == -1 else end
            for position in range(index, end):
                out[position] = " "
            index = end
            continue
        if character == "/" and index + 1 < length and source[index + 1] == "*":
            end = source.find("*/", index + 2)
            end = length if end == -1 else end + 2
            for position in range(index, end):
                if out[position] != "\n":
                    out[position] = " "
            index = end
            continue
        if character in "\"'`":
            quote = character
            index += 1
            while index < length:
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        index += 1
    return "".join(out)


def _source_files() -> list[pathlib.Path]:
    """Every TypeScript source file in the app."""
    found = [path for path in WEB_SRC.rglob("*.ts") if path.is_file()]
    found += [path for path in WEB_SRC.rglob("*.tsx") if path.is_file()]
    return sorted(found)


def _is_client_component(path: pathlib.Path) -> bool:
    """Whether the file opens with the `"use client"` directive.

    **The directive has to be the first thing in the file.** A docstring that
    *mentions* `"use client"` -- ``approval-summary.tsx`` does, when it calls
    itself "a `"use client"` boundary's neighbour" -- is prose about the
    boundary, not the boundary itself.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        return bool(_CLIENT_DIRECTIVE.match(line))
    return False


def _sections_of(source: str) -> list[set[str]]:
    """The catalogue sections named by each ``Pick<typeof en, ...>`` type."""
    return [
        set(_SECTION_NAME.findall(match.group("sections"))) for match in _COPY_PICK.finditer(source)
    ]


def _t_keys(source: str) -> set[str]:
    """Every catalogue key looked up with a literal string."""
    return {match.group("key") for match in _T_CALL.finditer(source)}


def _called_names(source: str) -> set[str]:
    """Every name the source calls."""
    return {match.group("name") for match in _CALL.finditer(source)}


def _body_from(source: str, start: int) -> str:
    """The body of the function whose signature ends at or after ``start``.

    The parameter list is skipped first, because a parameter may be typed
    inline -- ``export function DecisionPanel({ failure, t }: { ... })`` -- and
    that object type's brace is not the body.
    """
    paren = source.find("(", start)
    if paren == -1:
        return ""
    depth = 0
    cursor = paren
    while cursor < len(source):
        if source[cursor] == "(":
            depth += 1
        elif source[cursor] == ")":
            depth -= 1
            if depth == 0:
                cursor += 1
                break
        cursor += 1
    brace = source.find("{", cursor)
    if brace == -1:
        return ""
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace : index + 1]
    return source[brace:]


def _function_bodies(source: str) -> dict[str, str]:
    """Each exported function's body, by name."""
    bodies: dict[str, str] = {}
    for match in _EXPORTED_FUNCTION.finditer(source):
        body = _body_from(source, match.end())
        if body:
            bodies[match.group("name")] = body
    return bodies


def _imports(source: str) -> list[str]:
    """The `@/...` modules the source imports."""
    return [match.group("path") for match in _IMPORT_FROM.finditer(source)]


def _resolve(module: str) -> pathlib.Path | None:
    """The file an `@/...` import names, if there is one."""
    relative = module[2:]
    for suffix in (".tsx", ".ts"):
        candidate = WEB_SRC / f"{relative}{suffix}"
        if candidate.is_file():
            return candidate
    return None


class _Demand:
    """The catalogue sections each exported function of a module needs.

    **Per function, not per file.** ``web/src/lib/format.ts`` exports
    ``formatTimestamp`` (which reads nothing) and ``formatRelative`` (which
    reads ``format.*``). A file-level analysis would charge every importer of
    the module with ``formatRelative``'s demand and fail on a component that
    only ever renders a timestamp.
    """

    def __init__(self) -> None:
        self._cache: dict[pathlib.Path, dict[str, set[str]]] = {}

    def of_module(self, path: pathlib.Path) -> dict[str, set[str]]:
        """Each exported function of ``path``, and the sections it needs."""
        if path in self._cache:
            return self._cache[path]
        source = _strip_comments(path.read_text(encoding="utf-8"))
        # An import of the enum-label module demands the `status` section for
        # every function in the file: ES module imports are file-scoped, so a
        # function cannot use one the file did not import.
        status_demanded = _ENUMS_MODULE in _imports(source)
        demands: dict[str, set[str]] = {}
        for name, body in _function_bodies(source).items():
            needed = {key.split(".", 1)[0] for key in _t_keys(body)}
            needed |= self._demand_of_imports(path, source, body)
            if status_demanded:
                needed.add("status")
            demands[name] = needed
        self._cache[path] = demands
        return demands

    def _demand_of_imports(self, path: pathlib.Path, source: str, body: str) -> set[str]:
        """The sections the imported functions this body calls need."""
        needed: set[str] = set()
        called = _called_names(body)
        for module in _imports(source):
            target = _resolve(module)
            if target is None or target == path:
                continue
            for name, sections in self.of_module(target).items():
                if name in called:
                    needed |= sections
        return needed


def _client_components() -> list[pathlib.Path]:
    """Every Client Component in the app."""
    return [path for path in _source_files() if _is_client_component(path)]


def test_a_client_component_only_asks_for_the_catalogue_it_was_handed() -> None:
    """Every key a Client Component reaches must be inside its copy slice.

    **This is the check the type system cannot make.** ``createScopedT``
    returns a ``TFunction`` typed over the *whole* English catalogue, so a
    Client Component may name any key in it and ``npm run typecheck`` says
    nothing. At runtime the dictionary is the slice the Server Component
    handed down, and a key outside it is a throw from ``format.ts::resolve``
    inside the component.
    """
    demand = _Demand()
    problems: list[str] = []

    for path in _client_components():
        relative = path.relative_to(REPO_ROOT).as_posix()
        source = _strip_comments(path.read_text(encoding="utf-8"))
        scopes = _sections_of(source)

        needed: set[str] = {key.split(".", 1)[0] for key in _t_keys(source)}
        for module in _imports(source):
            target = _resolve(module)
            if target is None:
                continue
            for name, sections in demand.of_module(target).items():
                if name in _called_names(source):
                    needed |= sections
        if _ENUMS_MODULE in _imports(source):
            needed.add("status")

        if not scopes:
            # A component with no copy type takes no catalogue of its own;
            # anything it reads has to arrive as a prop.
            if needed:
                problems.append(
                    f"{relative}: reads {sorted(needed)} but declares no "
                    "`Pick<typeof en, ...>` copy type"
                )
            continue

        for scope in scopes:
            outside = sorted(needed - scope)
            if outside:
                problems.append(
                    f"{relative}: reads the catalogue sections {outside}, which "
                    f"its copy type does not include (it has {sorted(scope)}). "
                    "A key outside the slice is a throw from "
                    "format.ts::resolve inside the component, not a fallback."
                )

    assert not problems, (
        f"these Client Components look up catalogue sections they were not handed: {problems}"
    )


def test_the_only_catalogue_reading_helper_in_the_shared_format_module_is_server_only() -> None:
    """``lib/format.ts`` must expose exactly one catalogue-reading export.

    ``formatRelative`` reads ``format.*`` through a ``t`` it is handed, and its
    docstring says it is "a Server-Component-only helper". The demand analysis
    above handles that by working per exported function -- but only for as long
    as there is exactly one such function. A second one, added to the same
    module and called by a Client Component, would need the ``format`` section
    in its scope, and nothing above would say so.
    """
    module = WEB_SRC / "lib" / "format.ts"
    source = _strip_comments(module.read_text(encoding="utf-8"))
    readers = [name for name, body in _function_bodies(source).items() if _t_keys(body)]

    assert readers == ["formatRelative"], (
        "web/src/lib/format.ts exports "
        f"{readers} as functions that read the catalogue, and the demand "
        "analysis in this file knows how to place exactly one of them "
        "(`formatRelative`). A second one has to be checked here as well, "
        "because a Client Component cannot hold the whole catalogue."
    )

    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in _source_files()
        if path != module
        and re.search(r"\bformatRelative\s*\(", path.read_text(encoding="utf-8"))
        and _is_client_component(path)
    ]
    assert not offenders, (
        "these Client Components call `formatRelative`, which reads the "
        f"catalogue through a `t` they do not have: {offenders}. It is "
        "documented as a Server-Component-only helper."
    )


def _catalogue_leaves(node: dict[str, object], prefix: str = "") -> dict[str, str]:
    """Every string in a catalogue, plural forms included, by full path."""
    leaves: dict[str, str] = {}
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, str):
            leaves[path] = value
        elif isinstance(value, dict) and isinstance(value.get("other"), str):
            for form, text in value.items():
                leaves[f"{path}.{form}"] = text
        elif isinstance(value, dict):
            leaves.update(_catalogue_leaves(value, path))
    return leaves


def test_no_catalogue_value_carries_its_own_leading_space() -> None:
    """A message may not begin with whitespace.

    **The separators belong to the code.** Every message in this catalogue is a
    fragment of a sentence, and the code that renders one supplies the space
    around it -- ``{" "}`` between two JSX expressions, ``" " +`` between two
    sentences in ``lib/decision-error.ts``. A value that carries its own
    leading space therefore renders with two spaces wherever the code already
    puts one, and with a space that opens a line where it should not.

    This is not a hypothetical: the Chinese ``apiStatus.tokenHelp_after`` began
    with a space while every other value in either catalogue did not, and the
    banner it renders said ``web/.env.local`` -- two spaces -- in the one place
    an operator is told which environment variable to set.

    The check is on the *leading* edge only. A trailing space is occasionally
    wanted -- a fragment that ends a clause before a second one is appended --
    and this catalogue has none, but forbidding it would forbid a legitimate
    shape rather than a defect.
    """
    problems: list[str] = []
    for locale in ("en", "zh-CN"):
        path = WEB_SRC / "i18n" / "dictionaries" / f"{locale}.json"
        catalogue = json.loads(path.read_text(encoding="utf-8"))
        for key, value in _catalogue_leaves(catalogue).items():
            if value[:1].isspace():
                problems.append(f"{locale}.json {key}: starts with {value[0]!r}")

    assert not problems, (
        "these catalogue values begin with whitespace, which duplicates the "
        f"separator the rendering code already supplies: {problems}"
    )
