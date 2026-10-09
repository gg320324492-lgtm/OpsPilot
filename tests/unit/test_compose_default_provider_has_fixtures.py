"""The Compose default provider must be able to answer, in the image Compose ships.

`docker-compose.yml` defaults the worker to the deterministic provider --
``MODEL_PROVIDER: ${MODEL_PROVIDER:-fake}`` and
``OPSPILOT_FAKE_SCENARIO: ${OPSPILOT_FAKE_SCENARIO:-duplicate_charge}`` -- so a
clone with no API key and no ``.env`` runs the whole golden path on
:class:`~opspilot.adapters.models.fake.FakeModelProvider`. That is the single
most-exercised configuration this project ships, and it could not work: the
image copied ``src/``, ``mcp_servers/``, ``alembic.ini``, ``migrations/`` and
``knowledge/``, and never ``evals/datasets/``, where the fixtures live. Every run
died in its first step with

    opspilot.adapters.models.fake.UnmatchedFixtureError: FakeModelProvider: no
    recorded generate_structured response matched scenario 'duplicate_charge'

and ``failure_reason: "interrupted"``. The unit suite was green throughout,
because it resolves the fixtures off the repository root.

Two facts have to hold together for the shipped default to work, and each is
checked here by *reading* the thing that states it:

The scenario Compose defaults to must have a fixture
    ``OPSPILOT_FAKE_SCENARIO`` names a scenario; the fake replays a scenario's
    ``calls`` in order and raises ``UnmatchedFixtureError`` when the name has no
    fixture. A default pointing at a scenario nobody recorded fails every run.

The fixtures must be copied at the path ``fake.py`` resolves
    ``_default_fixtures_dir`` is ``Path(__file__).resolve().parents[4] / "evals"
    / "datasets" / "fixtures"`` -- the repository root, four levels above the
    package. That arithmetic only holds while the package resolves under the
    image's ``WORKDIR`` (``/app/src/opspilot/...``), so the COPY and the
    arithmetic are one contract seen from two ends. A test asserting only that
    some ``COPY evals`` line exists would pass if the fixtures were copied to
    ``/app/src/evals``, which is the exact shape that broke. So this resolves the
    real ``parents[4]`` and asserts *that* directory is the one the Dockerfile
    puts the fixtures in.

The fixture-matching logic in ``fake.py`` is deliberately untouched by this
guard. The fixtures are the record of what the deterministic provider answered;
loosening a match to make a run pass would destroy the determinism every test
downstream of it measures (``docs/risks.md`` R2).
"""

from __future__ import annotations

import pathlib
import re

from opspilot.adapters.models import fake

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DOCKERFILE = REPO_ROOT / "Dockerfile"


def _compose_default(variable: str) -> str:
    """The ``${VAR:-default}`` value compose would use for ``variable``.

    Returns the literal default, which is what a clone with no ``.env`` and no
    shell override gets -- the case that is broken when the default is wrong.
    """
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    # `${MODEL_PROVIDER:-fake}` -- the ``:-`` default form only.
    match = re.search(rf"^\s*{variable}:\s*\s*\$\{{{variable}:-([^}}]*)\}}", text, re.M)
    assert match is not None, (
        f"docker-compose.yml no longer declares a ${{{variable}:-...}} default "
        f"for {variable}. This guard reads the default out of the file; if the "
        "variable moved or lost its default, update this guard rather than "
        "letting it pass vacuously."
    )
    return match.group(1).strip()


def test_the_compose_default_scenario_has_a_fixture() -> None:
    """The scenario ``docker-compose.yml`` defaults to resolves in the fixtures.

    The guard that would have caught the shipped defect, and the one that matters.
    """
    scenario = _compose_default("OPSPILOT_FAKE_SCENARIO")
    assert scenario, (
        "OPSPILOT_FAKE_SCENARIO defaults to empty. An unconfigured deployment "
        "then matches by request hash instead of replaying a scenario, and "
        "every shipped fixture records `request_hash: null` -- so every call "
        "raises UnmatchedFixtureError."
    )
    provider = fake.FakeModelProvider(provider_name="fake", scenario=scenario)
    assert scenario in provider.scenario_names(), (
        f"docker-compose.yml defaults OPSPILOT_FAKE_SCENARIO to {scenario!r}, "
        f"which has no fixture. Available scenarios: "
        f"{provider.scenario_names()}. Either the default is wrong or the "
        "fixture was never recorded -- and either way every run started by the "
        "shipped default dies at `classifying` with UnmatchedFixtureError."
    )


def test_the_compose_default_provider_is_fake_and_needs_the_fixtures() -> None:
    """The default provider is the replaying one, so the fixtures are required.

    If ``MODEL_PROVIDER`` ever defaulted to a real provider this would stop
    being load-bearing; the assertion is what tells the next reader that the
    guard above is not protecting a dead configuration.
    """
    assert _compose_default("MODEL_PROVIDER") == "fake", (
        "docker-compose.yml no longer defaults MODEL_PROVIDER to 'fake'. The "
        "scenario guard above exists because that default replays fixtures; "
        "revisit it if the default becomes a real provider."
    )


def test_the_dockerfile_copies_the_fixtures_where_fake_py_resolves_them() -> None:
    """The fixtures are copied to the directory ``_default_fixtures_dir`` names.

    The assertion is on the *resolved* path rather than on the presence of a
    ``COPY evals`` line: ``fake.py`` reads ``parents[4]`` relative to its own
    location, so a copy to any other directory -- ``/app/src/evals``, say --
    satisfies the most obvious version of this test and still breaks every run.
    """
    resolved = fake._default_fixtures_dir()
    assert resolved == REPO_ROOT / "evals" / "datasets" / "fixtures", (
        f"_default_fixtures_dir() resolved to {resolved}, which is not "
        f"{REPO_ROOT / 'evals' / 'datasets' / 'fixtures'}. The arithmetic that "
        "walks four parents above fake.py only holds while the package lives at "
        "<root>/src/opspilot/adapters/models/fake.py. A non-editable install "
        "moves it into site-packages and silently breaks fixture resolution."
    )

    assert resolved.is_dir(), f"{resolved} does not exist; the guard is vacuous."

    text = DOCKERFILE.read_text(encoding="utf-8")
    # The sources of every `COPY src ... dst` in the runtime stage, with the
    # `--flag=value` prefixes dropped. The sources are relative to the build
    # context (the repo root) and the destinations relative to WORKDIR, so a
    # source ending in `evals/datasets/` is the line under test.
    copies: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == "COPY":
            copies += [p for p in parts[1:] if not p.startswith("--")][:-1]
    assert any(src.endswith("evals/datasets/") for src in copies), (
        "The Dockerfile copies no evals/datasets/ into the image, so "
        f"MODEL_PROVIDER=fake has nothing to replay and every run started by "
        f"the shipped default dies with UnmatchedFixtureError. COPY sources: "
        f"{copies}"
    )
