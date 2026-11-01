from datetime import datetime, timedelta, timezone

import boto3
import pytest
from botocore.stub import Stubber

from chaos_pilot.core.engine import ChaosEngine
from chaos_pilot.incidents.models import (
    CleanupResult,
    Incident,
    IncidentState,
    TimelineEvent,
)
from chaos_pilot.scenarios.bad_deployment import BadDeploymentScenario
from chaos_pilot.scenarios.config_change import ConfigChangeScenario
from tests.test_bad_deployment import BAD, CLUSTER, GOOD, SERVICE, TG, service
from tests.test_config_change import ALB_SG, ECS_SG, PERMISSION, expect_rule
from tests.test_pipeline import UNCLEAR_SIGNALS, FakeScenario, make_pipeline, run

S = IncidentState

REPORT_KEYS = {
    # projectcontext.md section 3: "Resolution report must contain"
    "incident_id", "scenario", "detected_signals", "likely_cause", "confidence",
    "recommended_action", "policy_decision", "policy_reasons", "executed_remediation",
    "verification", "recovery_seconds", "human_intervention",
    # additions
    "state", "detect_seconds", "failure_reason", "abort_reason", "pre_injection_state",
    "injected_fault", "cleanup", "timeline",
}


# --- fixtures -------------------------------------------------------------------


@pytest.fixture
def bad_deployment():
    ecs = boto3.client("ecs", region_name="ap-south-1")
    elbv2 = boto3.client("elbv2", region_name="ap-south-1")
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    with Stubber(ecs) as ecs_stub, Stubber(elbv2) as elb_stub:
        scenario = BadDeploymentScenario(
            CLUSTER, SERVICE, TG, ecs_client=ecs, elbv2_client=elbv2,
            cleanup_verify_timeout=10, cleanup_verify_interval=5,
            sleep=sleep, clock=lambda: now[0],
        )
        yield scenario, ecs_stub
        ecs_stub.assert_no_pending_responses()
        elb_stub.assert_no_pending_responses()


@pytest.fixture
def config_change():
    ec2 = boto3.client("ec2", region_name="ap-south-1")
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    with Stubber(ec2) as ec2_stub:
        scenario = ConfigChangeScenario(
            CLUSTER, SERVICE, TG, ECS_SG, ALB_SG,
            ec2_client=ec2, ecs_client=object(), elbv2_client=object(),
            cleanup_verify_timeout=10, cleanup_verify_interval=5,
            sleep=sleep, clock=lambda: now[0],
        )
        yield scenario, ec2_stub
        ec2_stub.assert_no_pending_responses()


def expect_services(ecs_stub, task_definition):
    ecs_stub.add_response(
        "describe_services", service(task_definition, rollout="IN_PROGRESS"),
        {"cluster": CLUSTER, "services": [SERVICE]},
    )


# --- injection: PENDING until inject() succeeds ------------------------------------


class FailingInject(FakeScenario):
    def inject(self):
        raise RuntimeError("register_task_definition timed out")


def test_failed_injection_is_failed_and_never_injected():
    scenario = FailingInject()
    incident = run(make_pipeline(), scenario)

    assert incident.state is S.FAILED
    assert S.INJECTED not in [event.state for event in incident.timeline]
    assert "register_task_definition timed out" in incident.failure_reason
    assert incident.injected_fault is None
    assert incident.pre_injection_state == {"task_definition": "app:1"}
    assert incident.cleanup.attempted  # the fault may be half applied, so cleanup runs


def test_incident_is_pending_until_the_injection_succeeds():
    pipeline = make_pipeline()
    incident = pipeline.start(FakeScenario())
    assert incident.state is S.PENDING

    pipeline.run(incident, FakeScenario())
    states = [event.state for event in incident.timeline]
    assert states[:3] == [S.PENDING, S.INJECTED, S.DETECTED]
    assert incident.injected_fault == {"type": "fake_fault"}


def test_bad_deployment_records_pre_injection_state_and_fault(bad_deployment):
    scenario, ecs_stub = bad_deployment
    expect_services(ecs_stub, GOOD)
    ecs_stub.add_response(
        "describe_task_definition",
        {"taskDefinition": {
            "taskDefinitionArn": GOOD, "family": "chaos-pilot-app", "revision": 1,
            "containerDefinitions": [{"name": "app", "image": "img", "environment": []}],
        }},
        {"taskDefinition": GOOD},
    )
    ecs_stub.add_response(
        "register_task_definition", {"taskDefinition": {"taskDefinitionArn": BAD}},
        {"family": "chaos-pilot-app", "containerDefinitions": [{
            "name": "app", "image": "img",
            "environment": [{"name": "HEALTHY", "value": "false"}],
        }]},
    )
    ecs_stub.add_response(
        "update_service", {}, {"cluster": CLUSTER, "service": SERVICE, "taskDefinition": BAD}
    )

    injection = scenario.inject()

    assert injection.injected_fault == {
        "type": "unhealthy_task_definition_revision",
        "task_definition": BAD,
        "env": {"HEALTHY": "false"},
    }
    assert scenario.pre_injection_state() == {
        "cluster": CLUSTER, "service": SERVICE, "task_definition": GOOD,
        "known_good_was_healthy": False,
    }


def test_bad_deployment_has_no_pre_injection_state_before_capture(bad_deployment):
    assert bad_deployment[0].pre_injection_state() == {}


def test_config_change_records_pre_injection_state_and_fault(config_change):
    scenario, ec2_stub = config_change
    ec2_stub.add_response(
        "describe_security_groups",
        {"SecurityGroups": [{"GroupId": ECS_SG, "IpPermissions": [{
            "IpProtocol": "tcp", "FromPort": 80, "ToPort": 80,
            "UserIdGroupPairs": [{"GroupId": ALB_SG, "Description": "Allow HTTP from ALB"}],
        }]}]},
        {"GroupIds": [ECS_SG]},
    )
    ec2_stub.add_response(
        "revoke_security_group_ingress", {"Return": True},
        {"GroupId": ECS_SG, "IpPermissions": [PERMISSION]},
    )

    injection = scenario.inject()

    identity = {
        "security_group_id": ECS_SG, "protocol": "tcp", "port": 80,
        "source_security_group_id": ALB_SG,
    }
    assert injection.injected_fault == {"type": "security_group_rule_removed", **identity}
    assert scenario.pre_injection_state() == {**identity, "description": "Allow HTTP from ALB"}


# --- verify_cleanup (read-only, bounded) ---------------------------------------------


def test_bad_deployment_cleanup_verified(bad_deployment):
    scenario, ecs_stub = bad_deployment
    scenario.known_good_task_definition = GOOD
    expect_services(ecs_stub, GOOD)

    result = scenario.verify_cleanup()

    assert result.passed
    assert GOOD in result.detail


def test_bad_deployment_cleanup_verification_retries_until_the_revert_shows(bad_deployment):
    scenario, ecs_stub = bad_deployment
    scenario.known_good_task_definition = GOOD
    expect_services(ecs_stub, BAD)
    expect_services(ecs_stub, GOOD)

    assert scenario.verify_cleanup().passed


def test_bad_deployment_cleanup_verification_gives_up_after_the_deadline(bad_deployment):
    scenario, ecs_stub = bad_deployment
    scenario.known_good_task_definition = GOOD
    for _ in range(3):  # t=0, 5, 10 with a 10 s deadline
        expect_services(ecs_stub, BAD)

    result = scenario.verify_cleanup()

    assert not result.passed
    assert BAD in result.detail


def test_config_change_cleanup_verified(config_change):
    scenario, ec2_stub = config_change
    scenario.known_good_rule_captured = True
    expect_rule(ec2_stub, True)

    assert scenario.verify_cleanup().passed


def test_config_change_cleanup_verification_sees_the_rule_still_missing(config_change):
    scenario, ec2_stub = config_change
    scenario.known_good_rule_captured = True
    for _ in range(3):
        expect_rule(ec2_stub, False)

    result = scenario.verify_cleanup()

    assert not result.passed
    assert "still missing" in result.detail


def test_config_change_cleanup_verification_error_propagates_for_the_pipeline_to_record(
    config_change,
):
    scenario, ec2_stub = config_change
    scenario.known_good_rule_captured = True
    ec2_stub.add_client_error("describe_security_groups", service_error_code="RequestLimitExceeded")

    with pytest.raises(Exception):
        scenario.verify_cleanup()


# --- cleanup result in the pipeline --------------------------------------------------


def test_verified_cleanup_is_recorded():
    incident = run(make_pipeline(), FakeScenario())

    assert incident.state is S.RESOLVED
    assert incident.cleanup == CleanupResult(
        attempted=True, succeeded=True, error=None, verified=True,
        verification_detail="fake revert check",
    )
    assert incident.timeline[-1].note == "Cleanup completed, revert verified: fake revert check"


def test_unverified_cleanup_is_recorded_honestly_and_state_is_unchanged(caplog):
    scenario = FakeScenario()
    scenario.cleanup_verified = False
    with caplog.at_level("ERROR", logger="chaos_pilot.core.pipeline"):
        incident = run(make_pipeline(), scenario)

    assert incident.state is S.RESOLVED  # recovery was proven; the cleanup field says otherwise
    assert incident.cleanup.succeeded is True
    assert incident.cleanup.verified is False
    assert incident.report()["cleanup"]["verified"] is False
    assert "revert NOT verified" in incident.timeline[-1].note
    assert any(incident.id in r.getMessage() and "not verified" in r.getMessage()
               for r in caplog.records)


def test_cleanup_that_raises_records_the_error_and_still_verifies():
    scenario = FakeScenario()

    def broken_cleanup():
        raise RuntimeError("AWS throttled")

    scenario.cleanup = broken_cleanup
    scenario.cleanup_verified = False
    incident = run(make_pipeline(), scenario)

    assert incident.cleanup.attempted
    assert incident.cleanup.succeeded is False
    assert incident.cleanup.error == "AWS throttled"
    assert incident.cleanup.verified is False
    assert incident.timeline[-1].note.startswith("Cleanup failed: AWS throttled")


def test_verification_error_is_recorded_as_not_verified():
    scenario = FakeScenario()

    def broken_check():
        raise RuntimeError("describe timed out")

    scenario.verify_cleanup = broken_check
    incident = run(make_pipeline(), scenario)

    assert incident.cleanup.succeeded is True
    assert incident.cleanup.verified is False
    assert "verification error: RuntimeError: describe timed out" in (
        incident.cleanup.verification_detail
    )


def test_cleanup_is_skipped_when_nothing_was_injected():
    pipeline = make_pipeline()
    scenario = FakeScenario()
    incident = pipeline.start(scenario)
    pipeline.kill_switch.engage("operator stop")
    pipeline.run(incident, scenario)

    assert incident.state is S.ABORTED
    assert incident.cleanup == CleanupResult(
        attempted=False, skipped_reason="nothing was injected"
    )
    assert not scenario.cleaned_up
    assert incident.timeline[-1].note == "Cleanup skipped: nothing was injected"


# --- report -------------------------------------------------------------------------


def aborted_incident():
    pipeline = make_pipeline()
    scenario = FakeScenario()
    original = scenario.inject

    def inject_then_engage():
        injection = original()
        pipeline.kill_switch.engage("operator stop")  # noticed at the next checkpoint
        return injection

    scenario.inject = inject_then_engage
    return pipeline.run(pipeline.start(scenario), scenario)


@pytest.mark.parametrize("outcome", ["RESOLVED", "FAILED", "ABORTED", "ESCALATED", "REJECTED"])
def test_report_has_every_required_field(outcome):
    if outcome == "RESOLVED":
        incident = run(make_pipeline(), FakeScenario())
    elif outcome == "FAILED":
        incident = run(make_pipeline(), FakeScenario(verified=False))
    elif outcome == "ABORTED":
        incident = aborted_incident()
    elif outcome == "ESCALATED":
        incident = run(make_pipeline(), FakeScenario(signals=UNCLEAR_SIGNALS))
    else:
        scenario = FakeScenario()
        scenario.name = "config_change"  # rollback does not fit this incident type
        incident = run(make_pipeline(), scenario)

    report = incident.report()

    assert incident.state.value == outcome
    assert REPORT_KEYS <= set(report)
    assert report["injected_fault"] == {"type": "fake_fault"}
    assert report["pre_injection_state"] == {"task_definition": "app:1"}
    assert report["cleanup"]["attempted"] is True
    assert report["cleanup"]["verified"] is True
    assert report["detect_seconds"] is not None
    if outcome == "RESOLVED":
        assert report["recovery_seconds"] is not None
    else:
        assert report["recovery_seconds"] is None
    if outcome == "ABORTED":
        assert report["abort_reason"]


# --- durations ----------------------------------------------------------------------

T0 = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)


def timeline(*steps):
    return [TimelineEvent(state, T0 + timedelta(seconds=seconds), "") for state, seconds in steps]


def test_detect_seconds_runs_from_injected_to_detected():
    incident = Incident(
        scenario="bad_deployment",
        timeline=timeline((S.PENDING, 0), (S.INJECTED, 2), (S.DETECTED, 47)),
    )

    assert incident.detect_seconds == 45


def test_detect_seconds_is_none_when_never_detected():
    incident = run(make_pipeline(), FakeScenario(detected=False))

    assert incident.state is S.FAILED
    assert incident.report()["detect_seconds"] is None


def test_detect_seconds_is_none_when_never_injected():
    assert run(make_pipeline(), FailingInject()).report()["detect_seconds"] is None


def test_recovery_seconds_ignores_entries_added_after_resolved():
    incident = Incident(
        scenario="bad_deployment",
        state=S.RESOLVED,
        timeline=timeline(
            (S.PENDING, 0), (S.INJECTED, 1), (S.DETECTED, 10), (S.REMEDIATING, 12),
            (S.VERIFYING, 20), (S.RESOLVED, 70), (S.RESOLVED, 500),  # cleanup note, later
        ),
    )

    assert incident.recovery_seconds == 60


# --- protocol -----------------------------------------------------------------------


def test_every_registered_scenario_can_report_and_verify_cleanup(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    monkeypatch.setenv("CHAOS_PILOT_TARGET_GROUP_ARN", TG)
    monkeypatch.setenv("CHAOS_PILOT_ECS_SECURITY_GROUP_ID", ECS_SG)
    monkeypatch.setenv("CHAOS_PILOT_ALB_SECURITY_GROUP_ID", ALB_SG)
    engine = ChaosEngine(task_failure=object())

    for name, factory in engine.scenario_factories.items():
        scenario = factory()
        assert callable(getattr(scenario, "pre_injection_state", None)), name
        assert callable(getattr(scenario, "verify_cleanup", None)), name
