import pytest
from botocore.exceptions import ClientError

from chaos_pilot.incidents.models import Decision, PolicyDecision
from chaos_pilot.policy.engine import DEFAULT_RULES
from chaos_pilot.remediation.engine import (
    PreconditionFailed,
    RemediationContext,
    RemediationEngine,
    RemediationRefused,
    RemediationStatus,
)

ROLLBACK = "rollback_to_previous_revision"
CONTEXT = RemediationContext(
    cluster="chaos-pilot-cluster",
    service="chaos-pilot-service",
    known_good_task_definition="app:1",
)


class FakeExecutor:
    def __init__(self, action=ROLLBACK, precondition_error=None, execute_error=None):
        self.action = action
        self.precondition_error = precondition_error
        self.execute_error = execute_error
        self.calls = []

    def check_preconditions(self, context):
        if self.precondition_error:
            raise PreconditionFailed(self.precondition_error)

    def execute(self, context):
        self.calls.append(context)
        if self.execute_error:
            raise self.execute_error
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
    assert result.succeeded
    assert result.detail == f"ran {ROLLBACK}"


def test_auto_executes_once():
    executor = FakeExecutor()

    result = make_engine(executor).execute(
        decision(Decision.AUTO), approved=False, context=CONTEXT
    )

    assert executor.calls == [CONTEXT]
    assert result.status is RemediationStatus.SUCCEEDED
    assert result.action == ROLLBACK
    assert result.error is None
    assert result.parameters == {
        "cluster": "chaos-pilot-cluster",
        "service": "chaos-pilot-service",
        "known_good_task_definition": "app:1",
        "known_good_was_healthy": False,
    }
    assert result.started_at <= result.finished_at


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


def test_aws_error_becomes_failed_result():
    executor = FakeExecutor(execute_error=ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "UpdateService"
    ))

    result = make_engine(executor).execute(
        decision(Decision.AUTO), approved=False, context=CONTEXT
    )

    assert result.status is RemediationStatus.FAILED
    assert not result.succeeded
    assert "AccessDeniedException" in result.error
    assert result.finished_at >= result.started_at


def test_failed_precondition_skips_execute_and_records_failed():
    executor = FakeExecutor(precondition_error="revision is INACTIVE")

    result = make_engine(executor).execute(
        decision(Decision.AUTO), approved=False, context=CONTEXT
    )

    assert result.status is RemediationStatus.FAILED
    assert result.error == "Precondition failed: revision is INACTIVE"
    assert executor.calls == []


def test_escalation_decision_is_refused_and_nothing_runs():
    executor = FakeExecutor("escalate_to_human")
    engine = RemediationEngine([executor], allowed_actions=["escalate_to_human"])
    escalation = PolicyDecision("escalate_to_human", Decision.ESCALATE, ("human review",))

    with pytest.raises(RemediationRefused):
        engine.execute(escalation, approved=True, context=CONTEXT)
    assert executor.calls == []


def test_escalate_to_human_is_refused_even_if_wrongly_marked_auto():
    executor = FakeExecutor("escalate_to_human")
    engine = make_engine(executor)

    with pytest.raises(RemediationRefused, match="not an allow-listed"):
        engine.execute(
            PolicyDecision("escalate_to_human", Decision.AUTO, ("x",)),
            approved=False,
            context=CONTEXT,
        )
    assert executor.calls == []
