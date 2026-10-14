import os
import threading

from chaos_pilot.core.pipeline import Pipeline
from chaos_pilot.experiments.task_failure import (
    ExperimentNotAllowed,
    TaskFailureExperiment,
)
from chaos_pilot.incidents.models import Incident
from chaos_pilot.incidents.store import IncidentStore
from chaos_pilot.investigator.rule_based import RuleBasedInvestigator
from chaos_pilot.policy.engine import PolicyEngine
from chaos_pilot.scenarios.bad_deployment import BadDeploymentScenario

DEFAULT_CLUSTER_NAME = "chaos-pilot-cluster"
DEFAULT_SERVICE_NAME = "chaos-pilot-service"


def experiments_enabled() -> bool:
    # Kill switch: experiments are refused unless explicitly enabled.
    return os.getenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", "false").lower() == "true"


class ChaosEngine:

    def __init__(
        self,
        task_failure: TaskFailureExperiment | None = None,
        pipeline: Pipeline | None = None,
        scenario_factories: dict | None = None,
    ):
        self.cluster = os.getenv("CHAOS_PILOT_CLUSTER", DEFAULT_CLUSTER_NAME)
        self.service = os.getenv("CHAOS_PILOT_SERVICE", DEFAULT_SERVICE_NAME)
        self.target_group_arn = os.getenv("CHAOS_PILOT_TARGET_GROUP_ARN", "")

        self.task_failure = task_failure or TaskFailureExperiment(
            cluster_name=self.cluster,
            service_name=self.service
        )
        self.store = IncidentStore()
        self.pipeline = pipeline or Pipeline(
            store=self.store,
            investigator=RuleBasedInvestigator(),
            policy=PolicyEngine(),
            experiments_enabled=experiments_enabled,
        )
        self.store = self.pipeline.store
        self.scenario_factories = scenario_factories or {
            "bad-deployment": self._bad_deployment,
        }
        # Only one experiment runs at a time against the test service.
        self._running = threading.Lock()

    def _bad_deployment(self):
        if not self.target_group_arn:
            raise ExperimentNotAllowed("CHAOS_PILOT_TARGET_GROUP_ARN is not set")
        return BadDeploymentScenario(
            cluster=self.cluster,
            service=self.service,
            target_group_arn=self.target_group_arn,
        )

    def get_status(self):
        return {
            "platform": "Chaos-Pilot",
            "status": "busy" if self._running.locked() else "ready",
            "experiments_enabled": experiments_enabled(),
            "scenarios": sorted(self.scenario_factories),
            "message": "Chaos engine is ready"
        }

    def run_task_failure(self, task_id: str):
        if not experiments_enabled():
            raise ExperimentNotAllowed(
                "Experiments are disabled. "
                "Set CHAOS_PILOT_EXPERIMENTS_ENABLED=true to allow them."
            )

        return self.task_failure.stop_task(task_id)

    def start_scenario(self, name: str, background: bool = True) -> Incident:
        if not experiments_enabled():
            raise ExperimentNotAllowed(
                "Experiments are disabled. "
                "Set CHAOS_PILOT_EXPERIMENTS_ENABLED=true to allow them."
            )
        factory = self.scenario_factories.get(name)
        if factory is None:
            raise ExperimentNotAllowed(f"Unknown scenario '{name}'")
        if not self._running.acquire(blocking=False):
            raise ExperimentNotAllowed("Another experiment is already running")

        try:
            scenario = factory()
            incident = self.pipeline.start(scenario)
        except Exception:
            self._running.release()
            raise

        def run():
            try:
                self.pipeline.run(incident, scenario)
            finally:
                self._running.release()

        if background:
            threading.Thread(target=run, daemon=True).start()
        else:
            run()
        return incident

    def answer_approval(self, incident_id: str, approved: bool) -> bool:
        return self.pipeline.answer_approval(incident_id, approved)
