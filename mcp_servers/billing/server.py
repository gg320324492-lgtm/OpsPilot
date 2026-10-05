"""MCP server: ``billing`` -- invoices, transactions and the idempotent refund.

This is the server where correctness matters: it is the one that mutates state.
The tool surface (``docs/mcp-contracts.md`` S2):

- ``billing.get_invoice``       -- READ, pure. Unknown id is a ``not_found`` result.
- ``billing.list_transactions`` -- READ, pure. Reports the charged rows faithfully
  and never editorialises about which rows are a duplicate. The word "duplicate"
  does not appear in any output: that judgement belongs to the agent and the
  policy engine, and a tool that pre-decides the question makes the agent's
  reasoning untestable.
- ``billing.issue_refund``      -- HIGH_RISK_WRITE, mutates. ``idempotency_key``
  is REQUIRED (a parameter with no default, so the generated JSON Schema marks it
  required and the protocol rejects a call without one -- ``mcp-sdk-notes.md`` S4).
  The key is unique in the store's ``refunds`` collection, so a second call with
  the same key returns the existing ``refund_id`` with ``replayed: true`` and
  inserts no second row. The uniqueness lives in the store, not in application
  logic: the check-and-insert runs against the same in-memory document the store
  persists, so it holds even for two calls in the same process.

Errors are results, never exceptions. A raised exception becomes an opaque
``UnexpectedToolError`` with the code lost (``mcp-sdk-notes.md`` S5), which would
break the "already refunded -> do not refund again" scenario: the agent has to be
able to tell ``invalid_state`` from a generic failure, so every refusal is a
structured result carrying a ``code``.

Seed data lives beside this module in ``seed.json``; its top-level ``refunds``
list is the idempotency store the golden-path demo depends on.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from mcp_servers._store import Store

__all__ = ["create_server", "main", "server"]

_SEED_PATH = Path(__file__).parent / "seed.json"

# The refusal codes from ``docs/mcp-contracts.md`` S2. Spelled once as a literal
# union so an invalid code is a type error at the call site rather than a typo
# that only shows up in a string comparison in a test.
ErrorCode = Literal[
    "validation_error",
    "not_found",
    "invalid_state",
    "amount_exceeds_transaction",
]


# ---------------------------------------------------------------------------
# Return models
#
# Every tool declares a Pydantic return model and registers with
# ``structured_output=True``: a bare ``dict`` return fails at registration time
# (``mcp-sdk-notes.md`` S3). The models are the contract the agent reasons over,
# so errors carry the same shape as successes plus a ``code``.
# ---------------------------------------------------------------------------


class LineItem(BaseModel):
    """One invoice line item."""

    description: str
    amount: float


class InvoiceResult(BaseModel):
    """Result of ``billing.get_invoice``.

    On success the invoice fields are populated and ``code`` is ``None``. On
    failure only ``code`` is set (``not_found``), and every invoice field is
    ``None``/empty so a caller cannot mistake a refusal for a record.
    """

    code: ErrorCode | None = None
    invoice_id: str | None = None
    customer_id: str | None = None
    issued_at: str | None = None
    due_at: str | None = None
    currency: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    total: float | None = None
    amount_paid: float | None = None
    status: str | None = None


class TransactionLine(BaseModel):
    """One transaction row, reported verbatim from the store."""

    transaction_id: str
    invoice_id: str
    amount: float
    currency: str
    status: str
    processed_at: str
    payment_method: str
    refund_id: str | None = None


class TransactionsResult(BaseModel):
    """Result of ``billing.list_transactions``.

    Deliberately carries **no** field naming or hinting at a duplicate -- no
    ``is_duplicate``, no ``suspected_duplicate``, no ``duplicate_of``. The server
    reports the rows and their total; the comparison is the agent's job.
    """

    code: ErrorCode | None = None
    transactions: list[TransactionLine] = Field(default_factory=list)
    total_charged: float = 0.0


class RefundResult(BaseModel):
    """Result of ``billing.issue_refund``.

    On success ``refund_id`` and friends are populated and ``replayed`` says
    whether this call created the refund (``False``) or returned an existing one
    for the same idempotency key (``True``). The flag is what keeps a replayed
    refund from looking identical to a fresh one in the run's audit.
    """

    code: ErrorCode | None = None
    refund_id: str | None = None
    transaction_id: str | None = None
    amount: float | None = None
    currency: str | None = None
    status: str | None = None
    idempotency_key: str | None = None
    created_at: str | None = None
    replayed: bool = False


# ---------------------------------------------------------------------------
# Helpers over the store document
# ---------------------------------------------------------------------------


def _now() -> str:
    """Return the current UTC time as an ISO-8601 ``Z`` string.

    Uses ``datetime.now(UTC)`` rather than ``utcnow()`` so the value is
    timezone-aware (ruff's DTZ rules require it) and matches the seed's format.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows(store: Store, name: str) -> list[dict[str, Any]]:
    """Return a top-level collection as mutable dicts.

    ``Store.collection`` returns frozen ``Record`` views, which are right for
    reads but cannot be mutated in place. A mutating tool edits the underlying
    document, so it works with the raw list through this helper. The list is
    returned by reference, not copied: appending to it mutates ``store.data``,
    which is what the subsequent ``store.save()`` persists. The seed's shape is
    fixed, so the list is taken as-is; ``Store.collection`` is where a malformed
    collection is caught.
    """
    return cast("list[dict[str, Any]]", store.data.get(name, []))


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def _get_invoice(store: Store, invoice_id: str) -> InvoiceResult:
    """Implement ``billing.get_invoice``."""
    row = store.find("invoices", invoice_id=invoice_id)
    if row is None:
        return InvoiceResult(code="not_found")
    extra: dict[str, Any] = row.model_extra or {}
    return InvoiceResult(
        invoice_id=row.model_dump().get("invoice_id"),
        customer_id=extra.get("customer_id"),
        issued_at=extra.get("issued_at"),
        due_at=extra.get("due_at"),
        currency=extra.get("currency"),
        line_items=[LineItem(**item) for item in extra.get("line_items", [])],
        total=extra.get("total"),
        amount_paid=extra.get("amount_paid"),
        status=extra.get("status"),
    )


def _list_transactions(store: Store, invoice_id: str) -> TransactionsResult:
    """Implement ``billing.list_transactions``."""
    lines = [
        TransactionLine.model_validate(row.model_dump())
        for row in store.collection("transactions")
        if (row.model_extra or {}).get("invoice_id") == invoice_id
    ]
    # ``total_charged`` sums only the rows still in ``charged`` status. A
    # refunded row is reported (the agent must be able to see that one of the
    # two charges was already refunded) but it no longer represents money
    # currently held, so it is excluded from the total. This is a factual sum,
    # not a judgement: it does not say the remaining rows are duplicates.
    total = sum(line.amount for line in lines if line.status == "charged")
    return TransactionsResult(transactions=lines, total_charged=round(total, 2))


def _issue_refund(
    store: Store,
    *,
    transaction_id: str,
    amount: float,
    idempotency_key: str,
    reason: str | None,
) -> RefundResult:
    """Implement ``billing.issue_refund``, including the idempotency guarantee.

    Order of checks matters and is the contract's:

    1. Missing/blank key or non-positive amount -> ``validation_error``.
    2. Unknown transaction -> ``not_found``.
    3. Existing refund for this key -> return it with ``replayed: true`` (before
       the state check, because a replay of a *successful* refund must return the
       original, and re-checking status would wrongly report ``invalid_state`` on
       the transaction the first call just moved to ``refunded``).
    4. Transaction status not ``charged`` -> ``invalid_state``.
    5. Amount greater than the transaction amount -> ``amount_exceeds_transaction``.

    On success the transaction is moved to ``refunded``, a refund row is
    appended, the ``next_refund_id`` counter increments, and one ``save()``
    persists both writes together.
    """
    if not idempotency_key.strip():
        return RefundResult(code="validation_error", transaction_id=transaction_id)
    if amount <= 0:
        return RefundResult(code="validation_error", transaction_id=transaction_id)

    # Idempotency first: the store is the authority. A replay returns the
    # recorded refund unchanged and never looks at the transaction again.
    for refund in _rows(store, "refunds"):
        if refund.get("idempotency_key") == idempotency_key:
            return RefundResult(
                refund_id=refund.get("refund_id"),
                transaction_id=refund.get("transaction_id"),
                amount=refund.get("amount"),
                currency=refund.get("currency"),
                status="refunded",
                idempotency_key=idempotency_key,
                created_at=refund.get("created_at"),
                replayed=True,
            )

    transaction: dict[str, Any] | None = None
    for candidate in _rows(store, "transactions"):
        if candidate.get("transaction_id") == transaction_id:
            transaction = candidate
            break
    if transaction is None:
        return RefundResult(code="not_found", transaction_id=transaction_id)

    if transaction.get("status") != "charged":
        return RefundResult(code="invalid_state", transaction_id=transaction_id)

    transaction_amount = float(transaction.get("amount", 0.0))
    if amount > transaction_amount:
        return RefundResult(code="amount_exceeds_transaction", transaction_id=transaction_id)

    number = int(store.data.get("next_refund_id", 10091))
    refund_id = f"REF-{number}"
    created_at = _now()
    currency = str(transaction.get("currency", "USD"))

    store.data["next_refund_id"] = number + 1
    _rows(store, "refunds").append(
        {
            "refund_id": refund_id,
            "transaction_id": transaction_id,
            "amount": amount,
            "currency": currency,
            "idempotency_key": idempotency_key,
            "reason": reason,
            "created_at": created_at,
        }
    )
    transaction["status"] = "refunded"
    transaction["refund_id"] = refund_id
    store.save()

    return RefundResult(
        refund_id=refund_id,
        transaction_id=transaction_id,
        amount=amount,
        currency=currency,
        status="refunded",
        idempotency_key=idempotency_key,
        created_at=created_at,
        replayed=False,
    )


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


def create_server(store: Store | None = None) -> MCPServer:
    """Build the billing MCP server, wiring the tools to ``store``.

    Args:
        store: The backing store. A fixture or test injects an isolated one
            (seeded from the committed ``seed.json`` into ``tmp_path``); when
            omitted the server builds the default store, which honours
            ``OPSPILOT_MCP_DATA_DIR``. Passing the store in is what keeps the
            tests hermetic without an env-var dance, and it is how a test can
            inspect the document the tools actually mutated.
    """
    backing = store if store is not None else Store(_SEED_PATH, filename="billing.json")
    mcp = MCPServer(name="billing")

    # `name=` is REQUIRED on every tool. The SDK defaults the registered name to
    # the bare function name, so `@mcp.tool()` alone would register `get_invoice`
    # while the contract (docs/mcp-contracts.md) and TOOL_REGISTRY both specify
    # `billing.get_invoice`. Gate 2 of the permission model is a registry lookup
    # keyed on the contracted name, so a bare name cannot be permission-checked.
    # tests/integration/test_mcp_contract_names.py compares these against the
    # contract document rather than against the code.
    @mcp.tool(
        name="billing.get_invoice", structured_output=True, description="Fetch one invoice by id."
    )
    def get_invoice(invoice_id: str) -> InvoiceResult:
        return _get_invoice(backing, invoice_id)

    @mcp.tool(
        name="billing.list_transactions",
        structured_output=True,
        description=(
            "List the transactions recorded against an invoice, verbatim. "
            "Reports rows and their total; makes no judgement about duplicates."
        ),
    )
    def list_transactions(invoice_id: str) -> TransactionsResult:
        return _list_transactions(backing, invoice_id)

    @mcp.tool(
        name="billing.issue_refund",
        structured_output=True,
        description=(
            "Refund a transaction. Idempotent on idempotency_key: the same key "
            "returns the same refund with replayed=true and creates no second refund."
        ),
    )
    def issue_refund(
        transaction_id: str,
        amount: float,
        idempotency_key: str,
        reason: str | None = None,
    ) -> RefundResult:
        return _issue_refund(
            backing,
            transaction_id=transaction_id,
            amount=amount,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    return mcp


# A module-level server so ``python -m mcp_servers.billing.server`` and the
# subprocess test have something to start without constructing one by hand.
server = create_server()


def main() -> None:
    """Entry point for the ``billing`` stdio MCP server.

    ``--reset`` reloads the committed seed before serving, so a demo run always
    starts from the same data.
    """
    if "--reset" in sys.argv[1:]:
        server_store = Store(_SEED_PATH, filename="billing.json")
        server_store.reset()
        server_store.save()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
