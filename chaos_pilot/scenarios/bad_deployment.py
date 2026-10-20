import time
from typing import Callable

import boto3

from chaos_pilot.incidents.models import CheckResult, Signal, VerificationResult
from chaos_pilot.scenarios.base import Detection

BAD_REVISION_ENV = {"name": "HEALTHY", "value": "false"}

# Fields describe_task_definition returns that register_task_definition rejects.
READ_ONLY_FIELDS = {
    "taskDefinitionArn", "revision", "status", "requiresAttributes",
    "compatibilities", "registeredAt", "registeredBy", "deregisteredAt",
}


class BadDeploymentScenario:
    """Deploys a revision whose health check fails, then rolls it back.

    The bad revision is the current task definition with HEALTHY=false added
    to the container environment, so no second image is needed.
    """

    name = "bad_deployment"

    def __init__(
        self,
        cluster: str,
        service: str,
        target_group_arn: str,
        ecs_client=None,
        elbv2_client=None,
        detect_timeout: float = 600,
        verify_timeout: float = 600,
        poll_interval: float = 15,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cluster = cluster
        self.service = service
        self.target_group_arn = target_group_arn
        self.ecs = ecs_client or boto3.client("ecs")
        self.elbv2 = elbv2_client or boto3.client("elbv2")
        self.detect_timeout = detect_timeout
        self.verify_timeout = verify_timeout
        self.poll_interval = poll_interval
        self.sleep = sleep
        self.clock = clock
        self.known_good_task_definition: str | None = None
        self.bad_task_definition: str | None = None

    # --- helpers -----------------------------------------------------------

    def _service(self) -> dict:
        response = self.ecs.describe_services(
            cluster=self.cluster, services=[self.service]
        )
        return response["services"][0]

    def _primary_deployment(self, service: dict) -> dict:
        return next(d for d in service["deployments"] if d["status"] == "PRIMARY")

    def _target_health(self) -> dict[str, int]:
        response = self.elbv2.describe_target_health(
            TargetGroupArn=self.target_group_arn
        )
        counts: dict[str, int] = {}
        for target in response["TargetHealthDescriptions"]:
            state = target["TargetHealth"]["State"]
            counts[state] = counts.get(state, 0) + 1
        return counts

    def _poll(self, check: Callable[[], bool], timeout: float) -> bool:
        deadline = self.clock() + timeout
        while True:
            if check():
                return True
            if self.clock() >= deadline:
                return False
            self.sleep(self.poll_interval)

    # --- scenario steps ----------------------------------------------------

    def inject(self) -> str:
        service = self._service()
        self.known_good_task_definition = service["taskDefinition"]

        current = self.ecs.describe_task_definition(
            taskDefinition=self.known_good_task_definition
        )["taskDefinition"]

        definition = {k: v for k, v in current.items() if k not in READ_ONLY_FIELDS}
        for container in definition["containerDefinitions"]:
            env = [e for e in container.get("environment", []) if e["name"] != "HEALTHY"]
            container["environment"] = env + [BAD_REVISION_ENV]

        registered = self.ecs.register_task_definition(**definition)
        self.bad_task_definition = registered["taskDefinition"]["taskDefinitionArn"]

        self.ecs.update_service(
            cluster=self.cluster,
            service=self.service,
            taskDefinition=self.bad_task_definition,
        )
        return f"Deployed unhealthy revision {self.bad_task_definition}"

    def _signals(self) -> list[Signal]:
        service = self._service()
        primary = self._primary_deployment(service)
        health = self._target_health()
        revision_changed = primary["taskDefinition"] != self.known_good_task_definition

        return [
            Signal("ecs", "current_task_definition", primary["taskDefinition"]),
            Signal("ecs", "previous_task_definition", self.known_good_task_definition),
            Signal("ecs", "revision_changed", revision_changed),
            Signal("ecs", "failed_tasks", primary.get("failedTasks", 0)),
            Signal("ecs", "rollout_state", primary.get("rolloutState")),
            Signal("alb", "unhealthy_targets", health.get("unhealthy", 0)),
            Signal("alb", "healthy_targets", health.get("healthy", 0)),
            # Old-revision tasks stay healthy during a rolling deploy, so the
            # new revision counts as healthy only once it is fully rolled out.
            Signal(
                "ecs",
                "new_revision_healthy_tasks",
                primary.get("runningCount", 0)
                if primary.get("rolloutState") == "COMPLETED"
                else 0,
            ),
        ]

    def detect(self) -> Detection:
        latest: list[Signal] = []

        def incident_present() -> bool:
            nonlocal latest
            latest = self._signals()
            values = {s.name: s.value for s in latest}
            return values["revision_changed"] and (
                values["failed_tasks"] > 0
                or values["unhealthy_targets"] > 0
                or values["rollout_state"] == "FAILED"
            )

        found = self._poll(incident_present, self.detect_timeout)
        return Detection(incident_found=found, signals=latest)

    def remediate(self, action: str) -> str:
        if action != "rollback_to_previous_revision":
            raise ValueError(f"{self.name} cannot run action '{action}'")
        if not self.known_good_task_definition:
            raise RuntimeError("No known-good revision was recorded before injection")

        self.ecs.update_service(
            cluster=self.cluster,
            service=self.service,
            taskDefinition=self.known_good_task_definition,
        )
        return f"Rolled back to {self.known_good_task_definition}"

    def verify(self) -> VerificationResult:
        checks: tuple[CheckResult, ...] = ()

        def recovered() -> bool:
            nonlocal checks
            service = self._service()
            primary = self._primary_deployment(service)
            health = self._target_health()
            desired = service["desiredCount"]
            checks = (
                CheckResult(
                    "running_known_good_revision",
                    primary["taskDefinition"] == self.known_good_task_definition,
                    primary["taskDefinition"],
                ),
                CheckResult(
                    "rollout_completed",
                    primary.get("rolloutState") == "COMPLETED",
                    str(primary.get("rolloutState")),
                ),
                CheckResult(
                    "running_tasks_match_desired",
                    primary.get("runningCount") == desired,
                    f"{primary.get('runningCount')}/{desired}",
                ),
                CheckResult(
                    "alb_targets_healthy",
                    health.get("healthy", 0) >= desired and set(health) == {"healthy"},
                    str(health),
                ),
            )
            return all(c.passed for c in checks)

        self._poll(recovered, self.verify_timeout)
        return VerificationResult(checks=checks)

    def cleanup(self) -> None:
        # Make sure the service is never left on the bad revision, then retire it.
        if self.known_good_task_definition:
            service = self._service()
            if service["taskDefinition"] != self.known_good_task_definition:
                self.ecs.update_service(
                    cluster=self.cluster,
                    service=self.service,
                    taskDefinition=self.known_good_task_definition,
                )
        if self.bad_task_definition:
            self.ecs.deregister_task_definition(taskDefinition=self.bad_task_definition)
