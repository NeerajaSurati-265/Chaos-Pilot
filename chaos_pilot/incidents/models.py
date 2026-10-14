from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IncidentState(str, Enum):
    INJECTED = "INJECTED"
    DETECTED = "DETECTED"
    INVESTIGATING = "INVESTIGATING"
    POLICY_DECIDED = "POLICY_DECIDED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REMEDIATING = "REMEDIATING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


TERMINAL_STATES = {IncidentState.RESOLVED, IncidentState.REJECTED, IncidentState.FAILED}

ALLOWED_TRANSITIONS = {
    IncidentState.INJECTED: {IncidentState.DETECTED},
    IncidentState.DETECTED: {IncidentState.INVESTIGATING},
    IncidentState.INVESTIGATING: {IncidentState.POLICY_DECIDED},
    IncidentState.POLICY_DECIDED: {
        IncidentState.REMEDIATING,
        IncidentState.AWAITING_APPROVAL,
        IncidentState.REJECTED,
    },
    IncidentState.AWAITING_APPROVAL: {IncidentState.REMEDIATING, IncidentState.REJECTED},
    IncidentState.REMEDIATING: {IncidentState.VERIFYING},
    IncidentState.VERIFYING: {IncidentState.RESOLVED},
}


class InvalidTransition(Exception):
    pass


@dataclass(frozen=True)
class Signal:
    """A deterministic observation, e.g. an ECS service event or ALB health."""
    source: str
    name: str
    value: Any
    observed_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class Diagnosis:
    """What the AI investigator thinks happened. It never authorizes anything."""
    likely_cause: str
    confidence: float
    explanation: str
    recommended_action: str


class Decision(str, Enum):
    AUTO = "AUTO"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    DENY = "DENY"


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    decision: Decision
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class VerificationResult:
    checks: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)


@dataclass(frozen=True)
class TimelineEvent:
    state: IncidentState
    at: datetime
    note: str = ""


@dataclass
class Incident:
    scenario: str
    id: str = field(default_factory=lambda: f"inc-{uuid4().hex[:8]}")
    state: IncidentState = IncidentState.INJECTED
    signals: list[Signal] = field(default_factory=list)
    diagnosis: Diagnosis | None = None
    policy_decision: PolicyDecision | None = None
    remediation: str | None = None
    verification: VerificationResult | None = None
    human_intervention: bool = False
    failure_reason: str | None = None
    timeline: list[TimelineEvent] = field(default_factory=list)

    def __post_init__(self):
        if not self.timeline:
            self.timeline.append(TimelineEvent(self.state, utc_now(), "Incident opened"))

    def add_note(self, note: str):
        self.timeline.append(TimelineEvent(self.state, utc_now(), note))

    def transition(self, new_state: IncidentState, note: str = ""):
        if self.state in TERMINAL_STATES:
            raise InvalidTransition(f"{self.id} is already {self.state.value}")

        allowed = ALLOWED_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise InvalidTransition(
                f"{self.id}: cannot go from {self.state.value} to {new_state.value}"
            )

        self.state = new_state
        self.timeline.append(TimelineEvent(new_state, utc_now(), note))

    def fail(self, reason: str):
        # Any non-terminal step can fail. Failure is recorded, never hidden.
        if self.state in TERMINAL_STATES:
            raise InvalidTransition(f"{self.id} is already {self.state.value}")

        self.failure_reason = reason
        self.state = IncidentState.FAILED
        self.timeline.append(TimelineEvent(IncidentState.FAILED, utc_now(), reason))

    @property
    def recovery_seconds(self) -> float | None:
        # Measured from detection to verified recovery.
        if self.state is not IncidentState.RESOLVED:
            return None
        detected = next(
            (e.at for e in self.timeline if e.state is IncidentState.DETECTED),
            self.timeline[0].at,
        )
        return (self.timeline[-1].at - detected).total_seconds()

    def report(self) -> dict:
        return {
            "incident_id": self.id,
            "scenario": self.scenario,
            "state": self.state.value,
            "detected_signals": [
                {"source": s.source, "name": s.name, "value": s.value}
                for s in self.signals
            ],
            "likely_cause": self.diagnosis.likely_cause if self.diagnosis else None,
            "confidence": self.diagnosis.confidence if self.diagnosis else None,
            "recommended_action": (
                self.diagnosis.recommended_action if self.diagnosis else None
            ),
            "policy_decision": (
                self.policy_decision.decision.value if self.policy_decision else None
            ),
            "policy_reasons": (
                list(self.policy_decision.reasons) if self.policy_decision else []
            ),
            "executed_remediation": self.remediation,
            "verification": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in (self.verification.checks if self.verification else ())
            ],
            "recovery_seconds": self.recovery_seconds,
            "human_intervention": self.human_intervention,
            "failure_reason": self.failure_reason,
            "timeline": [
                {"state": e.state.value, "at": e.at.isoformat(), "note": e.note}
                for e in self.timeline
            ],
        }
