"""Approval note and context: the two columns the human half of gate 5 lacked.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-21

Two columns on ``approval_requests``, and neither of them is the model's
arguments:

- ``note`` -- the operator's own remark on a decision. The API accepts it, the
  port carries it, and the store threw it away: ``SqlApprovalStore.decide``
  took ``note`` as an argument and marked it ``# noqa: ARG002 -- part of the
  port signature; no column``. A remark an operator typed and saw accepted was
  never written anywhere, and the approval row is the only place it could
  have gone.
- ``context`` -- supplementary context for the approver (the company, the
  ticket's subject line).

Why ``context`` is a column and not an entry in ``arguments_snapshot``
-------------------------------------------------------------------

The obvious place for display-only data is the snapshot, which the API's
``_context_for`` already reaches into for a ``_context`` key. That key is
written by nothing: ``arguments_snapshot`` is filled from the model's tool
arguments, which gate 1 validates with ``extra="forbid"``, so it cannot carry
a key the model did not propose. Every approval the system creates therefore
had ``context: null``, and the contract's worked example
(``docs/api-contract.md`` §5) could not occur.

Putting it there would also be wrong. ``arguments_snapshot`` is the one thing
that means *"this is what the human approved and this is what will be
executed"*, and it is immutable once written -- ``docs/tool-permissions.md``
§3.2 says so, and ``ApprovalArgumentsChanged`` guards it. A second meaning
inside it would stop the snapshot equalling the executed arguments, which is
the invariant the whole approval gate rests on. So the context gets a column
of its own, and the snapshot stays exactly one thing.

Both columns are nullable and both default to NULL:

- ``note`` -- a decision may carry no remark, and a pending approval has none.
- ``context`` -- the context is assembled from data a run *may* never have
  fetched. A run that never called ``crm.get_customer`` still parks a
  valid approval, with no company to show.

A non-null default would be a lie: ``''`` is not "no remark" and ``{}`` is not
"no context". NULL says the truth, and it is what every row written before
this migration already has.

The JSON type is the same ``JSON``-on-SQLite / ``JSONB``-on-Postgres construct
the rest of the schema uses, frozen here as its own literal rather than
imported from ``models.py``: a migration must be a fixed point of history,
applied to two databases, producing the same schema today and in three years
(the reasoning is at ``_EMBEDDING_DIM`` in ``0001_initial_schema.py``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine[object]:
    """``JSONB`` on Postgres, ``JSON`` everywhere else.

    The same construct ``models.py`` uses for every other JSON column
    (``_JSON`` there is a ``with_variant`` for exactly this reason), so the
    migration and the ORM agree on the DDL by construction rather than by
    coincidence.
    """
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "approval_requests",
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.add_column(
        "approval_requests",
        sa.Column("context", _json_type(), nullable=True),
    )


def downgrade() -> None:
    # Reverse of the upgrade: the column added last is dropped first. On SQLite
    # this needs the batch form -- SQLite cannot ``ALTER TABLE ... DROP
    # COLUMN`` a column it would have to rewrite the table for, and Alembic's
    # batch mode does that copy itself.
    with op.batch_alter_table("approval_requests") as batch:
        batch.drop_column("context")
        batch.drop_column("note")
