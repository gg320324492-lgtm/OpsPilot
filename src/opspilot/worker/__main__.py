"""Worker process entry point.

Responsibility: the ``opspilot-worker`` console script and ``python -m
opspilot.worker``. Wires concrete adapters, marks interrupted runs on boot, then
runs the poll loop until a shutdown signal.

Layer: ``worker``.
"""

from __future__ import annotations


def main() -> None:
    """Run the worker until interrupted. M0 stub."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
