"""``LinearOrchestrator``: the graph-free step executor.

Responsibility: implement ``Orchestrator.step`` as a straight sequence -- no
graph, no library -- by dispatching to the stage functions the runtime owns
(``agents/runtime.py::classify_stage`` and its four siblings). It is a *thin*
dispatcher: it holds a ``StageDriver`` (the shared sequencer) and one call to
``step`` runs exactly one stage. It carries no stage logic of its own, so the
pump and this executor cannot drift -- there is one copy of the sequence, in
``StageDriver``.

Layer: ``adapters`` (orchestration). Implements
``opspilot.ports.orchestrator.Orchestrator``. It imports ``agents.runtime``, which
the layering allows: ``adapters`` sits above ``agents``, and the arrow is the
reason the agent runtime depends on the *port* rather than on this class.

Why the driver is attached rather than injected at construction
--------------------------------------------------------------
``Orchestrator.step`` takes only a ``run`` (``ports/orchestrator.py``) -- the
port does not carry the ``RunContext`` or the seven collaborators a stage needs,
and it must not: ``ports/`` cannot import ``agents/``. So a ``LinearOrchestrator``
built before the run exists (which is how every call site builds it -- the
golden-path harness, ``worker/__main__.py``, ``evals/runner.py``,
``scripts/demo_golden_path.py``) is bound to its driver at the moment the pump
starts, through :meth:`bind_driver`. ``run_loop`` no longer discards its
``orchestrator`` argument (``_ = orchestrator``): ``_pump`` hands it the driver
and loops ``step``, so this class is what drives a provider-mode run rather than
a boundary nothing runs through.

``RunParked`` is not caught here. The EXECUTE stage raises it at gate 5; it
propagates out of ``step`` untouched to the worker, which is the only place that
owns releasing the row (``worker/loop.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opspilot.domain.runs import AgentRun
from opspilot.ports.orchestrator import StepResult

if TYPE_CHECKING:
    # ``agents.runtime`` imports this module at run time; the annotation-only
    # import keeps the cycle broken while ``StageDriver`` is still typed.
    from opspilot.agents.runtime import StageDriver


class LinearOrchestrator:
    """The graph-free executor: one ``step`` call runs one runtime stage.

    Deterministic and dependency-free -- no graph, no library -- which is why it
    is the executor the tests and the golden-path harness use.
    """

    def __init__(self) -> None:
        self._driver: StageDriver | None = None

    def bind_driver(self, driver: StageDriver) -> None:
        """Attach the run's driver, so ``step`` has the context and collaborators.

        Called by ``agents/runtime.py::_pump`` at the start of a provider-mode
        run. Binding is not part of the ``Orchestrator`` port -- it is how the
        linear executor is given the state the port signature cannot carry, and
        the pump reaches it duck-typed so an orchestrator that does not need it
        (a test spy, or a future graph) is unaffected.
        """
        self._driver = driver

    async def step(self, run: AgentRun) -> StepResult:
        """Advance ``run`` by exactly one stage, dispatching to the runtime stage.

        The stage logic lives in ``StageDriver`` (which calls the extracted
        ``*_stage`` functions in ``agents/runtime.py``); this method exists to
        satisfy the port and to be the seam a test can spy on. A step that
        reaches the approval gate runs the EXECUTE stage, which raises
        ``RunParked`` -- that propagates out of here untouched, because parking
        is the workflow working, not a failure.
        """
        if self._driver is None:
            msg = (
                "LinearOrchestrator.step called before bind_driver; a run must be "
                "driven through the pump, which binds the driver"
            )
            raise RuntimeError(msg)
        return await self._driver.step(run)
