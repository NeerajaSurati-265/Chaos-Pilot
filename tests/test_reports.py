from app import incident_view
from app.reports import NOT_RECORDED, render_markdown
from chaos_pilot.incidents.models import (
    CheckResult,
    CleanupResult,
    Diagnosis,
    Incident,
    IncidentState as S,
    Signal,
    VerificationResult,
)


def resolved():
    incident = Incident(scenario="bad_deployment")
    incident.signals = [Signal("ecs", "revision_changed", True)]
    incident.diagnosis = Diagnosis("bad release", 0.9, "because", "rollback_to_previous_revision")
    incident.injected_fault = {"type": "fake_fault"}
    incident.pre_injection_state = {"task_definition": "app:1"}
    incident.remediation = "ran rollback"
    incident.verification = VerificationResult((CheckResult("healthy", True, "1/1"),))
    incident.cleanup = CleanupResult(True, succeeded=True, verified=True, verification_detail="reverted")
    for state in (S.INJECTED, S.DETECTED, S.INVESTIGATING, S.POLICY_DECIDED, S.REMEDIATING, S.VERIFYING, S.RESOLVED):
        incident.transition(state)
    return incident


def test_markdown_has_every_report_field():
    md = render_markdown(incident_view.build(resolved(), 900))
    for needle in ("inc-", "bad_deployment", "RESOLVED", "bad release", "90%", "because",
                   "rollback_to_previous_revision", "ran rollback", "PASS: healthy (1/1)",
                   "fake_fault", "app:1", "Revert verified:** True", "Human intervention required:** no",
                   "Recovery time", "Detect time", "Timeline", "revision_changed"):
        assert needle in md, needle


def test_markdown_marks_unrecorded_fields():
    incident = Incident(scenario="bad_deployment")
    md = render_markdown(incident_view.build(incident, 900))
    assert md.count(NOT_RECORDED) >= 8
    assert "Likely cause:** Not recorded yet" in md


def test_markdown_flags_unverified_cleanup_and_failure_reason():
    incident = Incident(scenario="bad_deployment")
    incident.cleanup = CleanupResult(True, succeeded=True, verified=False, verification_detail="still faulted")
    incident.fail("Verification failed: healthy")
    md = render_markdown(incident_view.build(incident, 900))
    assert "WARNING: the injected fault may still be present" in md
    assert "Failure reason:** Verification failed: healthy" in md
    assert "recovered in" not in md


def test_markdown_code_fence_survives_backticks_in_evidence():
    incident = Incident(scenario="bad_deployment")
    incident.signals = [Signal("x", "note", "```evil```")]
    md = render_markdown(incident_view.build(incident, 900))
    assert "````json" in md
