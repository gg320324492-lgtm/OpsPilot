"""API layer: the FastAPI HTTP surface.

Responsibility: accept tickets, expose runs, tool calls, approvals and
knowledge, and serialize domain objects to JSON. It enqueues work -- it does not
do work: a request inserts a ticket and a ``RECEIVED`` run and returns 201
immediately, and a separate worker process drives the run.

Layer: ``api`` -- the top of the stack. It may import ``domain``, ``ports`` and
``adapters``; nothing imports it. See ``docs/architecture.md`` §5.
"""

from __future__ import annotations
