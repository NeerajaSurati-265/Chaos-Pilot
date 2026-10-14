from dataclasses import dataclass
from enum import Enum

from chaos_pilot.incidents.models import Decision, Diagnosis, PolicyDecision


class BlastRadius(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class ActionRule:
    """Risk profile of one remediation action Chaos-Pilot is allowed to run."""
    reversible: bool
    blast_radius: BlastRadius
    auto_allowed: bool


# Allow-list of remediation actions. Anything not listed here is denied.
DEFAULT_RULES: dict[str, ActionRule] = {
    "rollback_to_previous_revision": ActionRule(
        reversible=True, blast_radius=BlastRadius.LOW, auto_allowed=True
    ),
    "restore_security_group_rule": ActionRule(
        reversible=True, blast_radius=BlastRadius.MEDIUM, auto_allowed=False
    ),
    "restore_known_good_config": ActionRule(
        reversible=True, blast_radius=BlastRadius.MEDIUM, auto_allowed=False
    ),
    "restore_dependency": ActionRule(
        reversible=True, blast_radius=BlastRadius.MEDIUM, auto_allowed=False
    ),
}

DEFAULT_MIN_AUTO_CONFIDENCE = 0.7


class PolicyEngine:
    """Deterministic rules that decide AUTO, HUMAN_APPROVAL or DENY.

    The AI's confidence is one input. It can only make the decision more
    cautious, never grant an action the rules don't already allow.
    """

    def __init__(
        self,
        rules: dict[str, ActionRule] | None = None,
        min_auto_confidence: float = DEFAULT_MIN_AUTO_CONFIDENCE,
    ):
        self.rules = DEFAULT_RULES if rules is None else rules
        self.min_auto_confidence = min_auto_confidence

    def decide(self, diagnosis: Diagnosis, experiments_enabled: bool) -> PolicyDecision:
        action = diagnosis.recommended_action
        rule = self.rules.get(action)

        if not experiments_enabled:
            return PolicyDecision(action, Decision.DENY, ("Kill switch is on",))

        if rule is None:
            return PolicyDecision(
                action, Decision.DENY, (f"'{action}' is not an allow-listed action",)
            )

        reasons = []
        if not rule.auto_allowed:
            reasons.append("Action requires approval by policy")
        if not rule.reversible:
            reasons.append("Action is not reversible")
        if rule.blast_radius is not BlastRadius.LOW:
            reasons.append(f"Blast radius is {rule.blast_radius.value}")
        if diagnosis.confidence < self.min_auto_confidence:
            reasons.append(
                f"Confidence {diagnosis.confidence:.2f} is below "
                f"{self.min_auto_confidence:.2f}"
            )

        if reasons:
            return PolicyDecision(action, Decision.HUMAN_APPROVAL, tuple(reasons))

        return PolicyDecision(
            action,
            Decision.AUTO,
            ("Allow-listed, reversible, low blast radius, confidence above threshold",),
        )
