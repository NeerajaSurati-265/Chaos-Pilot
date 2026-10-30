import pytest

from chaos_pilot.incidents.models import Decision, Diagnosis
from chaos_pilot.policy.engine import SCENARIO_ACTIONS, PolicyEngine
from chaos_pilot.scenarios.bad_deployment import BadDeploymentScenario
from chaos_pilot.scenarios.config_change import ConfigChangeScenario

# The scenarios ChaosEngine registers (chaos_pilot/core/engine.py). Add new ones here.
REGISTERED_SCENARIOS = [BadDeploymentScenario, ConfigChangeScenario]

ROLLBACK = "rollback_to_previous_revision"
RESTORE_RULE = "restore_security_group_rule"


def diagnosis(action, confidence=0.9):
    return Diagnosis(
        likely_cause="test",
        confidence=confidence,
        explanation="test",
        recommended_action=action,
    )


engine = PolicyEngine()


def test_rollback_with_high_confidence_is_auto():
    result = engine.decide(diagnosis(ROLLBACK), True, scenario="bad_deployment")
    assert result.decision is Decision.AUTO


def test_low_confidence_rollback_needs_approval():
    result = engine.decide(diagnosis(ROLLBACK, 0.4), True, scenario="bad_deployment")
    assert result.decision is Decision.HUMAN_APPROVAL


def test_security_group_restore_always_needs_approval_even_when_confident():
    result = engine.decide(diagnosis(RESTORE_RULE, 0.99), True, scenario="config_change")
    assert result.decision is Decision.HUMAN_APPROVAL


def test_unknown_action_is_denied():
    result = engine.decide(diagnosis("delete_the_vpc", 0.99), True, scenario="bad_deployment")
    assert result.decision is Decision.DENY


def test_kill_switch_denies_everything():
    result = engine.decide(diagnosis(ROLLBACK), False, scenario="bad_deployment")
    assert result.decision is Decision.DENY


@pytest.mark.parametrize("confidence", [0.9, 1.0])
def test_rollback_during_config_change_is_denied_at_any_confidence(confidence):
    result = engine.decide(diagnosis(ROLLBACK, confidence), True, scenario="config_change")

    assert result.decision is Decision.DENY
    assert "does not fit incident type 'config_change'" in result.reasons[0]


def test_security_group_restore_during_bad_deployment_is_denied():
    result = engine.decide(diagnosis(RESTORE_RULE, 1.0), True, scenario="bad_deployment")

    assert result.decision is Decision.DENY
    assert "does not fit" in result.reasons[0]


def test_unknown_incident_type_is_denied():
    result = engine.decide(diagnosis(ROLLBACK, 1.0), True, scenario="made_up_incident")

    assert result.decision is Decision.DENY
    assert "Unknown incident type" in result.reasons[0]


def test_escalate_to_human_keeps_its_allow_list_denial():
    result = engine.decide(diagnosis("escalate_to_human", 0.3), True, scenario="config_change")

    assert result.decision is Decision.DENY
    assert "not an allow-listed action" in result.reasons[0]


@pytest.mark.parametrize("scenario_class", REGISTERED_SCENARIOS)
def test_every_registered_scenario_has_a_permitted_actions_entry(scenario_class):
    assert scenario_class.name in SCENARIO_ACTIONS
