from chaos_pilot.incidents.models import Decision, Diagnosis
from chaos_pilot.policy.engine import PolicyEngine


def diagnosis(action, confidence=0.9):
    return Diagnosis(
        likely_cause="test",
        confidence=confidence,
        explanation="test",
        recommended_action=action,
    )


engine = PolicyEngine()


def test_rollback_with_high_confidence_is_auto():
    result = engine.decide(diagnosis("rollback_to_previous_revision"), True)
    assert result.decision is Decision.AUTO


def test_low_confidence_rollback_needs_approval():
    result = engine.decide(diagnosis("rollback_to_previous_revision", 0.4), True)
    assert result.decision is Decision.HUMAN_APPROVAL


def test_security_group_restore_always_needs_approval_even_when_confident():
    result = engine.decide(diagnosis("restore_security_group_rule", 0.99), True)
    assert result.decision is Decision.HUMAN_APPROVAL


def test_unknown_action_is_denied():
    result = engine.decide(diagnosis("delete_the_vpc", 0.99), True)
    assert result.decision is Decision.DENY


def test_kill_switch_denies_everything():
    result = engine.decide(diagnosis("rollback_to_previous_revision"), False)
    assert result.decision is Decision.DENY
