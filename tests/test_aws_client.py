from pathlib import Path

import pytest
from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError

from chaos_pilot.core.aws import AWS_CONFIG, make_client
from chaos_pilot.core.pipeline import Pipeline
from chaos_pilot.incidents.models import Decision, IncidentState
from chaos_pilot.incidents.store import IncidentStore
from chaos_pilot.investigator.rule_based import RuleBasedInvestigator
from chaos_pilot.policy.engine import DEFAULT_RULES, PolicyEngine
from chaos_pilot.remediation.engine import (
    RemediationContext,
    RemediationEngine,
    RemediationStatus,
)
from chaos_pilot.remediation.executors import RollbackToPreviousRevision
from tests.test_pipeline import FakeScenario, run
from tests.test_remediation_engine import decision

S = IncidentState
ROOT = Path(__file__).resolve().parent.parent
GOOD = "arn:aws:ecs:ap-south-1:123456789012:task-definition/chaos-pilot-app:1"
HEALTHY_CONTEXT = RemediationContext(
    cluster="chaos-pilot-cluster", service="chaos-pilot-service",
    known_good_task_definition=GOOD, known_good_was_healthy=True,
)
READ_TIMEOUT = ReadTimeoutError(endpoint_url="https://ecs.ap-south-1.amazonaws.com")
CONNECT_TIMEOUT = ConnectTimeoutError(endpoint_url="https://ecs.ap-south-1.amazonaws.com")


@pytest.mark.parametrize("service", ["ecs", "ec2", "elbv2"])
def test_factory_clients_have_bounded_timeouts_and_retries(service):
    config = make_client(service, region_name="ap-south-1").meta.config

    assert config.connect_timeout == 5
    assert config.read_timeout == 15
    # botocore's max_attempts counts retries, so 3 means up to 4 calls in total.
    assert config.retries["mode"] == "standard"
    assert config.retries["total_max_attempts"] == 4
    assert AWS_CONFIG.read_timeout == 15


def test_only_the_factory_creates_boto3_clients():
    factory = ROOT / "chaos_pilot" / "core" / "aws.py"
    offenders = [
        str(path.relative_to(ROOT))
        for folder in ("chaos_pilot", "app")
        for path in (ROOT / folder).rglob("*.py")
        if path != factory
        and any(call in path.read_text() for call in ("boto3.client(", "boto3.resource("))
    ]

    assert offenders == []


def rollback_with_ecs(monkeypatch, **failing):
    executor = RollbackToPreviousRevision(ecs_client=make_client("ecs", "ap-south-1"))
    monkeypatch.setattr(
        executor.ecs, "describe_task_definition", lambda **_: {"taskDefinition": {"status": "ACTIVE"}}
    )
    monkeypatch.setattr(
        executor.ecs, "describe_services", lambda **_: {"services": [{"status": "ACTIVE"}]}
    )
    for method, error in failing.items():
        def raise_error(**_kwargs):
            raise error
        monkeypatch.setattr(executor.ecs, method, raise_error)
    return executor


@pytest.mark.parametrize("method", ["describe_task_definition", "update_service"])
@pytest.mark.parametrize("error", [READ_TIMEOUT, CONNECT_TIMEOUT])
def test_timeout_during_remediation_is_a_failed_result(monkeypatch, method, error):
    executor = rollback_with_ecs(monkeypatch, **{method: error})
    engine = RemediationEngine([executor], allowed_actions=DEFAULT_RULES)

    result = engine.execute(decision(Decision.AUTO), approved=False, context=HEALTHY_CONTEXT)

    assert result.status is RemediationStatus.FAILED
    assert type(error).__name__ in result.error


def pipeline_with(executor):
    return Pipeline(
        store=IncidentStore(),
        investigator=RuleBasedInvestigator(),
        policy=PolicyEngine(),
        remediation=RemediationEngine([executor], allowed_actions=DEFAULT_RULES),
        experiments_enabled=lambda: True,
        approval_timeout=5,
    )


def test_timeout_during_remediation_fails_the_incident_with_the_error_on_the_timeline(monkeypatch):
    scenario = FakeScenario()
    scenario.remediation_context = lambda: HEALTHY_CONTEXT
    incident = run(pipeline_with(rollback_with_ecs(monkeypatch, update_service=READ_TIMEOUT)), scenario)

    assert incident.state is S.FAILED
    assert "ReadTimeoutError" in incident.failure_reason
    assert any("ReadTimeoutError" in e.note for e in incident.timeline)
    assert incident.verification is None
    assert scenario.cleaned_up


def test_timeout_during_verification_fails_the_incident_with_the_error_on_the_timeline(monkeypatch):
    class TimingOutVerify(FakeScenario):
        def remediation_context(self):
            return HEALTHY_CONTEXT

        def verify(self):
            raise CONNECT_TIMEOUT

    monkeypatch.setattr(RollbackToPreviousRevision, "execute", lambda self, context: "rolled back")
    monkeypatch.setattr(RollbackToPreviousRevision, "check_preconditions", lambda self, context: None)
    scenario = TimingOutVerify()
    incident = run(pipeline_with(RollbackToPreviousRevision(ecs_client=object())), scenario)

    assert incident.state is S.FAILED
    assert "ConnectTimeoutError" in incident.failure_reason
    assert any("ConnectTimeoutError" in e.note for e in incident.timeline)
    assert scenario.cleaned_up
