# ADR-0004 — SQLite for tests, PostgreSQL for production, one port between

**Status:** Accepted (Phase 1)

## Context

The target database is PostgreSQL 16 with pgvector. The test suite must run on a
developer machine and in CI without requiring a Postgres server for every
invocation — and in the current development environment Docker is not installed,
so "spin up a container" is not available locally.

Two options:

1. **Postgres everywhere.** Tests need a reachable server. Strongest fidelity;
   every test runs against exactly what ships. Requires an installed Postgres
   (or Docker) on every machine that runs tests, including the one this is being
   built on.
2. **SQLite for tests, Postgres for production.** Fast, zero-setup, fully
   deterministic tests. Costs fidelity in specific places.

The specification for this project says PostgreSQL only, with an explicit
instruction not to add Pinecone/Qdrant/Weaviate. SQLite is not a second
*datastore* in the sense that rule addresses. It is a second SQL dialect in the
test path, and that needs to be an explicit, bounded decision rather than an
accident.

**Correction (found by running the real system, not by testing it).** An earlier
draft of this ADR claimed "no data lives in SQLite in any deployment". That was
false in one place, and the way it was false breaks the golden path. On SQLite
`adapters/wiring.py::build_vector_store` returns `InMemoryVectorStore`, which
keeps the chunk *embeddings* in the process's memory (`self._embeddings`) and
writes the `knowledge_chunks` rows with `embedding` NULL. The embedder's output
is therefore a per-process fact, not a stored one. A real deployment runs two
processes — the API serves `POST /api/knowledge/reindex`, the worker performs
retrieval — so the API's reindex loads vectors into the **API's** memory and the
worker's memory is empty. Retrieval returns zero hits, the run abstains and
escalates, no tool is called, and the reply still claims a resolution nothing
performed. The suite could not see it because its harness builds both processes
in one process, over a store a deployment does not share.

## Decision

Tests run on SQLite in-memory by default. PostgreSQL runs in CI as a service
container and in Docker Compose for the real system.

Fidelity is preserved by confining the differences to four named places, each
with a test that covers the divergence:

| Divergence | Handling |
|---|---|
| `FOR UPDATE SKIP LOCKED` | SQLite has no equivalent. The worker's claim uses a dialect-aware construct; the two-worker concurrency test is `@pytest.mark.postgres` and runs only in CI. |
| pgvector `vector(1536)` column | Replaced by a JSON column in tests, with `ports/vector_store.py` providing an in-memory cosine implementation. A differential test loads the same chunks into both stores (in the Postgres CI job) and asserts identical top-5 results and scores to 4 dp. **This is not a clean swap.** The in-memory store keeps embeddings in process memory, so it is correct only in a *single* process: two processes over one SQLite database do not share vectors, and a deployment that reindexes in the API and retrieves in the worker gets zero hits. The differential test compares what the two stores *return* within one process and cannot see this; SQLite is a tests-only path (one process), not a supported runtime configuration. |
| `JSONB` | Maps to SQLite's `JSON`. Same SQLAlchemy type; no application-visible difference for the queries used. |
| Partial indexes, `gen_random_uuid()` | Emulated for SQLite; native in Postgres. The claim-predicate index exists only in Postgres by design — SQLite is single-threaded in tests anyway. |

The Docker Compose stack keeps Postgres and pgvector as the real configuration.
Nothing about the production path is SQLite-flavoured: `DATABASE_URL` selects the
dialect, and the only `if dialect ==` branches in the codebase are the four above.

## Consequences

**Good.** `pytest` works on a clean checkout with no services, which means the
whole suite — unit, integration, agent, security, eval-smoke — is runnable in
under a minute and by anyone who clones the repository. This is what makes the
"run the tests" instruction in the README true. CI still exercises the Postgres
path for migrations, the vector store and the concurrency claim.

**Cost, stated plainly.** Local development does not test the production
database. Three specific risks follow, and each has a named mitigation rather
than a hope:

1. *A pgvector query behaves differently from the Python cosine fallback.*
   Mitigated by the differential test; it runs in CI only.
2. *The `SKIP LOCKED` claim is broken.* Mitigated by the Postgres-marked
   concurrency test; it runs in CI only and is skipped locally. **The local
   `pytest` output prints the number of skipped postgres tests** so a developer
   can see what was not checked, rather than reading a clean run as full
   coverage.
3. *A migration is valid in SQLite but not Postgres.* Migrations are not run
   against SQLite at all — schema for tests is created via
   `Base.metadata.create_all`. The `verify` CI job runs `alembic upgrade head`
   against a real pgvector container on every push, so a broken migration cannot
   merge.

**Documentation consequence.** The README's "how to run tests" section says both
things: SQLite by default, and which tests that skips.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Postgres required for tests | Correct on fidelity; blocks the suite on a service the development machine does not have, and makes the first experience of the repository a setup failure. |
| Postgres via `pytest-postgresql` / testcontainers | Best of both on paper; both need a working Docker daemon, which is the thing that is absent. Revisit in Phase 2 when the deployment story exists. |
| SQLite in production too | Removes a dependency and loses `SKIP LOCKED`, real concurrency and pgvector. The concurrency and the vector search are both load-bearing. |
| An abstracted repository layer that hides the dialect entirely | The differences above are real and hiding them makes the fallback's behaviour unknowable. Named and tested beats abstracted and unexercised. |
