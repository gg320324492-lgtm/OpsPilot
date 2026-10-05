"""Deprecated shim. Use ``opspilot.adapters.retrieval`` instead.

This package previously held the retrieval code. It now lives under
``opspilot.adapters.retrieval`` so that every concrete implementation of a port
sits in one place, and so the layering rule (adapters implement ports) holds
without exception.

Kept empty on purpose: nothing imports it, and it re-exports nothing. It exists
only so an old import path fails loudly at review rather than silently during a
demo. Delete it once nothing in the repository references it.
"""

from __future__ import annotations
