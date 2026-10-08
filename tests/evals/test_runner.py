"""The runner's plumbing: dataset loading, isolation, and the raw result shape.

These test the *harness*, not model quality. They assert the properties §M8's
acceptance criteria name -- each case isolated in a fresh database, a malformed
dataset failing loudly, and the raw result carrying the fields the metrics read --
and they deliberately run against the **fake** provider, because the harness
being correct is a different question from the model being good.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from opspilot.adapters.models.anthropic_provider import AnthropicModelProvider
from opspilot.evals.runner import (
    KNOWLEDGE_DIR,
    REPO_ROOT,
    ClassificationCase,
    DatasetError,
    RetrievalCase,
    SafetyCase,
    ToolSelectionCase,
    load_dataset,
    run_case,
    run_dataset,
    run_id_of,
)

DATASETS = REPO_ROOT / "evals" / "datasets"


def _calls(result: dict[str, object]) -> list[dict[str, object]]:
    """The tool-call records on a raw result, typed for the assertions below."""
    calls = result.get("executed_tool_calls")
    assert isinstance(calls, list)
    return [c for c in calls if isinstance(c, dict)]


# ---------------------------------------------------------------------------
# Dataset loading -- the four shapes, and a malformed line failing loudly.
# ---------------------------------------------------------------------------


def test_all_four_datasets_load_into_their_own_shapes() -> None:
    """Each committed dataset parses into its own case model, with its own fields.

    The four datasets genuinely differ (``docs/evals.md`` §2); a loader that
    forced them into one shape would have to make a field optional, and an
    optional field is a renamed field that scores zero silently.
    """
    cls = load_dataset(DATASETS / "classification.jsonl")
    ret = load_dataset(DATASETS / "retrieval.jsonl")
    tools = load_dataset(DATASETS / "tool_selection.jsonl")
    safety = load_dataset(DATASETS / "safety.jsonl")

    assert all(isinstance(c, ClassificationCase) for c in cls)
    assert all(isinstance(c, RetrievalCase) for c in ret)
    assert all(isinstance(c, ToolSelectionCase) for c in tools)
    assert all(isinstance(c, SafetyCase) for c in safety)

    # Narrow each list to its own arm for the field assertions below.
    classification_cases = [c for c in cls if isinstance(c, ClassificationCase)]
    retrieval_cases = [c for c in ret if isinstance(c, RetrievalCase)]
    tool_cases = [c for c in tools if isinstance(c, ToolSelectionCase)]
    safety_cases = [c for c in safety if isinstance(c, SafetyCase)]

    # The distinguishing field of each shape actually carries data.
    assert classification_cases[0].expected_category
    assert retrieval_cases[0].k >= 1
    assert isinstance(retrieval_cases[0].expected_documents, list)
    assert isinstance(tool_cases[0].must_not_propose, list)
    assert isinstance(safety_cases[0].must_require_approval, bool)

    total = len(cls) + len(ret) + len(tools) + len(safety)
    assert 50 <= total <= 80, f"docs/evals.md §2 says 50-80 cases total; got {total}"


def test_a_malformed_line_fails_loudly_naming_the_case(tmp_path: Path) -> None:
    """A missing required field raises with the case id -- never a silent zero.

    A line missing ``expected_category`` must not load as a case that scores 0:
    a metric that counted it would be measuring a renamed field, not the system.
    """
    bad = tmp_path / "classification.jsonl"
    bad.write_text('{"id": "cls-bad", "input": "no category here"}\n', encoding="utf-8")
    with pytest.raises(DatasetError) as excinfo:
        load_dataset(bad)
    assert "cls-bad" in str(excinfo.value), "the error must name the offending case"


def test_an_extra_unknown_field_fails_loudly(tmp_path: Path) -> None:
    """A misspelled key is a loud error, not an ignored one.

    ``extra="forbid"`` is what turns ``{"expected_catgory": ...}`` into a red run
    instead of a case whose expectation is silently absent.
    """
    bad = tmp_path / "classification.jsonl"
    bad.write_text(
        '{"id": "cls-typo", "input": "x", "expected_catgory": "other"}\n',
        encoding="utf-8",
    )
    with pytest.raises(DatasetError) as excinfo:
        load_dataset(bad)
    assert "cls-typo" in str(excinfo.value)


def test_an_unknown_dataset_file_fails_loudly(tmp_path: Path) -> None:
    """A renamed dataset file is an error, not a clean empty table."""
    unknown = tmp_path / "classifications.jsonl"
    unknown.write_text(
        '{"id": "x", "input": "y", "expected_category": "other"}\n', encoding="utf-8"
    )
    with pytest.raises(DatasetError):
        load_dataset(unknown)


def test_an_empty_dataset_fails_loudly(tmp_path: Path) -> None:
    """A dataset with no cases is an error, not a vacuously-clean run."""
    empty = tmp_path / "classification.jsonl"
    empty.write_text("\n\n", encoding="utf-8")
    with pytest.raises(DatasetError):
        load_dataset(empty)


# ---------------------------------------------------------------------------
# run_id_of
# ---------------------------------------------------------------------------


def test_run_id_of_reads_a_uuid_string_or_object() -> None:
    """``run_id_of`` returns the UUID from either a string or a UUID value."""
    value = uuid4()
    assert run_id_of({"run_id": str(value)}) == value
    assert run_id_of({"run_id": value}) == value


def test_run_id_of_is_none_when_absent_or_unparseable() -> None:
    """A missing or malformed run id is ``None``, not a raise."""
    assert run_id_of({}) is None
    assert run_id_of({"run_id": None}) is None
    assert run_id_of({"run_id": "not-a-uuid"}) is None


# ---------------------------------------------------------------------------
# run_case -- one real workflow, isolated, with the raw shape the metrics need.
# ---------------------------------------------------------------------------


@pytest.mark.slow
async def test_run_case_isolation_two_runs_do_not_share_a_refund(tmp_path: Path) -> None:
    """Two goldens-path cases each refund once -- isolation, not accumulation.

    Each case gets its own database and its own MCP store. If they shared a store
    the second case would find ``TX-88219`` already refunded and behave like the
    already-refunded scenario, which is exactly the cross-contamination §M8's
    "fresh database per case" forbids.
    """
    cases = load_dataset(DATASETS / "safety.jsonl")
    golden = next(c for c in cases if c.id == "safe-001")

    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = await run_case(golden, provider_name="fake", tmp_dir=first_dir)
    second = await run_case(golden, provider_name="fake", tmp_dir=second_dir)

    for result in (first, second):
        executed = [
            c
            for c in _calls(result)
            if c["tool_name"] == "billing.issue_refund" and c["status"] == "executed"
        ]
        assert len(executed) == 1, f"{result['case_id']} did not refund exactly once: {result}"
    assert first["run_id"] != second["run_id"]
    # The refund's idempotency key embeds the run id, so the two runs' keys differ
    # -- proof the two runs are genuinely separate executions, not a replay.
    first_key = next(
        c["idempotency_key"] for c in _calls(first) if c["tool_name"] == "billing.issue_refund"
    )
    second_key = next(
        c["idempotency_key"] for c in _calls(second) if c["tool_name"] == "billing.issue_refund"
    )
    assert first_key != second_key


@pytest.mark.slow
async def test_run_case_records_the_fields_the_metrics_read(tmp_path: Path) -> None:
    """The raw result carries every key the twelve metrics read.

    Asserted explicitly so a metric reading a renamed key is caught here rather
    than silently scoring a miss.
    """
    cases = load_dataset(DATASETS / "safety.jsonl")
    golden = next(c for c in cases if c.id == "safe-001")
    result = await run_case(golden, provider_name="fake", tmp_dir=tmp_path)

    for key in (
        "case_id",
        "dataset",
        "run_id",
        "latency_ms",
        "terminal_status",
        "predicted_category",
        "retrieved_documents",
        "proposed_tools",
        "executed_tool_calls",
        "approval_requested",
        "escalated",
        "model_calls",
        "must_require_approval",
    ):
        assert key in result, f"the raw result is missing {key!r}"

    assert result["terminal_status"] == "completed"
    assert result["approval_requested"] is True
    assert result["predicted_category"] == "duplicate_charge"
    latency = result["latency_ms"]
    assert isinstance(latency, int) and latency >= 0
    assert result["run_id"] is not None


@pytest.mark.slow
async def test_run_case_records_the_retrieval_rank_order(tmp_path: Path) -> None:
    """The retrieval step's slugs are recorded in rank order for Recall@K.

    The golden ticket's retrieval must surface at least one of the two documents
    ``docs/milestones.md`` §M6 names, or the recall metric has nothing to score.
    """
    cases = load_dataset(DATASETS / "safety.jsonl")
    golden = next(c for c in cases if c.id == "safe-001")
    result = await run_case(golden, provider_name="fake", tmp_dir=tmp_path)
    documents = result["retrieved_documents"]
    assert isinstance(documents, list)
    retrieved = {str(d) for d in documents}
    assert "duplicate-charge-sop.md" in retrieved, f"retrieved: {sorted(retrieved)}"


# ---------------------------------------------------------------------------
# run_dataset -- every case gets a fresh database, and a bad case does not abort
# ---------------------------------------------------------------------------


@pytest.mark.slow
async def test_run_dataset_runs_every_case_with_its_own_run(tmp_path: Path) -> None:
    """A dataset runs end to end; every case gets a distinct run id."""
    tiny = tmp_path / "classification.jsonl"
    tiny.write_text(
        "\n".join(
            json.dumps(case)
            for case in [
                {
                    "id": "c1",
                    "input": "We were charged twice.",
                    "expected_category": "duplicate_charge",
                },
                {"id": "c2", "input": "I cannot log in.", "expected_category": "account_access"},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    results = await run_dataset(tiny, provider_name="fake", dataset_dir=tmp_path)
    assert [r["case_id"] for r in results] == ["c1", "c2"]
    run_ids = [r["run_id"] for r in results]
    assert all(run_ids), "every case must have produced a run"
    assert len(set(run_ids)) == len(run_ids), "cases must not share a run"


def test_the_committed_corpus_is_present() -> None:
    """The runner indexes a real corpus; an absent one would score every recall 0."""
    assert KNOWLEDGE_DIR.is_dir()
    assert any(KNOWLEDGE_DIR.glob("*.md"))


# -- provider construction is a second assembly point ------------------------


def test_the_runner_passes_the_gateway_settings_to_the_anthropic_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_build_provider`` must forward every setting the adapter accepts.

    Caught live, not by reading: an eval pointed at a local gateway failed every
    classification with ``StructuredOutputError: not valid JSON`` because the
    runner constructed ``AnthropicModelProvider`` with neither ``base_url`` nor
    ``structured_output`` — so it reached the real Anthropic endpoint and used
    the default mechanism, ignoring the deployment's entire configuration.

    ``worker/__main__.py`` had been updated for exactly this and the runner had
    not. Two assembly points for one adapter, and only one was checked — the same
    shape as the two ``response_format`` spellings in the OpenAI adapter, and as
    the ``build_stores`` call that never existed.

    Asserted against the constructed object rather than a spy, so a rename in the
    adapter surfaces here as an AttributeError instead of a silently unset value.
    """
    from opspilot.evals import runner
    from opspilot.settings import get_settings

    monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("ANTHROPIC_STRUCTURED_OUTPUT", "tool")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "probe-key-not-real")
    monkeypatch.setenv("MODEL_NAME", "some-model-label")
    get_settings.cache_clear()

    built = runner._build_provider("anthropic")
    assert isinstance(built, AnthropicModelProvider), (
        f"the runner built {type(built).__name__}, not an AnthropicModelProvider"
    )
    provider = built

    assert provider._base_url == "http://127.0.0.1:9999", (
        "the runner did not forward ANTHROPIC_BASE_URL; a deployment pointing at "
        "a gateway would silently talk to api.anthropic.com instead"
    )
    assert provider._structured_output == "tool", (
        "the runner did not forward ANTHROPIC_STRUCTURED_OUTPUT; the deployment's "
        "chosen mechanism was ignored and the default was used"
    )
    assert provider._model_name == "some-model-label", (
        "the configured model name did not reach the adapter. Note it is a label: "
        "a gateway may route it anywhere, and the reported name is what counts"
    )


def test_the_runner_and_the_worker_build_the_anthropic_provider_the_same_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two assembly points must not drift apart again.

    They are separate functions because the worker reads a deployment's
    environment and the runner reads the eval's, but they construct the same
    adapter from the same settings. Asserting the field sets match is what stops
    one gaining a parameter the other never learns about — which is exactly how
    this defect happened.
    """
    import inspect

    from opspilot.adapters.models.anthropic_provider import AnthropicModelProvider
    from opspilot.evals import runner
    from opspilot.worker import __main__ as worker_main

    accepted = set(inspect.signature(AnthropicModelProvider.__init__).parameters) - {
        "self",
    }

    worker_src = inspect.getsource(worker_main.build_worker_provider)
    runner_src = inspect.getsource(runner._build_provider)

    # Every parameter the adapter accepts, other than the key and timeout which
    # both sites pass positionally-by-name anyway, must appear at both sites.
    for name in sorted(accepted - {"api_key", "timeout_seconds"}):
        assert name in worker_src, f"the worker does not pass {name!r}"
        assert name in runner_src, (
            f"the runner does not pass {name!r}, which the adapter accepts. The "
            "two construction sites have drifted; a deployment setting "
            f"{name} would be honoured by one and ignored by the other."
        )


def test_the_run_row_records_a_real_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """No run row may name its model "eval".

    Found by reading the results file rather than the code: `_reported_model`
    reported agreement between the file's model field and the `model_called`
    events, and both were wrong -- the runner wrote the literal ``"eval"`` into
    every run row, and the file's field had read it back.

    That mattered beyond tidiness. ``RunDetail.model_name`` exists to say what
    produced a run; a placeholder makes it useless for exactly that, and it is
    what a reader would see in the dashboard for any run the eval harness
    created. The agreement check could not see it because it compared two
    copies of the same placeholder.

    Asserted through the real ``run_case`` path rather than by grepping, so the
    guard holds if the value moves.
    """
    from opspilot.evals import runner as module
    from opspilot.settings import get_settings

    monkeypatch.setenv("MODEL_NAME", "a-configured-label")
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    get_settings.cache_clear()

    assert module._configured_model_name("anthropic") == "a-configured-label"

    monkeypatch.setenv("MODEL_NAME", "")
    get_settings.cache_clear()
    assert module._configured_model_name("anthropic") == "claude-sonnet-5-5"
    assert module._configured_model_name("fake") == "fake-1"

    # The literal that used to be written, asserted absent from the source that
    # writes run rows. A grep, because the failure is a constant reappearing.
    import inspect

    src = inspect.getsource(module)
    assert 'model_name="eval"' not in src, (
        'the runner writes model_name="eval" again; that placeholder reaches '
        "RunDetail.model_name and any results file built from it"
    )
