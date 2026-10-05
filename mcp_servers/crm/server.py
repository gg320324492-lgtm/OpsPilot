"""MCP server: ``crm`` -- customer, account and subscription reads.

Three ``READ``-permission tools over stdio, per ``docs/mcp-contracts.md`` S1.
They are pure: none of them writes, and the store is opened read-only in
spirit -- the CRM server never calls :meth:`Store.save`. It stands in for
Salesforce, so it answers questions about customers; it does not judge them.

Two things this module is careful about, because the generated JSON Schema is
the contract the agent reasons over:

1. **Every tool has a Pydantic return model.** ``structured_output=True``
   requires one -- a bare ``dict`` is rejected at registration
   (``docs/mcp-sdk-notes.md`` S3) -- and the model *is* the documented output
   shape, so a contract test can assert against it rather than against a
   hand-copied dict.
2. **Errors are results, never raised.** A raised exception becomes an opaque
   ``UnexpectedToolError`` with the code lost (``docs/mcp-sdk-notes.md`` S5), so
   ``not_found`` and ``validation_error`` are returned as fields on the result
   model. The agent can then distinguish "no such customer" from "you asked
   badly", which the refund workflow needs.

Seed data lives beside this module in ``seed.json`` and is loaded by the
``--reset`` flag and by the pytest fixture.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict

from mcp_servers._store import Record, Store

_SEED_PATH = Path(__file__).parent / "seed.json"

server: MCPServer = MCPServer(
    name="crm",
    version="0.1.0",
    instructions=(
        "Read-only customer records: customers, billing accounts and subscriptions. "
        "Look a customer up by either customer_email or customer_id, not both unless "
        "they agree. Errors are returned as a `code` field, never raised."
    ),
)

_store: Store = Store(seed_path=_SEED_PATH, filename="crm.json")


class _Base(BaseModel):
    """Shared config for every result model.

    ``extra="forbid"`` is deliberate: these models are small and their fields are
    the contract, so a typo in a field name should fail loudly in a test rather
    than silently adding an undocumented field to the agent's schema.
    """

    model_config = ConfigDict(extra="forbid")


class ErrorResult(_Base):
    """A structured error, returned instead of raising (``mcp-contracts`` S5).

    Attributes:
        code: Machine-readable reason. One of ``validation_error`` or
            ``not_found`` for this server. The agent branches on this.
        message: Human-readable detail. May be shown in a trace.
    """

    code: str
    message: str


class CustomerResult(_Base):
    """The result of ``crm.get_customer``.

    Exactly one of ``customer`` or ``error`` is set. ``error`` is present with a
    ``code`` of ``validation_error`` or ``not_found`` when the lookup fails, and
    ``customer`` is ``None`` in that case -- never a partial record, because a
    half-filled customer is how the wrong company gets refunded.

    Attributes:
        customer: The matched customer, or ``None`` on error.
        error: The structured error, or ``None`` on success.
    """

    customer: Customer | None = None
    error: ErrorResult | None = None


class Customer(_Base):
    """A CRM customer record."""

    customer_id: str
    company: str
    contact_name: str
    email: str
    plan: str
    status: str
    created_at: str


class AccountResult(_Base):
    """The result of ``crm.get_account``.

    Attributes:
        account: The billing account, or ``None`` on error.
        error: The structured error (``not_found``), or ``None`` on success.
    """

    account: Account | None = None
    error: ErrorResult | None = None


class BillingAddress(_Base):
    """A postal billing address."""

    country: str
    region: str


class Account(_Base):
    """A CRM billing account."""

    account_id: str
    customer_id: str
    billing_contact_email: str
    payment_terms: str
    currency: str
    billing_address: BillingAddress


class SubscriptionResult(_Base):
    """The result of ``crm.get_subscription``.

    Attributes:
        subscription: The subscription, or ``None`` on error.
        error: The structured error (``not_found``), or ``None`` on success.
    """

    subscription: Subscription | None = None
    error: ErrorResult | None = None


class Subscription(_Base):
    """A CRM subscription.

    ``monthly_amount`` is the number the golden-path duplicate charge is against
    (``docs/mcp-contracts.md`` S1): ``129.00`` for ACME's enterprise plan.
    """

    subscription_id: str
    customer_id: str
    plan: str
    seat_count: int
    monthly_amount: float
    currency: str
    status: str
    current_period_start: str
    current_period_end: str


def _customer_from(record: Record) -> Customer:
    return Customer.model_validate(record.model_dump())


@server.tool(name="crm.get_customer", structured_output=True)
def get_customer(
    customer_email: str | None = None,
    customer_id: str | None = None,
) -> CustomerResult:
    """Look up a customer by email or by id.

    Exactly one of ``customer_email`` / ``customer_id`` must be given. Passing
    neither, or passing both where they identify different customers, returns a
    ``validation_error`` result rather than guessing -- guessing which one the
    caller meant is how a wrong customer gets refunded. Passing both when they
    agree is accepted and returns the customer.

    Args:
        customer_email: The customer's billing email, e.g.
            ``"billing@acme.example"``.
        customer_id: The customer's id, e.g. ``"CUS-1001"``.

    Returns:
        A :class:`CustomerResult` with ``customer`` set on success, or ``error``
        set with a ``code`` of ``validation_error`` (neither identifier, or two
        that disagree) or ``not_found`` (no such customer).
    """
    if customer_email is None and customer_id is None:
        return CustomerResult(
            error=ErrorResult(
                code="validation_error",
                message="Provide exactly one of customer_email or customer_id.",
            )
        )

    by_email = (
        _store.find("customers", email=customer_email) if customer_email is not None else None
    )
    by_id = _store.find("customers", customer_id=customer_id) if customer_id is not None else None

    if (
        by_email is not None
        and by_id is not None
        and by_email.model_dump()["customer_id"] != by_id.model_dump()["customer_id"]
    ):
        return CustomerResult(
            error=ErrorResult(
                code="validation_error",
                message=(
                    f"customer_email {customer_email!r} and customer_id {customer_id!r} "
                    "identify different customers."
                ),
            )
        )

    record = by_id if by_id is not None else by_email
    if record is None:
        identifier = customer_email if customer_email is not None else customer_id
        return CustomerResult(
            error=ErrorResult(
                code="not_found",
                message=f"No customer matches {identifier!r}.",
            )
        )
    return CustomerResult(customer=_customer_from(record))


@server.tool(name="crm.get_account", structured_output=True)
def get_account(customer_id: str) -> AccountResult:
    """Return the billing account for a customer id.

    Args:
        customer_id: The customer's id, e.g. ``"CUS-1001"``. Required.

    Returns:
        An :class:`AccountResult` with ``account`` set on success, or ``error``
        set with a ``code`` of ``not_found`` when no account exists for that
        customer.
    """
    record = _store.find("accounts", customer_id=customer_id)
    if record is None:
        return AccountResult(
            error=ErrorResult(code="not_found", message=f"No account for customer {customer_id!r}.")
        )
    return AccountResult(account=Account.model_validate(record.model_dump()))


@server.tool(name="crm.get_subscription", structured_output=True)
def get_subscription(customer_id: str) -> SubscriptionResult:
    """Return the subscription for a customer id.

    Args:
        customer_id: The customer's id, e.g. ``"CUS-1001"``. Required.

    Returns:
        A :class:`SubscriptionResult` with ``subscription`` set on success, or
        ``error`` set with a ``code`` of ``not_found`` when the customer has no
        subscription.
    """
    record = _store.find("subscriptions", customer_id=customer_id)
    if record is None:
        return SubscriptionResult(
            error=ErrorResult(
                code="not_found", message=f"No subscription for customer {customer_id!r}."
            )
        )
    return SubscriptionResult(subscription=Subscription.model_validate(record.model_dump()))


def main() -> None:
    """Entry point for the ``crm`` stdio MCP server.

    With ``--reset`` the store is restored from the committed seed before
    serving, which is how the demo is made reproducible from the command line.
    """
    parser = argparse.ArgumentParser(description="OpsPilot CRM MCP server (stdio).")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Restore the store from seed.json before serving.",
    )
    args = parser.parse_args()
    if args.reset:
        _store.reset()
        print(f"crm: reset store from {_SEED_PATH}", file=sys.stderr)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
