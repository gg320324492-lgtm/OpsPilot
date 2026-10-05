"""Domain layer: pure business logic.

Responsibility: the rules of OpsPilot expressed as value objects, enums and
deterministic functions -- run status and transitions, tool permissions and the
registry, approval decisions, policy evaluation, and the domain exception
hierarchy.

Layer: ``domain``. Dependencies point downward only (see
``docs/architecture.md`` §3). This package imports *only* the standard library
(``enum``, ``uuid``, ``datetime``, ``typing``) and Pydantic. It must never
import SQLAlchemy, FastAPI, MCP, LangGraph, httpx, or anything from
``opspilot.adapters`` -- the domain does not know those systems exist. A CI
import-linter check (M1) enforces this.
"""

from __future__ import annotations
