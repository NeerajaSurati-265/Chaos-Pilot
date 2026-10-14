import os

from chaos_pilot.experiments.task_failure import (
    ExperimentNotAllowed,
    TaskFailureExperiment,
)

DEFAULT_CLUSTER_NAME = "chaos-pilot-cluster"
DEFAULT_SERVICE_NAME = "chaos-pilot-service"


def experiments_enabled() -> bool:
    # Kill switch: experiments are refused unless explicitly enabled.
    return os.getenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", "false").lower() == "true"


class ChaosEngine:

    def __init__(self, task_failure: TaskFailureExperiment | None = None):
        self.task_failure = task_failure or TaskFailureExperiment(
            cluster_name=os.getenv("CHAOS_PILOT_CLUSTER", DEFAULT_CLUSTER_NAME),
            service_name=os.getenv("CHAOS_PILOT_SERVICE", DEFAULT_SERVICE_NAME)
        )

    def get_status(self):
        return {
            "platform": "Chaos-Pilot",
            "status": "ready",
            "experiments_enabled": experiments_enabled(),
            "message": "Chaos engine is ready"
        }

    def run_task_failure(self, task_id: str):
        if not experiments_enabled():
            raise ExperimentNotAllowed(
                "Experiments are disabled. "
                "Set CHAOS_PILOT_EXPERIMENTS_ENABLED=true to allow them."
            )

        return self.task_failure.stop_task(task_id)
