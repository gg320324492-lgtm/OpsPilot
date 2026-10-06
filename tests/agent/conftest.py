"""Fixtures for the M6 end-to-end agent tests.

The harness itself lives in ``_golden_harness.py``; this file only re-exports its
fixtures so pytest registers them for both ``test_golden_path.py`` and
``test_readme_scenarios.py``. Keeping the assembly in one importable module (and
the registration here) is what lets the two test files share a scenario setup
without either importing the other's fixtures.
"""

from __future__ import annotations

from tests.agent._golden_harness import factory, harness

__all__ = ["factory", "harness"]
