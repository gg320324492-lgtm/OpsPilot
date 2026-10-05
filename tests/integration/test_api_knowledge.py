"""The knowledge routes over a really-wired retrieval stack.

Written from ``docs/api-contract.md`` §1 and §9:

- ``GET /api/knowledge`` "List indexed documents and chunk counts".
- ``POST /api/knowledge/reindex`` "Idempotent -- documents are re-hashed and
  unchanged ones are skipped. Returns counts."

The app factory takes ``knowledge_store`` and ``reindex_runner``; both were
always ``None`` before M5c, so the routes only ever returned the empty/zero
fallback. These tests inject the real stack (``build_retrieval_stack``) over a
shared in-memory SQLite factory and assert the routes read real data -- the
listing is populated and the second reindex reports zero indexed documents.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from opspilot.adapters.persistence import db
from opspilot.adapters.persistence.models import Base
from opspilot.adapters.wiring import RetrievalStack, build_retrieval_stack
from opspilot.api.app import create_app
from opspilot.settings import Settings, get_settings
from tests.integration.fakes import FakeApprovalStore, FakeRunStore, FakeTicketStore

_TOKEN = "test-operator-token"  # noqa: S105 -- a test fixture value, not a credential
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}

# The corpus has 17 policy documents (knowledge/README.md lists them; README.md
# itself is excluded by ingest). Asserting the exact count makes the listing
# assertion non-vacuous: an empty page (the pre-M5c behaviour) fails it.
_EXPECTED_DOCUMENTS = 17


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """A sessionmaker over a fresh in-memory SQLite schema (ADR-0004)."""
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    session_factory = db.session_factory(settings)
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(engine)
    try:
        yield session_factory
    finally:
        engine.dispose()


@pytest.fixture
async def stack(factory: sessionmaker[Session]) -> RetrievalStack:
    """The real retrieval stack over the shared in-memory factory.

    Built but **not** ingested here: the reindex route is what ingests, and one
    test asserts the second call skips. The stack shares the test's factory so
    the routes read the schema the test created.
    """
    return build_retrieval_stack(
        Settings(DATABASE_URL="sqlite+pysqlite:///:memory:", EMBEDDING_PROVIDER="local"),
        session_factory=factory,
    )


@pytest.fixture
def client(stack: RetrievalStack, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A ``TestClient`` over an app wired with the real retrieval stack."""
    monkeypatch.setenv("OPSPILOT_OPERATOR_TOKEN", _TOKEN)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    get_settings.cache_clear()

    run_store = FakeRunStore()
    app = create_app(
        run_store=run_store,
        ticket_store=FakeTicketStore(),
        approval_store=FakeApprovalStore(run_store=run_store),
        knowledge_store=stack.knowledge_store,
        reindex_runner=stack.reindex_runner,
    )
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# GET /api/knowledge
# ---------------------------------------------------------------------------


def test_listing_is_populated_after_a_reindex(client: TestClient) -> None:
    """``GET /api/knowledge`` lists the corpus with chunk counts.

    ``docs/api-contract.md`` §1: the route lists "indexed documents and chunk
    counts". Before M5c the app bound no ``knowledge_store`` and the route always
    returned an empty page (``knowledge.py``); a populated listing is the proof
    the store is really wired.
    """
    reindex = client.post("/api/knowledge/reindex", headers=_AUTH)
    assert reindex.status_code == 200

    response = client.get("/api/knowledge", headers=_AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == _EXPECTED_DOCUMENTS, (
        f"expected {_EXPECTED_DOCUMENTS} documents in the listing, got {body['total']}"
    )
    sources = {item["source"] for item in body["items"]}
    assert "refund-policy.md" in sources
    assert "duplicate-charge-sop.md" in sources

    # A known SQLite-path limitation, asserted rather than hidden: the in-memory
    # vector store keeps chunks in process memory and never writes
    # ``knowledge_chunks`` rows (``memory_store.InMemoryVectorStore.upsert`` vs
    # ``pgvector_store.PgVectorStore.upsert``, which does write them). So on the
    # SQLite configuration the chunk counts are all 0 even though the documents
    # are indexed. On Postgres the counts are real. This is *not* something the
    # knowledge route can fix: it reads the table, and the table is empty because
    # no code writes it on this path.
    assert all(item["chunk_count"] == 0 for item in body["items"]), (
        "the SQLite path now writes knowledge_chunks rows; update this test and "
        "re-check `docs/data-model.md` §2's chunk_count expectation"
    )


def test_the_listing_needs_a_token(client: TestClient) -> None:
    """The operator surface is authenticated (``docs/api-contract.md`` §1)."""
    assert client.get("/api/knowledge").status_code == 401


# ---------------------------------------------------------------------------
# POST /api/knowledge/reindex  (idempotent -- docs/api-contract.md §9)
# ---------------------------------------------------------------------------


def test_reindex_is_idempotent(client: TestClient) -> None:
    """The second reindex indexes nothing, because content hashes are unchanged.

    ``docs/api-contract.md`` §9: reindex "is idempotent -- documents are re-hashed
    and unchanged ones are skipped. Returns counts". The first call indexes the
    corpus; the second reports ``documents_indexed == 0``, and ``documents_seen``
    stays the same because the tree did not change.
    """
    first = client.post("/api/knowledge/reindex", headers=_AUTH).json()
    second = client.post("/api/knowledge/reindex", headers=_AUTH).json()

    assert first["documents_seen"] == _EXPECTED_DOCUMENTS
    assert first["documents_indexed"] == _EXPECTED_DOCUMENTS
    assert first["chunks_written"] > 0

    assert second["documents_seen"] == _EXPECTED_DOCUMENTS
    assert second["documents_indexed"] == 0, (
        f"a second reindex of an unchanged tree indexed {second['documents_indexed']} "
        f"documents; docs/api-contract.md §9 says unchanged documents are skipped"
    )
    assert second["chunks_written"] == 0
