"""Persistence adapters: SQLAlchemy engine, declarative models, repositories.

Responsibility: implement the store ports over SQLAlchemy 2.0 and handle the
Postgres/SQLite dialect differences. This is the only package that knows the ORM
exists -- ``domain/`` and ``agents/`` see plain Pydantic/dataclass objects.

Layer: ``adapters``. Implements ``opspilot.ports.stores``. See
``docs/data-model.md``.
"""

from __future__ import annotations
