"""Worker layer: the process that claims and drives runs.

Responsibility: a separate process -- *not* FastAPI ``BackgroundTasks`` -- that
claims claimable runs from the run table and drives them through the runtime.
The run table is the queue; ``FOR UPDATE SKIP LOCKED`` is the only queueing
primitive Phase 1 needs (no Redis, no Celery, no RabbitMQ, no Temporal).

Layer: ``worker``. Sits at the same level as ``api`` and may import ``domain``,
``ports`` and ``adapters``. See ``docs/architecture.md`` §5.
"""

from __future__ import annotations
