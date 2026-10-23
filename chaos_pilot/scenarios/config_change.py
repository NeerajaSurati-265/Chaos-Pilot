import time
from typing import Callable

import boto3

from chaos_pilot.incidents.models import CheckResult, Signal, VerificationResult
from chaos_pilot.remediation.engine import RemediationContext
from chaos_pilot.remediation.executors import ingress_permission, restore_ingress_rule
from chaos_pilot.scenarios.base import Detection


class ConfigChangeScenario:
    """Removes the rule that lets the ALB reach the ECS tasks, so it can be restored.

    This mimics a bad network change. Restoring a security-group rule needs
    human approval under the default policy.
    """

    name = "config_change"

    def __init__(
        self,
        cluster: str,
        service: str,
        target_group_arn: str,
        ecs_security_group_id: str,
        alb_security_group_id: str,
        port: int = 80,
        ec2_client=None,
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
        self.ecs_security_group_id = ecs_security_group_id
        self.alb_security_group_id = alb_security_group_id
        self.port = port
        self.ec2 = ec2_client or boto3.client("ec2")
        self.ecs = ecs_client or boto3.client("ecs")
        self.elbv2 = elbv2_client or boto3.client("elbv2")
        self.detect_timeout = detect_timeout
        self.verify_timeout = verify_timeout
        self.poll_interval = poll_interval
        self.sleep = sleep
        self.clock = clock
        self.rule_removed = False

    # --- helpers -----------------------------------------------------------

    @property
    def _permission(self) -> dict:
        return ingress_permission(self.alb_security_group_id, self.port)

    def _rule_present(self) -> bool:
        group = self.ec2.describe_security_groups(
            GroupIds=[self.ecs_security_group_id]
        )["SecurityGroups"][0]
        for permission in group.get("IpPermissions", []):
            if (
                permission.get("IpProtocol") == "tcp"
                and permission.get("FromPort") == self.port
                and permission.get("ToPort") == self.port
                and any(
                    pair.get("GroupId") == self.alb_security_group_id
                    for pair in permission.get("UserIdGroupPairs", [])
                )
            ):
                return True
        return False

    def _target_health(self) -> dict[str, int]:
        response = self.elbv2.describe_target_health(
            TargetGroupArn=self.target_group_arn
        )
        counts: dict[str, int] = {}
        for target in response["TargetHealthDescriptions"]:
            state = target["TargetHealth"]["State"]
            counts[state] = counts.get(state, 0) + 1
        return counts

    def _service(self) -> dict:
        return self.ecs.describe_services(
            cluster=self.cluster, services=[self.service]
        )["services"][0]

    def _poll(self, check: Callable[[], bool], timeout: float) -> bool:
        deadline = self.clock() + timeout
        while True:
            if check():
                return True
            if self.clock() >= deadline:
                return False
            self.sleep(self.poll_interval)

    def _restore_rule(self):
        restore_ingress_rule(
            self.ec2, self.ecs_security_group_id, self.alb_security_group_id, self.port
        )
        self.rule_removed = False

    # --- scenario steps ----------------------------------------------------

    def inject(self) -> str:
        if not self._rule_present():
            raise RuntimeError(
                "The ALB-to-ECS rule is already missing; refusing to start "
                "from an unknown state"
            )
        self.ec2.revoke_security_group_ingress(
            GroupId=self.ecs_security_group_id,
            IpPermissions=[self._permission],
        )
        self.rule_removed = True
        return (
            f"Removed tcp/{self.port} from {self.alb_security_group_id} "
            f"on {self.ecs_security_group_id}"
        )

    def detect(self) -> Detection:
        latest: list[Signal] = []

        def incident_present() -> bool:
            nonlocal latest
            health = self._target_health()
            rule_present = self._rule_present()
            latest = [
                Signal("alb", "unhealthy_targets", health.get("unhealthy", 0)),
                Signal("alb", "healthy_targets", health.get("healthy", 0)),
                Signal("ec2", "alb_ingress_rule_present", rule_present),
                Signal("ec2", "security_group_id", self.ecs_security_group_id),
                Signal("ecs", "revision_changed", False),
            ]
            return health.get("unhealthy", 0) > 0

        found = self._poll(incident_present, self.detect_timeout)
        return Detection(incident_found=found, signals=latest)

    def remediation_context(self) -> RemediationContext:
        return RemediationContext(
            security_group_id=self.ecs_security_group_id,
            source_security_group_id=self.alb_security_group_id,
            port=self.port,
        )

    def verify(self) -> VerificationResult:
        checks: tuple[CheckResult, ...] = ()

        def recovered() -> bool:
            nonlocal checks
            health = self._target_health()
            service = self._service()
            desired = service["desiredCount"]
            checks = (
                CheckResult("alb_ingress_rule_present", self._rule_present()),
                CheckResult(
                    "running_tasks_match_desired",
                    service.get("runningCount") == desired,
                    f"{service.get('runningCount')}/{desired}",
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
        # Never leave the service cut off from the load balancer.
        if self.rule_removed or not self._rule_present():
            self._restore_rule()
