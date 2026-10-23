import boto3
import pytest
from botocore.stub import Stubber

from chaos_pilot.incidents.models import Decision
from chaos_pilot.investigator.rule_based import RuleBasedInvestigator
from chaos_pilot.policy.engine import PolicyEngine
from chaos_pilot.remediation.engine import RemediationContext
from chaos_pilot.remediation.executors import RestoreSecurityGroupRule
from chaos_pilot.scenarios.config_change import ConfigChangeScenario

CLUSTER = "chaos-pilot-cluster"
SERVICE = "chaos-pilot-service"
TG = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:targetgroup/tg/abc"
ECS_SG = "sg-0ecs0000000000000"
ALB_SG = "sg-0alb0000000000000"
PERMISSION = {
    "IpProtocol": "tcp",
    "FromPort": 80,
    "ToPort": 80,
    "UserIdGroupPairs": [{"GroupId": ALB_SG}],
}


@pytest.fixture
def clients():
    ec2 = boto3.client("ec2", region_name="ap-south-1")
    ecs = boto3.client("ecs", region_name="ap-south-1")
    elbv2 = boto3.client("elbv2", region_name="ap-south-1")
    with Stubber(ec2) as ec2_stub, Stubber(ecs) as ecs_stub, Stubber(elbv2) as elb_stub:
        yield (ec2, ec2_stub), (ecs, ecs_stub), (elbv2, elb_stub)
        for stub in (ec2_stub, ecs_stub, elb_stub):
            stub.assert_no_pending_responses()


@pytest.fixture
def scenario(clients):
    (ec2, _), (ecs, _), (elbv2, _) = clients
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    return ConfigChangeScenario(
        CLUSTER, SERVICE, TG, ECS_SG, ALB_SG,
        ec2_client=ec2, ecs_client=ecs, elbv2_client=elbv2,
        detect_timeout=30, verify_timeout=30, poll_interval=15,
        sleep=sleep, clock=lambda: now[0],
    )


def expect_rule(ec2_stub, present):
    permissions = [{
        "IpProtocol": "tcp", "FromPort": 80, "ToPort": 80,
        "UserIdGroupPairs": [{"GroupId": ALB_SG}],
    }] if present else []
    ec2_stub.add_response(
        "describe_security_groups",
        {"SecurityGroups": [{"GroupId": ECS_SG, "IpPermissions": permissions}]},
        {"GroupIds": [ECS_SG]},
    )


def expect_targets(elb_stub, *states):
    elb_stub.add_response(
        "describe_target_health",
        {"TargetHealthDescriptions": [
            {"Target": {"Id": f"10.0.2.{i}"}, "TargetHealth": {"State": s}}
            for i, s in enumerate(states)
        ]},
        {"TargetGroupArn": TG},
    )


def expect_service(ecs_stub, running):
    ecs_stub.add_response(
        "describe_services",
        {"services": [{"desiredCount": 2, "runningCount": running}]},
        {"cluster": CLUSTER, "services": [SERVICE]},
    )


def test_inject_removes_the_alb_rule(clients, scenario):
    (_, ec2_stub), _, _ = clients
    expect_rule(ec2_stub, True)
    ec2_stub.add_response(
        "revoke_security_group_ingress", {"Return": True},
        {"GroupId": ECS_SG, "IpPermissions": [PERMISSION]},
    )

    scenario.inject()

    assert scenario.rule_removed
    assert scenario.remediation_context() == RemediationContext(
        security_group_id=ECS_SG, source_security_group_id=ALB_SG, port=80
    )


def test_inject_refuses_when_rule_already_missing(clients, scenario):
    (_, ec2_stub), _, _ = clients
    expect_rule(ec2_stub, False)

    with pytest.raises(RuntimeError):
        scenario.inject()


def test_detection_leads_to_approval_required_restore(clients, scenario):
    (_, ec2_stub), _, (_, elb_stub) = clients
    expect_targets(elb_stub, "unhealthy", "unhealthy")
    expect_rule(ec2_stub, False)

    detection = scenario.detect()
    diagnosis = RuleBasedInvestigator().investigate(scenario.name, detection.signals)
    decision = PolicyEngine().decide(diagnosis, experiments_enabled=True)

    assert detection.incident_found
    assert diagnosis.recommended_action == "restore_security_group_rule"
    assert decision.decision is Decision.HUMAN_APPROVAL


def test_restore_and_verify(clients, scenario):
    (ec2, ec2_stub), (_, ecs_stub), (_, elb_stub) = clients
    ec2_stub.add_response(
        "authorize_security_group_ingress", {"Return": True},
        {"GroupId": ECS_SG, "IpPermissions": [PERMISSION]},
    )
    expect_targets(elb_stub, "healthy", "healthy")
    expect_service(ecs_stub, running=2)
    expect_rule(ec2_stub, True)

    RestoreSecurityGroupRule(ec2_client=ec2).execute(scenario.remediation_context())
    assert scenario.verify().passed


def test_cleanup_restores_rule_if_still_removed(clients, scenario):
    (_, ec2_stub), _, _ = clients
    scenario.rule_removed = True
    ec2_stub.add_response(
        "authorize_security_group_ingress", {"Return": True},
        {"GroupId": ECS_SG, "IpPermissions": [PERMISSION]},
    )

    scenario.cleanup()

    assert not scenario.rule_removed


def test_cleanup_tolerates_rule_already_restored(clients, scenario):
    (_, ec2_stub), _, _ = clients
    scenario.rule_removed = True
    ec2_stub.add_client_error(
        "authorize_security_group_ingress",
        service_error_code="InvalidPermission.Duplicate",
    )

    scenario.cleanup()

    assert not scenario.rule_removed
