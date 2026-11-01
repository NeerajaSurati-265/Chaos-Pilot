import threading
import time

from chaos_pilot.core.engine import ChaosEngine
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
from chaos_pilot.scenarios.base import Detection, Injection
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
        self.cleanup_verified = True

    def inject(self):
        return Injection("injected", {"type": "fake_fault"})

    def pre_injection_state(self):
        return {"task_definition": "app:1"}

    def verify_cleanup(self):
        return CheckResult("cleanup_reverted", self.cleanup_verified, "fake revert check")

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


UNCLEAR_SIGNALS = [Signal("ecs", "revision_changed", False)]


def test_unclear_evidence_is_escalated_and_not_remediated():
    pipeline = make_pipeline()
    scenario = FakeScenario(signals=UNCLEAR_SIGNALS)
    incident = run(pipeline, scenario)
    report = incident.report()
    notes = [event.note for event in incident.timeline]

    assert incident.state is S.ESCALATED
    assert executed(pipeline) == []
    assert incident.remediation is None
    assert incident.remediation_result is None
    assert incident.verification is None
    assert scenario.cleaned_up
    assert "ESCALATE: escalate_to_human" in notes
    assert any("AI recommended human review" in note for note in notes)
    assert notes[-1].startswith("Cleanup completed")
    assert report["state"] == "ESCALATED"
    assert report["recommended_action"] == "escalate_to_human"
    assert report["policy_decision"] == "ESCALATE"
    assert "AI recommended human review" in report["policy_reasons"][0]
    assert report["human_intervention"] is True
    assert report["executed_remediation"] is None
    assert report["remediation_result"] is None
    assert report["verification"] == []
    assert report["recovery_seconds"] is None
    assert report["failure_reason"] is None


def test_escalation_logs_a_warning(caplog):
    with caplog.at_level("WARNING", logger="chaos_pilot.core.pipeline"):
        incident = run(make_pipeline(), FakeScenario(signals=UNCLEAR_SIGNALS))

    assert any(
        incident.id in record.getMessage() and "escalated" in record.getMessage()
        for record in caplog.records
    )


def test_cleanup_failure_on_an_escalated_incident_is_recorded():
    scenario = FakeScenario(signals=UNCLEAR_SIGNALS)

    def broken_cleanup():
        raise RuntimeError("AWS throttled")

    scenario.cleanup = broken_cleanup
    incident = run(make_pipeline(), scenario)

    assert incident.state is S.ESCALATED
    assert incident.cleanup.error == "AWS throttled"
    assert incident.timeline[-1].note.startswith("Cleanup failed: AWS throttled")


def test_escalated_incident_cannot_be_approved_or_rejected():
    pipeline = make_pipeline()
    incident = run(pipeline, FakeScenario(signals=UNCLEAR_SIGNALS))

    assert not pipeline.answer_approval(incident.id, True)
    assert not pipeline.answer_approval(incident.id, False)
    assert incident.state is S.ESCALATED


def test_new_experiment_can_start_after_an_escalation(monkeypatch):
    monkeypatch.setenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", "true")
    engine = ChaosEngine(
        task_failure=object(),
        pipeline=make_pipeline(),
        scenario_factories={
            "unclear": lambda: FakeScenario(signals=UNCLEAR_SIGNALS),
            "bad-deployment": FakeScenario,
        },
    )

    escalated = engine.start_scenario("unclear", background=False)
    assert escalated.state is S.ESCALATED
    assert not engine._running.locked()

    resolved = engine.start_scenario("bad-deployment", background=False)
    assert resolved.state is S.RESOLVED


def test_action_that_does_not_fit_the_incident_type_never_reaches_remediation():
    # The rule-based investigator recommends rollback for these signals, but this is a
    # config_change incident.
    pipeline = make_pipeline()
    scenario = FakeScenario()
    scenario.name = "config_change"
    incident = run(pipeline, scenario)

    assert incident.state is S.REJECTED
    assert incident.report()["policy_decision"] == "DENY"
    assert any(
        "does not fit incident type 'config_change'" in e.note
        for e in incident.timeline if e.state is S.REJECTED
    )
    assert incident.remediation_result is None
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


def test_failed_remediation_fails_incident_and_is_never_verified():
    failing = FakeExecutor(execute_error=RuntimeError("AWS throttled"))
    scenario = FakeScenario()
    incident = run(make_pipeline(executors=[failing]), scenario)

    assert incident.state is S.FAILED
    assert "Remediation failed" in incident.failure_reason
    assert "AWS throttled" in incident.failure_reason
    assert incident.remediation is None
    assert incident.remediation_result.status.value == "FAILED"
    assert incident.verification is None
    assert scenario.cleaned_up
