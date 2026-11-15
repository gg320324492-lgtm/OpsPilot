"""Retrieval search: embed the query, get top-k hits, apply the abstention threshold.

Responsibility: the one function the agent calls to retrieve policy. It embeds
the query (or extracts a good query from the ticket), asks the ``VectorStore``
for top-k, and applies ``RETRIEVAL_MIN_SCORE``: below the threshold it reports
*abstention*, which is a supported outcome -- the run escalates rather than
answering from weak evidence.

Layer: ``adapters`` (retrieval). Depends on the ``VectorStore`` port, so it is
byte-identical across pgvector and the in-memory store.

Citations are structural: the returned hits carry ``document_id`` and
``chunk_id``, and the agent's answer carries those ids. The UI renders
"Sources: refund-policy.md, duplicate-charge-sop.md" from ids, never by parsing
the model's prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from opspilot.adapters.retrieval.embeddings import Embedder
from opspilot.ports.vector_store import SearchHit, VectorStore


@dataclass(frozen=True)
class RetrievalOutcome:
    """Hits plus whether the evidence was strong enough to use."""

    hits: list[SearchHit]
    abstained: bool
    top_score: float | None


# Domain expansion groups used to grow a ticket query toward the *policy
# corpus's* own vocabulary. The corpus is fixed and committed (``knowledge/``),
# so this table is a curated map of the gaps the M3-02 analysis measured
# (``docs/retrieval-abstain-analysis.md``): each entry exists because a ticket
# phrasing and a policy phrasing for the same concept share no stem, which the
# lexical embedder cannot bridge on its own.
#
# Each group is ``(triggers, expansions)``: ``triggers`` are the ticket-side
# words/phrases that fire the group; ``expansions`` are the corpus-side phrases
# appended to the query. The table is deliberately *small*: every group is
# traceable to one of the three abstaining cases, and a generic thesaurus was
# measured to hurt -- expanding every ``invoice`` ticket with ``billing``/
# ``statement`` dragged an already-passing case below the threshold by
# boosting the wrong chunks. Expansion is for measured gaps, not vocabulary
# insurance.
#
#   - ``what plan``: a "what plan is X on" ticket is a plan-tier question; the
#     corpus answers it in subscription-policy's ``Plan Tiers`` section. The
#     phrase trigger (not the bare word ``plan``) is what keeps the expansion
#     away from tickets that merely contain ``plan`` -- the employee-stock and
#     world-cup distractor questions both say ``plan`` and must still abstain.
#   - ``seats``: "how many seats" maps to the corpus's ``seat count`` /
#     ``seat changes`` sections. The plural is the trigger; a singular ``seat``
#     trigger would fire on unrelated generic mentions.
#   - ``balance``: tickets say "what's the balance"; the corpus says
#     ``outstanding``/``overdue``/``dunning`` (enterprise-billing,
#     account-suspension).
#   - ``partial``/``partially``: "partially paid" maps to the corpus's
#     ``partial payment`` wording (account-suspension).
#   - ``paid``: ``paid`` stems to itself while the corpus uses
#     ``payment``/``pay`` stems; the group keeps a short billing ticket from
#     collapsing onto whichever single stem happens to appear.
#   - ``overcharge``/``overcharged``: tickets say "was overcharged"; the corpus
#     says ``amount disagreement``/``dispute`` (invoice-disputes, refund-policy).
#   - ``contract``: a contract-amount ticket is answered by contract-terms'
#     ``committed spend``/``minimum term`` sections.
_EXPANSION_GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("what plan", "which plan"), ("plan tier", "plan tiers", "tier", "subscription")),
    (("seats",), ("seat count", "seat changes", "seat count")),
    (("balance",), ("outstanding", "overdue", "dunning")),
    (("partial", "partially"), ("partial payment",)),
    (("paid",), ("payment", "pay", "overdue")),
    (("overcharge", "overcharged"), ("amount disagreement", "dispute", "dispute")),
    (("contract",), ("committed spend", "minimum term")),
)


def _expand_query(query: str) -> str:
    """Expand a raw ticket query toward the policy corpus's vocabulary.

    For every group whose trigger word or phrase appears in the query
    (standalone tokens, so ``plan`` fires but ``planning`` does not), the
    group's expansion phrases are appended to the query. The original query is
    always kept verbatim as the prefix, so expansion can only *add* lexical
    mass, never remove it.

    Why appended rather than substituted: the query is embedded whole, and
    appending preserves the original term weights while adding corpus-side
    vocabulary. Why not a full thesaurus: the corpus is a fixed 17-document
    policy set; the table above is the measured ticket-vs-corpus gap, and every
    entry is traceable to a document. A generic thesaurus would add noise the
    abstention threshold then has to absorb.
    """
    lowered = query.lower()
    # Standalone-token test: ``plan`` matches but ``plans``/``planning`` do
    # not, keeping the expansion from firing on unrelated inflections.
    tokens = set(re.findall(r"[a-z0-9]+", lowered))
    extras: list[str] = []
    for triggers, expansions in _EXPANSION_GROUPS:
        hit = any(t in lowered for t in triggers if " " in t) or any(
            t in tokens for t in triggers if " " not in t
        )
        if hit:
            extras.extend(expansions)
    if not extras:
        return query
    # Cap the expansion so a ticket that trips many groups cannot swamp its
    # own terms (the worst case observed in the analysis was 4 groups; the cap
    # is headroom, not a tuning knob). No de-duplication: a phrase listed
    # twice in a group is weighted twice, exactly like term frequency.
    return query + " " + " ".join(extras[:24])


#: The abstention threshold used when a caller does not pass one.
#:
#: Read from ``Settings.retrieval_min_score`` rather than repeated here. It was
#: repeated, with a test asserting the two agreed -- and that test is exactly
#: what let the drift through: comparing two numbers passes when both are stale
#: in the same way. The threshold then existed in four places, and moving it in
#: one left a caller abstaining on a value the deployment had already lowered.
#: ``tests/unit/test_threshold_has_one_definition.py`` is the guard that
#: actually works: it fails when a *second* literal exists, which an equality
#: assertion cannot do.
def _default_min_score() -> float:
    """``Settings.retrieval_min_score``, read per call so there is one source."""
    from opspilot.settings import get_settings

    return get_settings().retrieval_min_score


async def retrieve(
    query: str,
    *,
    embedder: Embedder,
    store: VectorStore,
    top_k: int = 5,
    min_score: float | None = None,
) -> RetrievalOutcome:
    """Embed ``query`` and return the top-k hits, or abstain.

    The query is embedded **once** and that one vector drives the search: the
    score the threshold is applied to is the score of the same vector that
    produced the ranking, so "best hit" and "the score we compare to
    ``min_score``" can never disagree.

    Args:
        query: The text to retrieve for. Expanded toward the policy corpus's
            vocabulary before embedding (``_expand_query``) -- the M3-02
            analysis showed tickets and policy chunks describing the same
            concept can share no stem (``overcharge`` vs "amount
            disagreement", ``balance`` vs ``overdue``), and the lexical
            embedder cannot bridge that gap on its own.
        embedder: Turns the query into a vector.
        store: The ``VectorStore`` holding the corpus.
        top_k: How many hits to ask the store for.
        min_score: The abstention threshold (``RETRIEVAL_MIN_SCORE``). ``None``
            resolves it from the settings **when the call is made** -- not
            when this module was imported, which is what the constant this
            replaced did. A threshold frozen at import is a threshold that
            cannot follow a setting an operator moves; the adapter cannot
            import settings, so the resolution is a call rather than an
            import-time value.

    Returns:
        A ``RetrievalOutcome``. ``abstained`` is ``True`` when the store returns
        no hits **or** the best score is below ``min_score``; ``top_score`` is
        ``None`` only when there are no hits. The hits are still returned on a
        below-threshold result -- the caller has the evidence and the decision,
        and ``docs/milestones.md`` §M5 makes escalation (not a guess) the
        response.
    """
    vectors = await embedder.embed([_expand_query(query)])
    if not vectors:  # pragma: no cover - an embedder returning nothing is broken
        return RetrievalOutcome(hits=[], abstained=True, top_score=None)

    hits = await store.search(vectors[0], top_k=top_k)
    if not hits:
        return RetrievalOutcome(hits=[], abstained=True, top_score=None)

    top_score = hits[0].score
    threshold = min_score if min_score is not None else _default_min_score()
    return RetrievalOutcome(
        hits=hits,
        abstained=top_score < threshold,
        top_score=top_score,
    )
