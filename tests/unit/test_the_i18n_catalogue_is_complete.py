"""The dashboard's two catalogues must carry the same messages, and the Chinese
one must actually be Chinese.

## Why this file exists

The dashboard ships two message catalogues -- ``web/src/i18n/dictionaries/en.json``
and ``zh-CN.json`` -- and four groups of contract enums whose labels live in a
``status`` section of each of them (``web/src/i18n/enums.ts``). Both are read by
the runtime in a way that fails *loudly* for a missing key and *quietly* for a
missing label, and the quiet one is the one that reaches an operator.

1. **A key in one catalogue and not the other.** ``format.ts::resolve`` throws
   on a miss and names the key, so this is a 500 on the page an operator is
   trying to decide a refund from. The other direction -- a Chinese key English
   does not have -- is a translation of something that no longer exists.

2. **A placeholder in one catalogue and not the other.** The runtime fills
   ``{name}`` from the params the call site passes and leaves anything else
   alone, so a placeholder English has and Chinese does not is a number the
   Chinese operator never sees, and one Chinese has and English does not is a
   number the English operator never sees. This is not caught by "the key
   exists in both files": ``dashboard.replayRunSuffix`` existed in both, and
   the English one silently dropped the card count the call site passes.

3. **A Chinese value that is still English.** A Chinese file that copies
   English verbatim is green on every structural check and ships a product
   that claims to be localised and is not.

4. **An enum member with no label.** ``enums.ts::labelFor`` catches a missing
   key and falls back to ``humanise(value)``, which is right in English and
   wrong on a Chinese page: the operator gets one English word where the rest
   of the screen is Chinese, on the one screen they are deciding on. The
   fallback exists for a deployment running a newer API than this dashboard,
   not for a status this dashboard has forgotten.

## Why this is a Python test and not the TypeScript one

``web/e2e/i18n-catalogue.spec.ts`` already asserts (1), (3) and the plural
rules. It is a Playwright file, so it needs the web toolchain and a browser to
run, which makes it the wrong place for the check a translator wants to run
before pushing: the translator edits ``web/src/i18n/dictionaries/`` and cannot
easily start the whole stack. This file reads the same two JSON files and the
same backend enums with nothing but the standard library, so it runs in the
ordinary ``pytest tests/unit`` sweep.

The enum half overlaps ``tests/unit/test_frontend_copies_match_the_backend.py``,
which asserts the same four groups against the same backend enums. That
overlap is deliberate and cheap; the parts this file adds are the ones that
test does not reach:

* the catalogue's ``status`` section names against the ``LabelGroup`` union in
  ``enums.ts`` -- a rename there would make ``labelFor`` ask for
  ``status.tool_call.proposed`` and fall back to ``humanise()`` on **every**
  tool call status, in every locale, while every other test stayed green;
* the placeholder parity, which no test asserted;
* the Chinese catalogue's prose, which no Python test asserted.

## What is deliberately not asserted

**The values are not required to differ from each other.** A contract field
name, an environment variable, a path and a fixture are the same string in
every locale, and they are listed in ``_IDENTICAL_BY_DESIGN`` with the reason
each one is exempt. An exemption without a reason is a hole in the check, so
the reason is part of the entry.

**The plural forms are not compared between locales.** ``Intl.PluralRules``
selects ``one`` in English and ``other`` in Chinese, so the two catalogues
genuinely have different shapes; what is asserted is that each one is shaped
correctly for its own language.
"""

from __future__ import annotations

import json
import pathlib
import re
import unicodedata
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DICTIONARY_DIR = REPO_ROOT / "web" / "src" / "i18n" / "dictionaries"
_ENUMS_TS = REPO_ROOT / "web" / "src" / "i18n" / "enums.ts"

#: The locale every other one is a translation of.
SOURCE_LOCALE = "en"

#: The locale the dashboard ships besides the source one.
TRANSLATED_LOCALE = "zh-CN"

#: A CJK ideograph, so a value can be asked "is this Chinese at all?".
_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")

#: The parameters a message may interpolate.
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

#: The `LabelGroup` union in ``enums.ts``.
_LABEL_GROUP = re.compile(r"export\s+type\s+LabelGroup\s*=\s*(?P<body>[^;]+);", re.DOTALL)

#: The dotted path ``enums.ts`` looks a label up under.
_LOOKUP_TEMPLATE = re.compile(r"`(?P<path>[^`]*\$\{group\}[^`]*)`")


#: Chinese values that are **identical** to the English ones, on purpose.
#:
#: Each is a machine identifier, a path, a fixture, or a language's own name --
#: never prose. The reason is part of the entry, because an exemption without a
#: reason is a hole in the check that a later edit widens by accident.
_IDENTICAL_BY_DESIGN: dict[str, str] = {
    # The operator-token environment variable's name. It is greppable in the
    # compose file and in the API process's environment, so it is a name and
    # not a sentence.
    "common.envOperatorToken": "the operator-token environment variable's name",
    # The path of the file the operator has to edit.
    "apiStatus.envFile": "the dashboard's local environment file path",
    # API field names. `arguments_snapshot` and `tool_call_id` are columns in
    # `docs/api-contract.md` that an operator greps the schema for.
    "approvalCard.argumentsLabel": "an API field name",
    "approvalCard.toolCallIdLabel": "an API field name",
    "approvalSummary.argumentsLabel": "an API field name",
    # The mono row `decision-panel.tsx` renders after a failed decision. That
    # file's docstring specifies it as verbatim in every locale: it is the
    # machine's own account of the failure, and an operator quotes it in a
    # bug report.
    "decision.codeLabel": "the label of the machine's own error-code row",
    "decision.approvalIdLabel": "a field name from the API's `details`",
    "decision.statusLabel": "a field name from the API's `details`",
    "decision.traceIdLabel": "a field name from the API's `details`",
    # The directory and the glob the API reads.
    "knowledge.reindex.dir": "the directory the API reads",
    "knowledge.reindex.glob": "the glob the API reads",
    # The two languages, each named by itself. A locale switcher shows a
    # language the way that language writes it.
    "nav.locale.en": "the English language's own name",
    "nav.locale.zh-CN": "the Chinese language's own name",
    # Fixtures rather than copy: the fake provider matches the golden path on
    # this address, and the e2e suite asserts the id.
    "ticketForm.emailPlaceholder": "the golden path's fixture email address",
    "ticketForm.externalIdPlaceholder": "the golden path's fixture ticket id",
}


def _catalogue(locale: str) -> dict[str, Any]:
    """The parsed catalogue for a locale."""
    path = _DICTIONARY_DIR / f"{locale}.json"
    catalogue: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return catalogue


def _is_plural(value: object) -> bool:
    """Whether a catalogue node is a plural group rather than a group of messages.

    **The same discriminator the runtime uses.** ``format.ts`` tells a plural
    from a group by the presence of an ``other`` key, and the derived
    ``MessageKey`` type uses the same rule. A test that classified plurals
    differently would be testing a second implementation of the catalogue --
    the thing this file exists to prevent.
    """
    return isinstance(value, dict) and isinstance(value.get("other"), str)


def _message_keys(node: dict[str, Any], prefix: str = "") -> set[str]:
    """Every key a caller can pass to ``t()``.

    A plural group is **one** key, named by its base (``dashboard.replay``),
    because that is what a caller writes and what a missing form is reported
    against.
    """
    keys: set[str] = set()
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, str) or _is_plural(value):
            keys.add(path)
        elif isinstance(value, dict):
            keys |= _message_keys(value, path)
        else:
            raise AssertionError(f"the catalogue holds a non-string at {path!r}: {value!r}")
    return keys


def _leaves(node: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Every string in a catalogue, plural forms included, by full path."""
    leaves: dict[str, str] = {}
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, str):
            leaves[path] = value
        elif _is_plural(value):
            for form, text in value.items():
                leaves[f"{path}.{form}"] = text
        elif isinstance(value, dict):
            leaves.update(_leaves(value, path))
    return leaves


def _is_punctuation_only(value: str) -> bool:
    """Whether a value is nothing but punctuation and whitespace.

    A Chinese sentence that is one full-width colon or one full-stop -- the
    shape a split sentence takes when the words live in the sibling key -- is
    Chinese. It has no ideograph in it, and it must not be reported as
    untranslated English.
    """
    return all(
        character.isspace() or unicodedata.category(character).startswith("P")
        for character in value
    )


def _label_groups() -> set[str]:
    """The group names ``enums.ts`` says it can label."""
    source = _ENUMS_TS.read_text(encoding="utf-8")
    match = _LABEL_GROUP.search(source)
    assert match is not None, (
        "web/src/i18n/enums.ts no longer declares a `LabelGroup` union, so the "
        "groups it can label are no longer readable from the source. The "
        "catalogue's `status` section has to agree with whatever it declares."
    )
    return set(re.findall(r'"([^"]+)"', match.group("body")))


def _lookup_path() -> str:
    """The dotted path ``enums.ts`` looks a label up under."""
    source = _ENUMS_TS.read_text(encoding="utf-8")
    matches = _LOOKUP_TEMPLATE.findall(source)
    assert len(matches) == 1, (
        f"web/src/i18n/enums.ts should build exactly one label path from the "
        f"group and the value, and it builds {len(matches)}: {matches!r}. The "
        "path is what connects the catalogue to the backend's enums, so it "
        "cannot be guessed at."
    )
    path: str = matches[0]
    return path


def _backend_members() -> dict[str, set[str]]:
    """The backend's four contract enums, by the group that labels them."""
    from opspilot.domain.approvals import ApprovalStatus
    from opspilot.domain.runs import RunStatus
    from opspilot.domain.tools import Permission, ToolCallStatus

    return {
        "run": {status.value for status in RunStatus},
        "approval": {status.value for status in ApprovalStatus},
        "toolCall": {status.value for status in ToolCallStatus},
        "permission": {permission.value for permission in Permission},
    }


def test_the_two_catalogues_carry_the_same_message_keys() -> None:
    """Neither catalogue may have a key the other does not, in either direction.

    **Both directions, because they fail differently.** A key English has and
    Chinese does not is a throw on a page -- ``format.ts::resolve`` names the
    key and refuses to render a blank. A key Chinese has and English does not
    is a translation of a message that no longer exists, and it is invisible
    from the English side, which is the side every other test on this
    repository reads.
    """
    source = _message_keys(_catalogue(SOURCE_LOCALE))
    translated = _message_keys(_catalogue(TRANSLATED_LOCALE))

    missing = sorted(source - translated)
    assert not missing, (
        f"these keys are in {SOURCE_LOCALE}.json and not in "
        f"{TRANSLATED_LOCALE}.json: {missing}. A missing message is a throw "
        "on a page, not a fallback -- web/src/i18n/format.ts::resolve names "
        "the key and refuses to render a blank."
    )

    extra = sorted(translated - source)
    assert not extra, (
        f"these keys are in {TRANSLATED_LOCALE}.json and not in "
        f"{SOURCE_LOCALE}.json: {extra}. English is the source locale, so a "
        "key only one catalogue has is a translation of something that no "
        "longer exists."
    )


def test_every_message_placeholder_survives_translation() -> None:
    """The two catalogues must interpolate the same parameters for a message.

    ``resolve`` fills ``{name}`` from the params the call site passes and
    leaves every other name alone, so a parameter English has and Chinese does
    not is a number the Chinese operator never sees -- and one Chinese has and
    English does not is a number the English operator never sees. Neither
    throws, and neither is visible to a key-existence check.

    This is not hypothetical: ``dashboard.replayRunSuffix`` was in both
    catalogues with ``{count}`` in the Chinese one and without it in the
    English one, so the dashboard listed how many identical pending cards a
    replayed run had in Chinese and not in English.
    """
    source = _leaves(_catalogue(SOURCE_LOCALE))
    translated = _leaves(_catalogue(TRANSLATED_LOCALE))

    mismatched: list[str] = []
    for key in sorted(set(source) & set(translated)):
        expected = sorted(_PLACEHOLDER.findall(source[key]))
        found = sorted(_PLACEHOLDER.findall(translated[key]))
        if expected != found:
            mismatched.append(
                f"{key}: {SOURCE_LOCALE} has {expected}, {TRANSLATED_LOCALE} has {found}"
            )

    assert not mismatched, (
        "these messages interpolate different parameters in the two "
        "catalogues, so the same call site renders different information in "
        f"the two languages: {mismatched}"
    )


def test_the_chinese_catalogue_is_not_english() -> None:
    """No Chinese message may be the English one, except the ones that must be.

    A Chinese catalogue that copies English verbatim is green on every
    structural check and ships a product that claims to be localised and is
    not. The exemptions are the machine identifiers, the paths, the fixtures
    and the language names -- all listed in ``_IDENTICAL_BY_DESIGN`` with the
    reason each one is exempt.
    """
    source = _leaves(_catalogue(SOURCE_LOCALE))
    translated = _leaves(_catalogue(TRANSLATED_LOCALE))

    untranslated: list[str] = []
    for key in sorted(set(source) & set(translated)):
        chinese = translated[key]
        english = source[key]
        if chinese == english:
            if key not in _IDENTICAL_BY_DESIGN:
                untranslated.append(f"{key}: identical to the English ({chinese!r})")
            continue
        if (
            not _CJK.search(chinese)
            and not _is_punctuation_only(chinese)
            and key not in _IDENTICAL_BY_DESIGN
        ):
            untranslated.append(f"{key}: no Chinese character in it ({chinese!r})")

    assert not untranslated, (
        "these Chinese messages are still English, or are the same string in "
        "both catalogues without a reason on record: "
        f"{untranslated}. Either translate them, or add them to "
        "_IDENTICAL_BY_DESIGN with the reason they are identical."
    )


def test_the_label_groups_agree_with_the_catalogue() -> None:
    """``enums.ts``'s ``LabelGroup`` must name the catalogue's ``status`` sections.

    ``labelFor`` builds its key as ``status.<group>.<value>`` and the catalogue
    holds the labels under ``status``. If the two ever name the same groups
    differently -- a rename of ``toolCall`` to ``tool_call``, say -- then
    ``labelFor`` asks for a key that is not in the catalogue, catches the
    throw, and falls back to ``humanise(value)``. Every tool-call status on
    every screen then renders as one English word, in every locale, while
    every other test in this repository stays green.
    """
    groups = _label_groups()
    assert groups, "web/src/i18n/enums.ts declares no label groups at all."

    path = _lookup_path()
    section = path.split(".", 1)[0]
    assert section, (
        "web/src/i18n/enums.ts builds its label path from the group and the "
        f"value without a section ({path!r}), so the catalogue section it "
        "reads cannot be checked against it."
    )

    for locale in (SOURCE_LOCALE, TRANSLATED_LOCALE):
        sections = set(_catalogue(locale).get(section, {}))
        assert sections == groups, (
            f"the {locale} catalogue's `{section}` sections are {sorted(sections)}, "
            f"and web/src/i18n/enums.ts declares the groups {sorted(groups)}. A "
            "group the catalogue does not have is a label that resolves to "
            "nothing, and `labelFor` falls back to the English word."
        )


def test_every_backend_enum_member_has_a_label_in_both_catalogues() -> None:
    """The backend's four contract enums must be labelled exactly, in both locales.

    **Both directions, and both locales.** A member with no label does not
    break the page -- ``labelFor`` catches the missing key and falls back to
    ``humanise(value)``, which is right in English and *wrong on a Chinese
    page*: the operator gets one English word where the rest of the screen is
    Chinese, on the one screen they are deciding on.

    A label for a member the backend never sends is the other half of the
    same hole: it is a claim about the API that is false, and it is how the
    hole was hidden the first time -- the catalogue had
    ``status.toolCall.pending``, a status the backend does not have, where the
    backend's ``proposed`` was missing. The extra key made the group *look*
    complete to a reader counting keys.
    """
    backend = _backend_members()

    for locale in (SOURCE_LOCALE, TRANSLATED_LOCALE):
        labels = _catalogue(locale).get("status", {})
        for group, members in backend.items():
            labelled = set(labels.get(group, {}))
            assert labelled == members, (
                f"the {locale} catalogue's `status.{group}` labels are "
                f"{sorted(labelled)}, and the backend's {group} values are "
                f"{sorted(members)}. A status with no label renders as the "
                "English word on a translated page; a label for a status the "
                "backend never sends is a false claim about the API."
            )


def test_each_plural_is_shaped_for_its_own_locale() -> None:
    """English carries both plural forms; Chinese carries the one it selects.

    ``Intl.PluralRules("en")`` selects ``one`` and ``other``, so an English
    plural without a real ``one`` throws for a count of one -- and a plural
    whose two forms are identical is worse than a missing one, because it
    renders and reads as though English had no singular.

    ``Intl.PluralRules("zh-CN")`` selects ``other`` for every count, so a
    Chinese ``one`` form is never selected, is a sentence someone will think
    they have to maintain, and invites the belief that the two languages share
    a plural system.
    """
    source = _catalogue(SOURCE_LOCALE)
    translated = _catalogue(TRANSLATED_LOCALE)

    def plurals(node: dict[str, Any], prefix: str = "") -> dict[str, dict[str, str]]:
        found: dict[str, dict[str, str]] = {}
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                if _is_plural(value):
                    found[path] = value
                else:
                    found.update(plurals(value, path))
        return found

    problems: list[str] = []
    for key, forms in plurals(source).items():
        if "one" not in forms:
            problems.append(f"{SOURCE_LOCALE} {key}: no `one` form")
        elif "other" not in forms:
            problems.append(f"{SOURCE_LOCALE} {key}: no `other` form")
        elif forms["one"] == forms["other"]:
            problems.append(f"{SOURCE_LOCALE} {key}: `one` and `other` are identical")

    for key, forms in plurals(translated).items():
        if "one" in forms:
            problems.append(
                f"{TRANSLATED_LOCALE} {key}: an `one` form is never selected by "
                "Intl.PluralRules for this locale"
            )
        if "other" not in forms:
            problems.append(f"{TRANSLATED_LOCALE} {key}: no `other` form to fall back to")

    assert not problems, (
        f"the plural groups are not shaped for the language that selects them: {problems}"
    )
