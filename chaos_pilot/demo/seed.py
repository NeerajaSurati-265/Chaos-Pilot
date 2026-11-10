import logging
import threading
import time
from dataclasses import dataclass

from chaos_pilot.core.engine import ChaosEngine
from chaos_pilot.core.killswitch import KillSwitch
from chaos_pilot.core.pipeline import Pipeline
from chaos_pilot.demo.scenario import (
    DEMO_ABORT_REASON,
    DemoRestoreRule,
    DemoRollback,
    DemoScenario,
    DemoTimings,
)
from chaos_pilot.experiments.task_failure import ExperimentNotAllowed
from chaos_pilot.incidents.models import TERMINAL_STATES, IncidentState
from chaos_pilot.incidents.store import IncidentStore
from chaos_pilot.investigator.rule_based import RuleBasedInvestigator
from chaos_pilot.policy.engine import DEFAULT_RULES, PolicyEngine
from chaos_pilot.remediation.engine import RemediationEngine

logger = logging.getLogger(__name__)

BD, CC = "bad-deployment", "config-change"


@dataclass(frozen=True)
class Step:
    scenario: str
    variant: str = "success"
    answer: bool | None = None  # approve (True) / reject (False) when a human is asked


# Fixed plan: the outcomes come from the real pipeline reacting to these inputs.
DEFAULT_PLAN = (
    Step(BD), Step(CC, answer=True), Step(BD), Step(CC, answer=True),
    Step(BD, "verify_fail"), Step(BD), Step(CC, answer=False),
    Step(BD, "misdiagnosis"), Step(CC, answer=True), Step(BD, "remediation_fail"),
    Step(BD, "unclear"), Step(BD, "abort"), Step(BD),
)


class DemoTaskFailure:
    """Stands in for TaskFailureExperiment, whose constructor would create a boto3 client."""

    def stop_task(self, task_id: str):
        raise ExperimentNotAllowed("Not available in demo mode")


class DemoEngine(ChaosEngine):
    """Own store, own kill switch, own pipeline. Never shares state with a real engine."""

    demo = True

    def __init__(self, timings: DemoTimings | None = None):
        self.timings = timings or DemoTimings()
        self._local = threading.local()  # seeder-thread-only variant for the factories
        self.seeder: threading.Thread | None = None
        kill_switch = KillSwitch()
        executors = [DemoRollback(self.timings), DemoRestoreRule(self.timings)]
        pipeline = Pipeline(
            store=IncidentStore(),
            investigator=RuleBasedInvestigator(),
            policy=PolicyEngine(),
            remediation=RemediationEngine(executors, allowed_actions=DEFAULT_RULES),
            experiments_enabled=lambda: True,
            kill_switch=kill_switch,
        )
        super().__init__(
            task_failure=DemoTaskFailure(),
            pipeline=pipeline,
            scenario_factories={
                BD: lambda: self._scenario("bad_deployment"),
                CC: lambda: self._scenario("config_change"),
            },
            gate=lambda: True,
        )
        self.cluster, self.service = "demo-cluster", "demo-service"

    def _scenario(self, name: str) -> DemoScenario:
        variant = getattr(self._local, "variant", "success")
        return DemoScenario(name, variant, self.timings, self.kill_switch)


def build_demo_engine(timings: DemoTimings | None = None, seed: bool = True) -> DemoEngine:
    engine = DemoEngine(timings)
    if seed:
        engine.seeder = threading.Thread(
            target=seed_demo, args=(engine,), name="demo-seeder", daemon=True)
        engine.seeder.start()
    return engine


def _wait_until_idle(engine: DemoEngine, incident, step: Step, timeout: float):
    """Blocks until the run has finished (cleanup included). Approvals go through the real path."""
    timings = engine.timings
    answered = False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (not answered and step.answer is not None
                and incident.state is IncidentState.AWAITING_APPROVAL):
            timings.pause("approval_reaction")
            answered = engine.answer_approval(incident.id, step.answer)
        if incident.state in TERMINAL_STATES and engine.get_status()["status"] == "ready":
            return
        timings.sleep(timings.poll_interval)
        time.sleep(0)  # yield even when the injected sleep does nothing
    logger.warning("Demo seeding: %s did not finish within %.0f s", incident.id, timeout)


def _release_if_ours(engine: DemoEngine):
    """Release the switch only if the demo itself engaged it (its own reason string)."""
    ks = engine.kill_switch
    if ks.engaged and ks.reason == DEMO_ABORT_REASON:
        ks.release()
        return True
    return False


def seed_demo(engine: DemoEngine, plan=DEFAULT_PLAN, max_wait: float = 30.0,
              run_timeout: float = 60.0) -> dict:
    """Runs the plan one incident at a time. Returns what happened (for logs and tests)."""
    started, stopped_early = [], None
    for step in plan:
        deadline = time.monotonic() + max_wait
        while True:
            engine._local.variant = step.variant
            try:
                incident = engine.start_scenario(step.scenario, background=True)
                break
            except ExperimentNotAllowed as refusal:
                # A user's kill switch or run is in the way. Wait for it; never override it.
                if time.monotonic() >= deadline:
                    stopped_early = str(refusal)
                    logger.warning("Demo seeding stopped early: %s", stopped_early)
                    return {"started": started, "stopped_early": stopped_early}
                engine.timings.sleep(engine.timings.poll_interval)
                time.sleep(0.001)
            finally:
                engine._local.variant = "success"
        started.append(incident.id)
        _wait_until_idle(engine, incident, step, run_timeout)
        _release_if_ours(engine)
    return {"started": started, "stopped_early": None}
