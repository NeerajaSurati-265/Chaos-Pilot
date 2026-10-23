import pytest

from chaos_pilot.incidents.models import Decision, PolicyDecision
from chaos_pilot.policy.engine import DEFAULT_RULES
from chaos_pilot.remediation.engine import (
    RemediationContext,
    RemediationEngine,
    RemediationRefused,
    RemediationResult,
)

ROLLBACK = "rollback_to_previous_revision"
CONTEXT = RemediationContext(
    cluster="chaos-pilot-cluster",
    service="chaos-pilot-service",
    known_good_task_definition="app:1",
)


class FakeExecutor:
    def __init__(self, action=ROLLBACK):
        self.action = action
        self.calls = []

    def execute(self, context):
        self.calls.append(context)
        return f"ran {self.action}"


def make_engine(*executors, allowed_actions=DEFAULT_RULES):
    return RemediationEngine(executors=executors, allowed_actions=allowed_actions)


def decision(kind, action=ROLLBACK):
    return PolicyDecision(action, kind, ("test",))


def test_deny_never_executes():
    executor = FakeExecutor()

    with pytest.raises(RemediationRefused):
        make_engine(executor).execute(decision(Decision.DENY), approved=True, context=CONTEXT)

    assert executor.calls == []


def test_human_approval_without_approval_never_executes():
    executor = FakeExecutor()

    with pytest.raises(RemediationRefused):
        make_engine(executor).execute(
            decision(Decision.HUMAN_APPROVAL), approved=False, context=CONTEXT
        )

    assert executor.calls == []


def test_human_approval_with_approval_executes_once():
    executor = FakeExecutor()

    result = make_engine(executor).execute(
        decision(Decision.HUMAN_APPROVAL), approved=True, context=CONTEXT
    )

    assert executor.calls == [CONTEXT]
    assert result == RemediationResult(ROLLBACK, True, f"ran {ROLLBACK}")


def test_auto_executes_once():
    executor = FakeExecutor()

    result = make_engine(executor).execute(
        decision(Decision.AUTO), approved=False, context=CONTEXT
    )

    assert executor.calls == [CONTEXT]
    assert result.executed
    assert result.action == ROLLBACK


def test_action_not_allow_listed_is_refused():
    executor = FakeExecutor(action="delete_everything")

    with pytest.raises(RemediationRefused, match="not an allow-listed action"):
        make_engine(executor).execute(
            decision(Decision.AUTO, action="delete_everything"),
            approved=True,
            context=CONTEXT,
        )

    assert executor.calls == []


def test_allow_listed_action_without_executor_is_refused():
    executor = FakeExecutor()

    with pytest.raises(RemediationRefused, match="No executor"):
        make_engine(executor).execute(
            decision(Decision.AUTO, action="restore_dependency"),
            approved=True,
            context=CONTEXT,
        )

    assert executor.calls == []


def test_only_the_matching_executor_runs():
    rollback = FakeExecutor()
    restore = FakeExecutor(action="restore_security_group_rule")

    make_engine(rollback, restore).execute(
        decision(Decision.HUMAN_APPROVAL, action="restore_security_group_rule"),
        approved=True,
        context=CONTEXT,
    )

    assert rollback.calls == []
    assert restore.calls == [CONTEXT]


def test_two_executors_for_one_action_are_rejected():
    with pytest.raises(ValueError):
        make_engine(FakeExecutor(), FakeExecutor())
