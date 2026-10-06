"""The SQLite vector store is documented as in-memory, and it is.

What was found by running the real system (not by a test)
----------------------------------------------------------

``adapters/wiring.py::_build_vector_store`` returns ``InMemoryVectorStore``
whenever ``settings.is_sqlite``. That store keeps the chunk *embeddings* in the
process's memory (``self._embeddings``) and writes the ``knowledge_chunks`` rows
with ``embedding`` NULL. A real deployment runs two processes -- the API serves
``POST /api/knowledge/reindex``, the worker retrieves -- so the API's reindex
loads vectors into the *API's* memory and the worker's memory is empty.
Retrieval returns **zero hits on every SQLite deployment**: the run abstains,
escalates, no tool is called, no refund is proposed, and the reply still claims
a resolution nothing performed.

The suite could not see it because ``tests/agent/_golden_harness.py`` builds the
API and the worker in one process, sharing a store a deployment does not share.

The decision (ADR-0004, the correction recorded there): production is PostgreSQL
+ pgvector; SQLite is a *tests-only* path. The fix is not to make SQLite work --
it is to stop the documents implying it does, and to keep that claim from
drifting back. This is the guard that keeps the claim honest, in the register of
``test_threshold_has_one_definition.py`` and ``test_dataset_vocabulary.py``: it
reads a *claim out of a document* and asserts a *relationship* against the code,
rather than snapshotting today's sentence.

What the guard asserts
----------------------

1. The documents that an operator meets -- ``docs/limitations.md``,
   ``.env.example``, ``docs/adr/0004`` -- say, in the SQLite context, that the
   store is in-memory / per-process. The extractor reads for that meaning; a
   rewording that dropped it would fail this test, not pass it vacuously.

2. ``build_vector_store`` on a SQLite settings object actually returns
   ``InMemoryVectorStore``. That is the relationship: *documented in-memory* and
   *actually in-memory* must agree. If either side moves without the other, the
   test goes red and names both sides.

3. The thing that actually breaks: a *second* store built from the same SQLite
   session factory -- the nearest cheap model of "a second process" -- sees
   **zero** chunks after the first store upserted. The embedding data lives in
   the first object's memory and nowhere the database can hand to a new process.
"""

from __future__ import annotations

import pathlib
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db, models
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.persistence.repositories import SqlKnowledgeDocumentStore
from opspilot.adapters.retrieval.memory_store import InMemoryVectorStore
from opspilot.adapters.wiring import _build_vector_store
from opspilot.ports.vector_store import ChunkRecord
from opspilot.settings import Settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Documents that must carry the claim, and the meaning each must express: that
# the SQLite store is in-memory / per-process. The pattern is a disjunction of
# phrasings, so a reworded passage that *still says the thing* passes, while a
# passage that stops saying it (or is deleted) fails. This is deliberately a
# meaning check, not an equality check against a stored sentence.
_SQLITE_CLAIM_DOCUMENTS = (
    "docs/limitations.md",
    ".env.example",
    "docs/adr/0004-sqlite-tests-postgres-production.md",
)

# Each regex is *a* way to say "the SQLite store is in process memory and does
# not survive a process boundary". Held as a compiled-meaning test rather than a
# substring so the guard survives a copy-edit and still catches a semantic
# deletion.
_IN_MEMORY_PATTERNS = (
    "in process memory",
    "in-process",
    "in memory",
    "in-memory",
    "does not survive",
    "per-process",
)


def _sqlite_claims() -> dict[str, bool]:
    """Whether each document still says the SQLite store is in-memory.

    Reads the file and reports presence of the meaning, not of a frozen
    sentence. A document that stopped making the claim comes back ``False`` and
    the agreement test names it.

    Whitespace is collapsed before matching so a prose line-wrap (``in\\n#
    process memory``) does not defeat a phrase check -- the meaning survives the
    fold, and so should the extractor. A leading ``#`` on the wrapped line is
    dropped for the same reason: it is a comment marker between two words of one
    sentence, not a break in the sentence.
    """
    claims: dict[str, bool] = {}
    for relpath in _SQLITE_CLAIM_DOCUMENTS:
        path = REPO_ROOT / relpath
        assert path.is_file(), (
            f"{relpath} is missing; the SQLite in-memory claim has no home. If "
            "the limitation was resolved, update this guard too -- do not delete "
            "the document silently."
        )
        # Split on whitespace, drop bare comment markers, rejoin: "in\n# process
        # memory" becomes "in process memory".
        words = [word for word in path.read_text(encoding="utf-8").split() if word != "#"]
        text = " ".join(words).lower()
        claims[relpath] = any(pattern in text for pattern in _IN_MEMORY_PATTERNS)
    return claims


def _sqlite_settings() -> Settings:
    """A SQLite ``Settings``, the configuration the claim is about."""
    return Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")


def _fresh_factory(settings: Settings) -> sessionmaker[Session]:
    """A SQLite session factory with the schema created, as a test would build."""
    maker = db.session_factory(settings)
    Base.metadata.create_all(maker.kw["bind"])
    return maker


def _sqlite_store(factory: sessionmaker[Session], settings: Settings) -> object:
    """The store ``build_vector_store`` actually hands a SQLite deployment."""
    documents = SqlKnowledgeDocumentStore(factory)
    return _build_vector_store(settings, factory, documents)


def test_the_documents_claim_the_sqlite_store_is_in_memory() -> None:
    """Each document an operator meets still makes the claim.

    This is the "guard the guard" half: if a rewording (or a deletion) dropped
    the meaning, the agreement test below would pass vacuously -- it would find
    no claim to disagree with. An empty/false claim set has to fail *here*.
    """
    claims = _sqlite_claims()
    silent = sorted(relpath for relpath, claimed in claims.items() if not claimed)
    assert not silent, (
        f"these documents no longer say the SQLite store is in-memory: {silent}. "
        "Either the limitation was resolved -- in which case update this guard "
        "and the other documents together -- or a copy-edit dropped the meaning, "
        "in which case the guard below would silently stop checking anything."
    )


def test_the_documented_sqlite_behaviour_matches_the_wired_store() -> None:
    """Docs say in-memory; the code must hand SQLite an in-memory store.

    The relationship the whole file exists for. Two ways to go red:

    * ``build_vector_store`` starts returning a *persistent* store for SQLite --
      the limitation is gone, and the documents that still say otherwise are now
      wrong.
    * the SQLite path stops being ``InMemoryVectorStore`` while the documents
      still describe it as in-memory -- the same drift in the other direction.
    """
    settings = _sqlite_settings()
    factory = _fresh_factory(settings)
    store = _sqlite_store(factory, settings)

    documented_in_memory = all(_sqlite_claims().values())
    wired_in_memory = isinstance(store, InMemoryVectorStore)

    assert documented_in_memory == wired_in_memory, (
        "the documents and the code disagree about the SQLite vector store. "
        f"docs say in-memory: {documented_in_memory}; "
        f"build_vector_store returns {type(store).__name__} "
        f"(in-memory: {wired_in_memory}). If SQLite now gets a persistent store, "
        "the limitation is resolved and the documents must be updated; if it "
        "still gets InMemoryVectorStore, the documents must keep saying so."
    )
    # Pin the *positive* direction too, so a future refactor that made both
    # sides "not in-memory" without anyone noticing cannot pass by symmetry.
    assert wired_in_memory, (
        "build_vector_store no longer returns InMemoryVectorStore for SQLite. "
        "If that is deliberate, this is the finding that the limitation is gone "
        "-- update ADR-0004, docs/limitations.md and .env.example, then this "
        "guard. If it is not deliberate, the golden path on SQLite now depends "
        "on a store whose persistence properties nobody checked."
    )


async def test_a_second_process_sees_no_embeddings() -> None:
    """The break itself: embeddings do not survive a second process.

    A *second* store built from the same SQLite session factory -- which reads
    the same on-disk database a second process would -- sees zero chunks after
    the first store upserted. ``InMemoryVectorStore`` keeps vectors in
    ``self._embeddings`` (the database rows hold ``embedding`` NULL), so the
    data is in the first object's memory and nowhere a new process can reach.

    A truly separate OS process would be the strongest witness, but building one
    here would need a subprocess, a real on-disk URL and marshalled settings --
    disproportionate for a unit guard. A second store over the same factory is
    the nearest honest thing: it is exactly the object a second process
    constructs from the same database, and it is empty.

    Asserted as a *relationship* to the documented claim, not as a snapshot: the
    method alone cannot see the vectors, and this test is what makes "second
    process sees nothing" a property the suite enforces.
    """
    settings = _sqlite_settings()
    factory = _fresh_factory(settings)

    document_id = uuid4()
    # ``knowledge_chunks.document_id`` is a foreign key onto ``knowledge_documents``
    # (docs/data-model.md §2), so the chunk row needs its document to exist first.
    # Inserting it also *demonstrates* the split this limitation is about: the
    # document row is in the database and a second process can read it, while the
    # vector is not.
    with db.session_scope(factory) as session:
        session.add(
            models.KnowledgeDocument(
                id=document_id,
                title="Refund policy",
                source="refund-policy.md",
                content="the refund policy",
                doc_metadata={},
                content_hash="deadbeef",
            )
        )

    chunk = ChunkRecord(
        document_id=document_id,
        ordinal=0,
        anchor="a",
        heading_path="H",
        content="the refund policy",
        token_count=3,
    )

    first = _sqlite_store(factory, settings)
    assert isinstance(first, InMemoryVectorStore)
    await first.upsert([chunk], [[1.0, 0.0, 0.0]])

    # The database *does* hold the chunk metadata row -- the limitation is about
    # the vectors, not about the rows -- so a second process can see the chunk's
    # existence but not its embedding, which is exactly why retrieval finds zero.
    second = _sqlite_store(factory, settings)
    assert isinstance(second, InMemoryVectorStore)

    hits = await second.search([1.0, 0.0, 0.0], top_k=5)
    assert hits == [], (
        "a second InMemoryVectorStore built from the same SQLite database "
        f"returned {len(hits)} hit(s) after the first store upserted. This test "
        "asserts the *limitation*: on SQLite the embeddings live in one "
        "process's memory, so a second process (the worker, when the API "
        "reindexed) sees nothing and the golden path abstains. If this test now "
        "fails because a second process *can* see the vectors, the limitation is "
        "resolved -- remove this test and update ADR-0004, docs/limitations.md "
        "and .env.example."
    )
