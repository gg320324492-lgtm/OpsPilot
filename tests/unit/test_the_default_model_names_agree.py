"""The two default-model tables must be the same table.

## Why this file exists

``src/opspilot/worker/__main__.py`` and ``src/opspilot/evals/runner.py`` each
carry a table of "the model this provider falls back to when the setting is
blank". The worker's comment says it "mirrors" the runner's, and the runner's
says it "mirrors" the worker's -- and each is a **separate copy** of the same
three lines.

That matters more than a duplicated constant usually does, because the value
reaches the record: the model name is written onto the run row, and an eval
results file is compared against it. A table that drifts in one place produces
a run that says it used ``claude-sonnet-5-5`` while the other says something
else, and neither file is wrong on its own.

The fix is not to share the constant across the layer boundary -- the runner
deliberately does not import the worker's module -- but to hold the two
copies to each other.

## What this file asserts

1. **The two tables are equal**, key for key and value for value.
2. **Every provider the settings accept has an entry.** A provider without a
   default falls back to its own name as a model name, which is a string that
   names no model.
"""

from __future__ import annotations

from opspilot.evals import runner
from opspilot.settings import Settings
from opspilot.worker import __main__ as worker_main


def test_the_two_default_model_tables_are_the_same_table() -> None:
    """The runner's and the worker's must not drift apart."""
    assert runner._DEFAULT_MODEL_NAMES == worker_main._DEFAULT_MODEL_NAMES, (
        "the eval runner and the worker disagree about the default model for a "
        f"provider: runner {runner._DEFAULT_MODEL_NAMES}, worker "
        f"{worker_main._DEFAULT_MODEL_NAMES}. The name is written onto the run "
        "row and into the results file, so the two copies have to agree."
    )


def test_every_provider_the_settings_accept_has_a_default_model() -> None:
    """A provider with no entry falls back to its own name as a model name."""
    accepted = Settings.model_fields["model_provider"].annotation
    # The annotation is a Literal; its arguments are the accepted values.
    providers = set(getattr(accepted, "__args__", ()))
    assert providers, "the model_provider setting does not name its providers"
    missing = sorted(providers - set(worker_main._DEFAULT_MODEL_NAMES))
    assert not missing, (
        f"no default model for {missing}. The fallback is the provider's own "
        "name, which names no model -- a run would record `anthropic` as the "
        "model that answered it."
    )
