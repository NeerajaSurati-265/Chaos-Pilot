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
from chaos_pilot.policy.engine import DEFAULT_RULES, PolicyEngine
from chaos_pilot.remediation.engine import RemediationContext, RemediationEngine
from chaos_pilot.scenarios.base import Detection
from tests.test_remediation_engine import FakeExecutor

S = IncidentState

BAD_DEPLOY_SIGNALS = [
    Signal("ecs", "current_task_definition", "app:2"),
    Signal("ecs", "previous_task_definition", "app:1"),
    Signal("ecs", "revision_changed", True),
    Signal("ecs", "new_revision_healthy_tasks", 0),
]
CONTEXT = RemediationContext(
    cluster="chaos-pilot-cluster", service="chaos-pilot-service",
    known_good_task_definition="app:1",
)


class FakeScenario:
    name = "bad_deployment"

    def __init__(self, detected=True, signals=None, verified=True, raise_on=None):
        self.detected = detected
        self.signals = BAD_DEPLOY_SIGNALS if signals is None else signals
        self.verified = verified
        self.raise_on = raise_on
        self.cleaned_up = False

    def inject(self):
        return "injected"

    def detect(self):
        if self.raise_on == "detect":
            raise RuntimeError("AWS throttled")
        return Detection(self.detected, self.signals)

    def remediation_context(self):
        return CONTEXT

    def verify(self):
        return VerificationResult((CheckResult("healthy", self.verified),))

    def cleanup(self):
        self.cleaned_up = True


def make_pipeline(enabled=True, approval_timeout=5, executors=None):
    executors = [FakeExecutor()] if executors is None else executors
    return Pipeline(
        store=IncidentStore(),
        investigator=RuleBasedInvestigator(),
        policy=PolicyEngine(),
        remediation=RemediationEngine(executors, allowed_actions=DEFAULT_RULES),
        experiments_enabled=lambda: enabled,
        approval_timeout=approval_timeout,
    )


def executed(pipeline):
    return [
        (action, context)
        for action, executor in pipeline.remediation.executors.items()
        for context in executor.calls
    ]


ROLLED_BACK = [("rollback_to_previous_revision", CONTEXT)]


def run(pipeline, scenario):
    return pipeline.run(pipeline.start(scenario), scenario)


def test_bad_deployment_is_rolled_back_automatically():
    pipeline = make_pipeline()
    scenario = FakeScenario()
    incident = run(pipeline, scenario)

    assert incident.state is S.RESOLVED
    assert executed(pipeline) == ROLLED_BACK
    assert incident.remediation == "ran rollback_to_previous_revision"
    assert incident.human_intervention is False
    assert incident.report()["policy_decision"] == "AUTO"
    assert scenario.cleaned_up


def test_failed_verification_is_reported_as_failed():
    incident = run(make_pipeline(), FakeScenario(verified=False))

    assert incident.state is S.FAILED
    assert "healthy" in incident.failure_reason


def test_nothing_detected_fails_without_remediating():
    pipeline = make_pipeline()
    incident = run(pipeline, FakeScenario(detected=False))

    assert incident.state is S.FAILED
    assert executed(pipeline) == []


def test_unclear_evidence_is_denied_and_not_remediated():
    pipeline = make_pipeline()
    scenario = FakeScenario(signals=[Signal("ecs", "revision_changed", False)])
    incident = run(pipeline, scenario)

    assert incident.state is S.REJECTED
    assert executed(pipeline) == []


def test_kill_switch_stops_before_injection():
    pipeline = make_pipeline(enabled=False)
    incident = run(pipeline, FakeScenario())

    assert incident.state is S.FAILED
    assert executed(pipeline) == []


def test_unexpected_error_becomes_failed_and_still_cleans_up():
    scenario = FakeScenario(raise_on="detect")
    incident = run(make_pipeline(), scenario)

    assert incident.state is S.FAILED
    assert "AWS throttled" in incident.failure_reason
    assert scenario.cleaned_up


def test_refused_remediation_fails_and_never_resolves():
    # Allow-listed and approved by policy, but nothing can run it.
    pipeline = make_pipeline(executors=[])
    scenario = FakeScenario()
    incident = run(pipeline, scenario)

    assert incident.state is S.FAILED
    assert "Remediation refused" in incident.failure_reason
    assert "No executor" in incident.failure_reason
    assert incident.remediation is None
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
    return incident, pipeline


def test_approved_action_runs():
    incident, pipeline = run_with_answer(True)

    assert incident.state is S.RESOLVED
    assert incident.human_intervention is True
    assert executed(pipeline) == ROLLED_BACK


def test_rejected_action_does_not_run():
    incident, pipeline = run_with_answer(False)

    assert incident.state is S.REJECTED
    assert executed(pipeline) == []


def test_approval_timeout_fails():
    pipeline = make_pipeline(approval_timeout=0.01)
    pipeline.policy = ApprovalPolicy()
    incident = run(pipeline, FakeScenario())

    assert incident.state is S.FAILED
    assert incident.failure_reason == "Approval timed out"
    assert executed(pipeline) == []
