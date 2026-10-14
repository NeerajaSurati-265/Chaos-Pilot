import pytest

from chaos_pilot.incidents.models import (
    CheckResult,
    Incident,
    IncidentState,
    InvalidTransition,
    VerificationResult,
)

S = IncidentState


def walk(incident, *states):
    for state in states:
        incident.transition(state)


def test_happy_path_reaches_resolved_with_recovery_time():
    incident = Incident(scenario="bad_deployment")
    walk(incident, S.DETECTED, S.INVESTIGATING, S.POLICY_DECIDED,
         S.REMEDIATING, S.VERIFYING, S.RESOLVED)

    assert incident.state is S.RESOLVED
    assert incident.recovery_seconds is not None
    assert [e.state for e in incident.timeline][0] is S.INJECTED


def test_approval_path():
    incident = Incident(scenario="config_change")
    walk(incident, S.DETECTED, S.INVESTIGATING, S.POLICY_DECIDED,
         S.AWAITING_APPROVAL, S.REMEDIATING)

    assert incident.state is S.REMEDIATING


def test_cannot_skip_steps():
    incident = Incident(scenario="bad_deployment")

    with pytest.raises(InvalidTransition):
        incident.transition(S.REMEDIATING)


def test_cannot_resolve_without_verifying():
    incident = Incident(scenario="bad_deployment")
    walk(incident, S.DETECTED, S.INVESTIGATING, S.POLICY_DECIDED, S.REMEDIATING)

    with pytest.raises(InvalidTransition):
        incident.transition(S.RESOLVED)


def test_failure_is_recorded_and_terminal():
    incident = Incident(scenario="bad_deployment")
    walk(incident, S.DETECTED, S.INVESTIGATING, S.POLICY_DECIDED,
         S.REMEDIATING, S.VERIFYING)

    incident.fail("Healthy task count stayed at 0 after 300s")

    assert incident.state is S.FAILED
    assert incident.recovery_seconds is None
    assert incident.report()["failure_reason"].startswith("Healthy task count")
    with pytest.raises(InvalidTransition):
        incident.transition(S.RESOLVED)


def test_verification_needs_at_least_one_passing_check():
    assert not VerificationResult(checks=()).passed
    assert not VerificationResult(
        checks=(CheckResult("alb_healthy", True), CheckResult("tasks_running", False))
    ).passed
    assert VerificationResult(checks=(CheckResult("alb_healthy", True),)).passed
