from datetime import datetime, timedelta, timezone

from app import incident_view
from chaos_pilot.incidents.models import (
    Decision,
    Incident,
    IncidentState as S,
    PolicyDecision,
    TimelineEvent,
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make(events, state, decision=None, **kw):
    incident = Incident(
        scenario="bad_deployment",
        state=state,
        timeline=[TimelineEvent(s, BASE + timedelta(seconds=t), note) for s, t, note in events],
        **kw,
    )
    if decision:
        incident.policy_decision = PolicyDecision("rollback_to_previous_revision", decision, ("why",))
    return incident


RESOLVED_FLOW = [
    (S.PENDING, 0, "opened"), (S.INJECTED, 1, ""), (S.DETECTED, 3, ""),
    (S.INVESTIGATING, 3, ""), (S.POLICY_DECIDED, 5, ""), (S.REMEDIATING, 6, ""),
    (S.VERIFYING, 10, ""), (S.RESOLVED, 12, ""), (S.RESOLVED, 13, "cleanup note"),
]


def test_stage_seconds_for_a_resolved_run():
    incident = make(RESOLVED_FLOW, S.RESOLVED, Decision.AUTO)
    assert incident_view.stage_seconds(incident) == {
        "break": 1, "notice": 2, "investigate": 2, "decide": 1,
        "approval_wait": None, "fix": 4, "verify": 2,
    }


def test_stage_still_running_has_no_duration_yet():
    incident = make(RESOLVED_FLOW[:7], S.VERIFYING, Decision.AUTO)
    seconds = incident_view.stage_seconds(incident)
    assert seconds["fix"] == 4 and seconds["verify"] is None


def test_approval_wait_is_measured():
    events = [
        (S.PENDING, 0, ""), (S.INJECTED, 1, ""), (S.DETECTED, 2, ""), (S.INVESTIGATING, 2, ""),
        (S.POLICY_DECIDED, 3, ""), (S.AWAITING_APPROVAL, 4, ""), (S.REMEDIATING, 34, ""),
    ]
    incident = make(events, S.REMEDIATING, Decision.HUMAN_APPROVAL)
    assert incident_view.stage_seconds(incident)["approval_wait"] == 30


def test_stopped_at_names_the_stage_a_run_ended_in():
    verify_fail = make(RESOLVED_FLOW[:7] + [(S.FAILED, 11, "nope"), (S.FAILED, 12, "cleanup")], S.FAILED)
    assert incident_view.stopped_at(verify_fail) == "verify"
    denied = make(RESOLVED_FLOW[:5] + [(S.REJECTED, 6, "denied")], S.REJECTED, Decision.DENY)
    assert incident_view.stopped_at(denied) == "decide"
    injection_failed = make([(S.PENDING, 0, ""), (S.FAILED, 1, "boom")], S.FAILED)
    assert incident_view.stopped_at(injection_failed) == "break"


def test_stopped_at_is_none_for_resolved_and_running():
    assert incident_view.stopped_at(make(RESOLVED_FLOW, S.RESOLVED)) is None
    assert incident_view.stopped_at(make(RESOLVED_FLOW[:4], S.INVESTIGATING)) is None


def test_outcome_separates_policy_denial_from_human_rejection():
    base = RESOLVED_FLOW[:5] + [(S.REJECTED, 6, "")]
    assert incident_view.outcome(make(base, S.REJECTED, Decision.DENY)) == "DENIED"
    assert incident_view.outcome(make(base, S.REJECTED, Decision.HUMAN_APPROVAL)) == "REJECTED"
    assert incident_view.outcome(make(base, S.REJECTED)) == "REJECTED"


def test_outcome_for_other_states():
    assert incident_view.outcome(make(RESOLVED_FLOW, S.RESOLVED)) == "RESOLVED"
    assert incident_view.outcome(make(RESOLVED_FLOW[:2], S.INJECTED)) == "IN_PROGRESS"
    assert incident_view.outcome(make(RESOLVED_FLOW[:5], S.AWAITING_APPROVAL)) == "AWAITING_APPROVAL"
    for state in (S.FAILED, S.ABORTED, S.ESCALATED):
        assert incident_view.outcome(make(RESOLVED_FLOW[:3], state)) == state.value


def test_approval_deadline_is_computed_on_the_server():
    events = RESOLVED_FLOW[:5] + [(S.AWAITING_APPROVAL, 10, "")]
    incident = make(events, S.AWAITING_APPROVAL, Decision.HUMAN_APPROVAL)
    now = BASE + timedelta(seconds=40)
    info = incident_view.approval_info(incident, 900, now)
    assert info["approval_deadline"] == (BASE + timedelta(seconds=910)).isoformat()
    assert info["approval_seconds_remaining"] == 870


def test_approval_remaining_is_clamped_and_cleared_once_answered():
    events = RESOLVED_FLOW[:5] + [(S.AWAITING_APPROVAL, 10, "")]
    waiting = make(events, S.AWAITING_APPROVAL, Decision.HUMAN_APPROVAL)
    late = BASE + timedelta(seconds=5000)
    assert incident_view.approval_info(waiting, 900, late)["approval_seconds_remaining"] == 0
    answered = make(events + [(S.REMEDIATING, 20, "")], S.REMEDIATING, Decision.HUMAN_APPROVAL)
    info = incident_view.approval_info(answered, 900, late)
    assert info["approval_seconds_remaining"] is None and info["approval_deadline"] is not None


def test_no_approval_fields_when_never_awaited():
    info = incident_view.approval_info(make(RESOLVED_FLOW, S.RESOLVED), 900)
    assert info == {"approval_deadline": None, "approval_seconds_remaining": None}


def test_build_adds_derived_fields_without_dropping_report_fields():
    incident = make(RESOLVED_FLOW[:5] + [(S.REJECTED, 6, "")], S.REJECTED, Decision.DENY)
    view = incident_view.build(incident, 900)
    assert {"incident_id", "timeline", "policy_decision", "cleanup"} <= set(view)
    assert view["outcome"] == "DENIED"
    assert view["policy_denied_ai_action"] is True
    assert view["policy_action"] == "rollback_to_previous_revision"
    assert view["approval_timeout_seconds"] == 900
    assert view["stopped_at"] == "decide"


def test_build_for_an_incident_with_no_decision():
    view = incident_view.build(Incident(scenario="bad_deployment"), 900)
    assert view["policy_action"] is None and view["policy_denied_ai_action"] is False
    assert view["outcome"] == "IN_PROGRESS" and view["stopped_at"] is None
