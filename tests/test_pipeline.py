import threading
import time

from chaos_pilot.core.pipeline import Pipeline
from chaos_pilot.incidents.models import (
    CheckResult,
    IncidentState,
    Signal,
    VerificationResult,
)
from chaos_pilot.incidents.store import IncidentStore
from chaos_pilot.investigator.rule_based import RuleBasedInvestigator
from chaos_pilot.policy.engine import PolicyEngine
from chaos_pilot.scenarios.base import Detection

S = IncidentState

BAD_DEPLOY_SIGNALS = [
    Signal("ecs", "current_task_definition", "app:2"),
    Signal("ecs", "previous_task_definition", "app:1"),
    Signal("ecs", "revision_changed", True),
    Signal("ecs", "new_revision_healthy_tasks", 0),
]


class FakeScenario:
    name = "bad_deployment"

    def __init__(self, detected=True, signals=None, verified=True, raise_on=None):
        self.detected = detected
        self.signals = BAD_DEPLOY_SIGNALS if signals is None else signals
        self.verified = verified
        self.raise_on = raise_on
        self.remediated_with = None
        self.cleaned_up = False

    def inject(self):
        return "injected"

    def detect(self):
        if self.raise_on == "detect":
            raise RuntimeError("AWS throttled")
        return Detection(self.detected, self.signals)

    def remediate(self, action):
        self.remediated_with = action
        return f"ran {action}"

    def verify(self):
        return VerificationResult((CheckResult("healthy", self.verified),))

    def cleanup(self):
        self.cleaned_up = True


def make_pipeline(enabled=True, approval_timeout=5):
    return Pipeline(
        store=IncidentStore(),
        investigator=RuleBasedInvestigator(),
        policy=PolicyEngine(),
        experiments_enabled=lambda: enabled,
        approval_timeout=approval_timeout,
    )


def run(pipeline, scenario):
    return pipeline.run(pipeline.start(scenario), scenario)


def test_bad_deployment_is_rolled_back_automatically():
    scenario = FakeScenario()
    incident = run(make_pipeline(), scenario)

    assert incident.state is S.RESOLVED
    assert scenario.remediated_with == "rollback_to_previous_revision"
    assert incident.human_intervention is False
    assert incident.report()["policy_decision"] == "AUTO"
    assert scenario.cleaned_up


def test_failed_verification_is_reported_as_failed():
    incident = run(make_pipeline(), FakeScenario(verified=False))

    assert incident.state is S.FAILED
    assert "healthy" in incident.failure_reason


def test_nothing_detected_fails_without_remediating():
    scenario = FakeScenario(detected=False)
    incident = run(make_pipeline(), scenario)

    assert incident.state is S.FAILED
    assert scenario.remediated_with is None


def test_unclear_evidence_is_denied_and_not_remediated():
    scenario = FakeScenario(signals=[Signal("ecs", "revision_changed", False)])
    incident = run(make_pipeline(), scenario)

    assert incident.state is S.REJECTED
    assert scenario.remediated_with is None


def test_kill_switch_stops_before_injection():
    scenario = FakeScenario()
    incident = run(make_pipeline(enabled=False), scenario)

    assert incident.state is S.FAILED
    assert scenario.remediated_with is None


def test_unexpected_error_becomes_failed_and_still_cleans_up():
    scenario = FakeScenario(raise_on="detect")
    incident = run(make_pipeline(), scenario)

    assert incident.state is S.FAILED
    assert "AWS throttled" in incident.failure_reason
    assert scenario.cleaned_up


class ApprovalPolicy(PolicyEngine):
    def __init__(self):
        super().__init__(min_auto_confidence=0.99)


def run_with_answer(approved):
    pipeline = make_pipeline()
    pipeline.policy = ApprovalPolicy()
    scenario = FakeScenario()
    incident = pipeline.start(scenario)
    worker = threading.Thread(target=pipeline.run, args=(incident, scenario))
    worker.start()

    deadline = time.monotonic() + 5
    while incident.state is not S.AWAITING_APPROVAL and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pipeline.answer_approval(incident.id, approved)
    worker.join(5)
    return incident, scenario


def test_approved_action_runs():
    incident, scenario = run_with_answer(True)

    assert incident.state is S.RESOLVED
    assert incident.human_intervention is True
    assert scenario.remediated_with == "rollback_to_previous_revision"


def test_rejected_action_does_not_run():
    incident, scenario = run_with_answer(False)

    assert incident.state is S.REJECTED
    assert scenario.remediated_with is None


def test_approval_timeout_fails():
    pipeline = make_pipeline(approval_timeout=0.01)
    pipeline.policy = ApprovalPolicy()
    incident = run(pipeline, FakeScenario())

    assert incident.state is S.FAILED
    assert incident.failure_reason == "Approval timed out"
