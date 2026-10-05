"""Placeholder: worker claim semantics under a real Postgres. Populated in M1."""

import pytest


@pytest.mark.postgres
@pytest.mark.skip(reason="M0 skeleton — implemented in M1")
def test_two_workers_cannot_claim_the_same_run() -> None: ...
