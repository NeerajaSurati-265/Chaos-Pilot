import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from chaos_pilot.remediation.engine import RemediationContext
from chaos_pilot.remediation.executors import (
    RestoreSecurityGroupRule,
    RollbackToPreviousRevision,
)

CLUSTER = "chaos-pilot-cluster"
SERVICE = "chaos-pilot-service"
GOOD = "arn:aws:ecs:ap-south-1:123456789012:task-definition/chaos-pilot-app:1"
ECS_SG = "sg-0ecs0000000000000"
ALB_SG = "sg-0alb0000000000000"
PERMISSION = {
    "IpProtocol": "tcp",
    "FromPort": 80,
    "ToPort": 80,
    "UserIdGroupPairs": [{"GroupId": ALB_SG}],
}
SG_CONTEXT = RemediationContext(
    security_group_id=ECS_SG, source_security_group_id=ALB_SG, port=80
)


@pytest.fixture
def ecs():
    client = boto3.client("ecs", region_name="ap-south-1")
    with Stubber(client) as stub:
        yield client, stub
        stub.assert_no_pending_responses()


@pytest.fixture
def ec2():
    client = boto3.client("ec2", region_name="ap-south-1")
    with Stubber(client) as stub:
        yield client, stub
        stub.assert_no_pending_responses()


def test_rollback_updates_service_to_known_good_revision(ecs):
    client, stub = ecs
    stub.add_response(
        "update_service", {},
        {"cluster": CLUSTER, "service": SERVICE, "taskDefinition": GOOD},
    )
    context = RemediationContext(
        cluster=CLUSTER, service=SERVICE, known_good_task_definition=GOOD
    )

    detail = RollbackToPreviousRevision(ecs_client=client).execute(context)

    assert detail == f"Rolled back to {GOOD}"


def test_rollback_refuses_without_known_good_revision(ecs):
    client, _ = ecs
    context = RemediationContext(cluster=CLUSTER, service=SERVICE)

    with pytest.raises(RuntimeError, match="known_good_task_definition"):
        RollbackToPreviousRevision(ecs_client=client).execute(context)


def test_restore_re_adds_the_removed_rule(ec2):
    client, stub = ec2
    stub.add_response(
        "authorize_security_group_ingress", {"Return": True},
        {"GroupId": ECS_SG, "IpPermissions": [PERMISSION]},
    )

    detail = RestoreSecurityGroupRule(ec2_client=client).execute(SG_CONTEXT)

    assert detail == f"Restored tcp/80 from {ALB_SG} on {ECS_SG}"


def test_restore_tolerates_rule_already_present(ec2):
    client, stub = ec2
    stub.add_client_error(
        "authorize_security_group_ingress",
        service_error_code="InvalidPermission.Duplicate",
    )

    RestoreSecurityGroupRule(ec2_client=client).execute(SG_CONTEXT)


def test_restore_raises_other_aws_errors(ec2):
    client, stub = ec2
    stub.add_client_error(
        "authorize_security_group_ingress", service_error_code="UnauthorizedOperation"
    )

    with pytest.raises(ClientError):
        RestoreSecurityGroupRule(ec2_client=client).execute(SG_CONTEXT)


def test_restore_refuses_without_security_groups(ec2):
    client, _ = ec2

    with pytest.raises(RuntimeError, match="security_group_id"):
        RestoreSecurityGroupRule(ec2_client=client).execute(RemediationContext(port=80))
