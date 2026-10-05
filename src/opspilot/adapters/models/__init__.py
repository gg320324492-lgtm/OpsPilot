"""Model provider adapters: fake, anthropic, openai.

Responsibility: concrete ``ModelProvider`` implementations. The fake provider is
the default and is a first-class citizen -- the deterministic demo, the golden
path and the whole test suite run on it with no API key.

Layer: ``adapters``. The provider SDKs are optional extras: the core, the full
test suite and the demo run with *neither* installed. For this reason the
anthropic and openai adapters import their SDK **inside their methods**, not at
module top level, so importing this package never requires an SDK.
"""

from __future__ import annotations
