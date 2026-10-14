import boto3


class ExperimentNotAllowed(Exception):
    """Raised when an experiment targets something outside the allow-list."""


class TaskFailureExperiment:

    def __init__(self, cluster_name: str, service_name: str, ecs_client=None):
        self.cluster_name = cluster_name
        self.service_name = service_name
        self.ecs = ecs_client or boto3.client("ecs")

    def _ensure_task_is_allowed(self, task_id: str):
        # Only tasks that belong to the test service in the test cluster
        # may be stopped. Anything else is refused before any change is made.
        response = self.ecs.describe_tasks(
            cluster=self.cluster_name,
            tasks=[task_id]
        )

        tasks = response.get("tasks", [])
        if not tasks:
            raise ExperimentNotAllowed(
                f"Task {task_id} was not found in cluster {self.cluster_name}"
            )

        group = tasks[0].get("group")
        if group != f"service:{self.service_name}":
            raise ExperimentNotAllowed(
                f"Task {task_id} does not belong to service {self.service_name}"
            )

    def stop_task(self, task_id: str):
        self._ensure_task_is_allowed(task_id)

        response = self.ecs.stop_task(
            cluster=self.cluster_name,
            task=task_id,
            reason="Chaos-Pilot task failure experiment"
        )

        return {
            "task_id": task_id,
            "status": response["task"]["lastStatus"],
            "reason": response["task"].get("stoppedReason")
        }
