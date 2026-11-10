import random
import time
from dataclasses import dataclass, field
from typing import Callable

from chaos_pilot.incidents.models import CheckResult, Signal, VerificationResult
from chaos_pilot.remediation.engine import PreconditionFailed, RemediationContext
from chaos_pilot.scenarios.base import Detection, Injection

DEMO_ABORT_REASON = "demo: simulated operator stop"

VARIANTS = ("success", "verify_fail", "remediation_fail", "misdiagnosis", "unclear", "abort")

BAD_DEPLOYMENT_SIGNALS = (
    ("ecs", "current_task_definition", "demo-app:2"),
    ("ecs", "previous_task_definition", "demo-app:1"),
    ("ecs", "revision_changed", True),
    ("ecs", "new_revision_healthy_tasks", 0),
)
CONFIG_CHANGE_SIGNALS = (
    ("ec2", "security_group_id", "sg-demo-ecs"),
    ("ec2", "alb_ingress_rule_present", False),
    ("alb", "unhealthy_targets", 1),
)
UNCLEAR_SIGNALS = (("ecs", "revision_changed", False),)


@dataclass
class DemoTimings:
    """Seconds slept per step. `sleep` is injectable so tests run with no delay."""
    ranges: dict = field(default_factory=lambda: {
        "inject": (0.2, 0.4), "detect": (0.3, 0.8), "remediate": (0.3, 0.9),
        "verify": (0.3, 0.8), "approval_reaction": (0.5, 1.5),
    })
    sleep: Callable[[float], None] = time.sleep
    poll_interval: float = 0.05
    seed: int = 7

    def __post_init__(self):
        self._random = random.Random(self.seed)  # fixed seed; no engineered trend

    def pause(self, step: str):
        low, high = self.ranges[step]
        self.sleep(self._random.uniform(low, high))

    @classmethod
    def instant(cls, **kwargs):
        return cls(sleep=lambda seconds: None, poll_interval=0.001, **kwargs)


class DemoScenario:
    """Fake Scenario (same protocol as the real ones). It touches no AWS."""

    def __init__(self, name: str, variant: str, timings: DemoTimings, kill_switch=None):
        if variant not in VARIANTS:
            raise ValueError(f"Unknown demo variant {variant!r}")
        self.name = name
        self.variant = variant
        self.timings = timings
        self.kill_switch = kill_switch
        self.abort_guard = None
        self._injected = False

    # The "known good" state a real scenario would capture.
    def pre_injection_state(self) -> dict:
        if not self._injected:
            return {}
        if self.name == "config_change":
            return {"security_group_id": "sg-demo-ecs", "port": 80, "mocked": True}
        return {"task_definition": "demo-app:1", "known_good_was_healthy": True, "mocked": True}

    def inject(self) -> Injection:
        self.timings.pause("inject")
        self._injected = True
        if self.name == "config_change":
            return Injection("Removed the demo ALB-to-ECS rule (mocked)",
                             {"type": "security_group_rule_removed", "security_group_id": "sg-demo-ecs"})
        return Injection("Deployed an unhealthy demo revision (mocked)",
                         {"type": "unhealthy_task_definition_revision", "task_definition": "demo-app:2"})

    def _signals(self) -> list[Signal]:
        if self.variant == "unclear":
            raw = UNCLEAR_SIGNALS
        elif self.variant == "misdiagnosis":
            # A Bad Deployment run whose evidence looks like a config change: the real
            # investigator then recommends an action policy must deny for this incident type.
            raw = CONFIG_CHANGE_SIGNALS
        else:
            raw = CONFIG_CHANGE_SIGNALS if self.name == "config_change" else BAD_DEPLOYMENT_SIGNALS
        return [Signal(*item) for item in raw]

    def detect(self) -> Detection:
        self.timings.pause("detect")
        if self.variant == "abort" and self.kill_switch is not None:
            self.kill_switch.engage(DEMO_ABORT_REASON)
        return Detection(True, self._signals())

    def remediation_context(self) -> RemediationContext:
        healthy = self.variant != "remediation_fail"
        if self.name == "config_change":
            return RemediationContext(
                cluster="demo-cluster", service="demo-service", security_group_id="sg-demo-ecs",
                source_security_group_id="sg-demo-alb", port=80, known_good_rule_captured=healthy)
        return RemediationContext(
            cluster="demo-cluster", service="demo-service",
            known_good_task_definition="demo-app:1", known_good_was_healthy=healthy)

    def verify(self) -> VerificationResult:
        self.timings.pause("verify")
        ok = self.variant != "verify_fail"
        if self.name == "config_change":
            return VerificationResult((
                CheckResult("alb_ingress_rule_present", True, "True (mocked)"),
                CheckResult("running_tasks_match_desired", ok, "1/1" if ok else "0/1"),
                CheckResult("alb_targets_healthy", ok, "{'healthy': 1}" if ok else "{'unhealthy': 1}"),
            ))
        return VerificationResult((
            CheckResult("running_known_good_revision", True, "demo-app:1"),
            CheckResult("running_tasks_match_desired", ok, "1/1" if ok else "0/1"),
            CheckResult("alb_targets_healthy", ok, "{'healthy': 1}" if ok else "{'unhealthy': 1}"),
        ))

    def cleanup(self):
        return None

    def verify_cleanup(self) -> CheckResult:
        return CheckResult("cleanup_reverted", True, "demo state restored (mocked)")


class DemoRollback:
    action = "rollback_to_previous_revision"

    def __init__(self, timings: DemoTimings):
        self.timings = timings
        self.calls = []

    def check_preconditions(self, context: RemediationContext) -> None:
        if not context.known_good_was_healthy:
            raise PreconditionFailed("the previous revision was not recorded as healthy")

    def execute(self, context: RemediationContext) -> str:
        self.timings.pause("remediate")
        self.calls.append(context)
        return f"rolled back to {context.known_good_task_definition} (mocked)"


class DemoRestoreRule:
    action = "restore_security_group_rule"

    def __init__(self, timings: DemoTimings):
        self.timings = timings
        self.calls = []

    def check_preconditions(self, context: RemediationContext) -> None:
        if not context.known_good_rule_captured:
            raise PreconditionFailed("the known-good rule was not captured before injection")

    def execute(self, context: RemediationContext) -> str:
        self.timings.pause("remediate")
        self.calls.append(context)
        return f"restored the rule on {context.security_group_id} (mocked)"
