"""Claim lease: ``agent_runs.claimed_until`` and ``claimed_by``.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-13

The defect this migration serves, measured: ``claim_next`` claims with ``FOR
UPDATE SKIP LOCKED``, but that lock dies with its own ``SELECT`` -- the store
opens one transaction per call and commits it -- and the claim does not change
the run's status. So two workers whose polls do not overlap both receive the
same ``RECEIVED`` run, and both drive it. Thirty concurrent claim pairs on one
row returned it to both, thirty times out of thirty (``docs/limitations.md`` §2
and §8; commit 23d3ffe).

The fix is a **lease**, not a status change. A status change would have to make
``EXECUTING`` unclaimable, and it must not be: ``db.mark_interrupted_runs``
explains at length that an ``EXECUTING`` run is either one a worker is driving
*right now* or one a human approved before the worker died, and a single status
column cannot tell those apart. Sweeping the second kind discards a decision a
person made and strands an approved refund, which ``docs/milestones.md`` §M6
requires to resume. The lease distinguishes the two states a status column
cannot: **alive but busy** (lease in the future -- leave it alone) and **dead
and recoverable** (lease null or expired -- claimable again).

Two columns rather than one:

- ``claimed_until`` -- the moment the lease expires. ``claim_next`` takes a row
  only when this is NULL (never leased) or in the past, and sets it forward in
  the same transaction that reads the row.
- ``claimed_by`` -- the ``WORKER_ID`` of the holder. It is what makes **renewal**
  safe: a worker whose lease expired and was stolen must not be able to extend
  the thief's lease by renewing a run it no longer holds. Renewal is a
  conditional ``UPDATE ... WHERE id = ? AND claimed_by = ?``, which affects zero
  rows once somebody else holds the row. Without this column the lease alone is
  not sufficient, and that is stated rather than discovered.

**Existing rows get NULL in both columns**, which the claim predicate reads as
"never leased, claimable now". That is the only default that does not change the
meaning of a populated database: at upgrade time no worker is mid-claim under
the new code, and a run with no lease is exactly as claimable as it was under
0001. A non-NULL default -- "now", or an epoch -- would either strand a run for
one lease period or misdate a lease that was never taken. NULL says the truth:
this row has never been claimed under the lease.

The index is partial, over ``claimed_until``, matching the shape of
``ix_agent_runs_claimable``: it keeps the claim predicate's second clause (the
lease check) an index condition rather than a filter over every claimable row.
It is emitted on both dialects -- SQLite supports partial indexes, and the
predicate is a plain SQL comparison, so unlike ``SKIP LOCKED`` it needs no
dialect branch (ADR-0004).

A migration must be a frozen snapshot of history, so the predicate here is a
literal, not imported from ``models.py``; ``tests/integration/test_migration_schema.py``
asserts the migration's columns match the ORM after ``alembic upgrade head``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None



def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("claimed_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("claimed_by", sa.String(), nullable=True),
    )
    # Partial: only rows that carry a lease need this index, which is exactly the
    # set the lease clause consults. A full index would carry every terminal run
    # -- the overwhelming majority -- for nothing.
    op.create_index(
        "ix_agent_runs_claimed_until",
        "agent_runs",
        ["claimed_until"],
        postgresql_where=sa.text("claimed_until IS NOT NULL"),
        sqlite_where=sa.text("claimed_until IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_claimed_until", table_name="agent_runs")
    op.drop_column("agent_runs", "claimed_by")
    op.drop_column("agent_runs", "claimed_until")
