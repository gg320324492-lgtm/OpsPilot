"""The approvals API is read/decide only: no endpoint may *create* an approval.

An ``ApprovalRequest`` can only come from the runtime parking a run at gate 5
(``agents/runtime.py``'s ``_park_run``). The dashboard does not get to invent one,
and neither does a caller. The router therefore exposes exactly four routes: list
the inbox, get one, approve it, reject it -- and nothing else.

This matters as a security control. If ``POST /api/approvals`` existed, any caller
(with the operator token) could mint an "approved" row for a tool call that the
runtime had never parked, and the next time the worker resumed that run the
approval gate would read ``has_approved(call.id) is True`` and dispatch a
high-risk write with no human decision behind it. Pinning the route set to exactly
the four safe verbs means a new write endpoint has to be argued for in *this* test
rather than slipped in silently.

What this file deliberately does **not** assert:

* that approve/reject behave correctly -- ``tests/integration/test_api_approvals.py``
  covers the decision semantics, the 409s, and that approving executes no tool;
* that the routes are wired into the app -- ``test_api_approvals.py`` drives the
  ``TestClient`` end to end;
* that the endpoints are authenticated -- that is ``api/auth.py``'s ``require_operator``
  dependency, asserted elsewhere.

It asserts one thing: the *set of routes* the approvals router publishes.
"""

from __future__ import annotations

from opspilot.api.routers import approvals as approvals_router


def _route_set(router: object) -> set[tuple[str, str]]:
    """The ``(method, path)`` pairs a router publishes, as a set.

    A ``set`` rather than a list because the assertion is about *membership*, not
    order: the property under test is "these four routes and no others", and a set
    makes "and no others" a single ``==`` rather than four ``in`` checks plus a
    length guard. ``methods`` is a tuple of the HTTP verbs a route answers; a
    route with more than one verb (a future ``@router.api_route`` accepting both
    GET and POST, say) would still compare as one element.
    """
    routes = getattr(router, "routes", [])
    pairs: set[tuple[str, str]] = set()
    for route in routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path is None or methods is None:
            continue
        for method in sorted(methods):  # normalise so a set of one pair is stable
            pairs.add((method, path))
    return pairs


def test_the_approvals_router_exposes_only_list_get_approve_reject() -> None:
    """The approvals API publishes exactly four routes, all read/decide.

    The expected set is spelled out verbatim -- a future ``POST /api/approvals``
    would make this fail, which is the point: a write endpoint has to be added to
    this list by hand, forcing a human to justify it. No write verb (POST to the
    collection, PUT, DELETE, PATCH) may appear.
    """
    expected = {
        ("GET", "/api/approvals"),
        ("GET", "/api/approvals/{approval_id}"),
        ("POST", "/api/approvals/{approval_id}/approve"),
        ("POST", "/api/approvals/{approval_id}/reject"),
    }

    actual = _route_set(approvals_router.router)

    assert actual == expected

    # Explicitly: no creation endpoint. A POST to the collection would let a caller
    # fabricate an approval the runtime never parked -- the bypass the gate order
    # is designed to prevent.
    assert ("POST", "/api/approvals") not in actual

    # And no mutation verb on the collection or on a single approval.
    for method in ("PUT", "PATCH", "DELETE"):
        assert (method, "/api/approvals") not in actual
        assert (method, "/api/approvals/{approval_id}") not in actual
