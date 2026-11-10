"""Read-only view helpers: derived fields the dashboard needs, computed on the server.

Nothing here changes an incident; it only reads the timeline and policy decision.
"""
from datetime import datetime

from chaos_pilot.incidents.models import (
    TERMINAL_STATES,
    Decision,
    Incident,
    IncidentState as S,
    utc_now,
)

# Stage → the state whose first timeline entry starts it. A stage ends at the next entry
# with a different state (or the terminal entry); None while it is still running.
STAGE_START = {
    "break": S.PENDING,
    "notice": S.INJECTED,
    "investigate": S.INVESTIGATING,
    "decide": S.POLICY_DECIDED,
    "approval_wait": S.AWAITING_APPROVAL,
    "fix": S.REMEDIATING,
    "verify": S.VERIFYING,
}
STATE_STAGE = {
    S.PENDING: "break",
    S.INJECTED: "notice",
    S.DETECTED: "investigate",
    S.INVESTIGATING: "investigate",
    S.POLICY_DECIDED: "decide",
    S.AWAITING_APPROVAL: "approval_wait",
    S.REMEDIATING: "fix",
    S.VERIFYING: "verify",
}


def stage_seconds(incident: Incident) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    events = incident.timeline
    for stage, start_state in STAGE_START.items():
        start = next((i for i, e in enumerate(events) if e.state is start_state), None)
        end = None
        if start is not None:
            end = next((e for e in events[start + 1:] if e.state is not start_state), None)
        result[stage] = (
            None if start is None or end is None
            else (end.at - events[start].at).total_seconds()
        )
    return result


def stopped_at(incident: Incident) -> str | None:
    """The stage a run ended in, or None if it resolved or is still going."""
    if incident.state not in TERMINAL_STATES or incident.state is S.RESOLVED:
        return None
    events = incident.timeline
    first_terminal = next((i for i, e in enumerate(events) if e.state in TERMINAL_STATES), None)
    if not first_terminal:
        return None
    return STATE_STAGE.get(events[first_terminal - 1].state)


def outcome(incident: Incident) -> str:
    state = incident.state
    if state is S.REJECTED:
        denied = (
            incident.policy_decision is not None
            and incident.policy_decision.decision is Decision.DENY
        )
        return "DENIED" if denied else "REJECTED"
    if state in TERMINAL_STATES:
        return state.value
    return "AWAITING_APPROVAL" if state is S.AWAITING_APPROVAL else "IN_PROGRESS"


def approval_info(
    incident: Incident, timeout_seconds: float, now: datetime | None = None
) -> dict:
    entered = next((e.at for e in incident.timeline if e.state is S.AWAITING_APPROVAL), None)
    if entered is None:
        return {"approval_deadline": None, "approval_seconds_remaining": None}
    deadline = datetime.fromtimestamp(entered.timestamp() + timeout_seconds, entered.tzinfo)
    remaining = None
    if incident.state is S.AWAITING_APPROVAL:
        remaining = max(0.0, (deadline - (now or utc_now())).total_seconds())
    return {
        "approval_deadline": deadline.isoformat(),
        "approval_seconds_remaining": remaining,
    }


DEMO_NOTICE = "DEMO DATA: sample incident from mocked AWS, not a real run"


def build(
    incident: Incident, approval_timeout: float, now: datetime | None = None, demo: bool = False
) -> dict:
    """incident.report() plus derived, read-only fields for the dashboard."""
    view = incident.report()
    decision = incident.policy_decision
    view.update(
        outcome=outcome(incident),
        stage_seconds=stage_seconds(incident),
        stopped_at=stopped_at(incident),
        policy_action=decision.action if decision else None,
        policy_denied_ai_action=bool(decision and decision.decision is Decision.DENY),
        approval_timeout_seconds=approval_timeout,
        demo=demo,
        demo_notice=DEMO_NOTICE if demo else None,
        **approval_info(incident, approval_timeout, now),
    )
    return view
