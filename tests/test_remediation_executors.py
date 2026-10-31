import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from chaos_pilot.incidents.models import Decision, PolicyDecision
from chaos_pilot.policy.engine import DEFAULT_RULES
from chaos_pilot.remediation.engine import (
    PreconditionFailed,
    RemediationContext,
    RemediationEngine,
    RemediationStatus,
)
from chaos_pilot.remediation.executors import (
    ingress_permission,
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
    security_group_id=ECS_SG, source_security_group_id=ALB_SG, port=80,
    known_good_rule_captured=True,
)
DESCRIBED_CONTEXT = RemediationContext(
    security_group_id=ECS_SG, source_security_group_id=ALB_SG, port=80,
    known_good_rule_captured=True, rule_description="Allow HTTP traffic from ALB",
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


def rollback_context(healthy=True):
    return RemediationContext(
        cluster=CLUSTER, service=SERVICE, known_good_task_definition=GOOD,
        known_good_was_healthy=healthy,
    )


def expect_rollback_preconditions(stub, revision_status="ACTIVE", service_status="ACTIVE"):
    stub.add_response(
        "describe_task_definition",
        {"taskDefinition": {"taskDefinitionArn": GOOD, "status": revision_status}},
        {"taskDefinition": GOOD},
    )
    stub.add_response(
        "describe_services",
        {"services": [{"serviceName": SERVICE, "status": service_status}]},
        {"cluster": CLUSTER, "services": [SERVICE]},
    )


def test_rollback_preconditions_pass_for_active_healthy_revision(ecs):
    client, stub = ecs
    expect_rollback_preconditions(stub)

    RollbackToPreviousRevision(ecs_client=client).check_preconditions(rollback_context())


def test_rollback_precondition_fails_if_revision_was_not_healthy(ecs):
    client, _ = ecs

    with pytest.raises(PreconditionFailed, match="not recorded as healthy"):
        RollbackToPreviousRevision(ecs_client=client).check_preconditions(
            rollback_context(healthy=False)
        )


def test_rollback_precondition_fails_if_revision_is_inactive(ecs):
    client, stub = ecs
    stub.add_response(
        "describe_task_definition",
        {"taskDefinition": {"taskDefinitionArn": GOOD, "status": "INACTIVE"}},
        {"taskDefinition": GOOD},
    )

    with pytest.raises(PreconditionFailed, match="INACTIVE"):
        RollbackToPreviousRevision(ecs_client=client).check_preconditions(rollback_context())


def test_rollback_precondition_fails_if_service_is_not_active(ecs):
    client, stub = ecs
    expect_rollback_preconditions(stub, service_status="DRAINING")

    with pytest.raises(PreconditionFailed, match="not ACTIVE"):
        RollbackToPreviousRevision(ecs_client=client).check_preconditions(rollback_context())


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


def test_restore_preconditions_pass_when_both_groups_exist(ec2):
    client, stub = ec2
    stub.add_response(
        "describe_security_groups",
        {"SecurityGroups": [{"GroupId": ECS_SG}, {"GroupId": ALB_SG}]},
        {"GroupIds": [ECS_SG, ALB_SG]},
    )

    RestoreSecurityGroupRule(ec2_client=client).check_preconditions(SG_CONTEXT)


def test_restore_precondition_fails_if_a_group_is_missing(ec2):
    client, stub = ec2
    stub.add_response(
        "describe_security_groups",
        {"SecurityGroups": [{"GroupId": ECS_SG}]},
        {"GroupIds": [ECS_SG, ALB_SG]},
    )

    with pytest.raises(PreconditionFailed):
        RestoreSecurityGroupRule(ec2_client=client).check_preconditions(SG_CONTEXT)


def test_restore_precondition_fails_if_known_good_rule_was_not_captured(ec2):
    client, _ = ec2  # no responses queued: any AWS call would fail the test
    uncaptured = RemediationContext(
        security_group_id=ECS_SG, source_security_group_id=ALB_SG, port=80
    )

    with pytest.raises(PreconditionFailed, match="not captured"):
        RestoreSecurityGroupRule(ec2_client=client).check_preconditions(uncaptured)


def test_restore_without_captured_rule_makes_no_aws_call_and_fails(ec2):
    client, _ = ec2
    engine = RemediationEngine([RestoreSecurityGroupRule(ec2_client=client)], DEFAULT_RULES)
    uncaptured = RemediationContext(
        security_group_id=ECS_SG, source_security_group_id=ALB_SG, port=80
    )

    result = engine.execute(
        PolicyDecision("restore_security_group_rule", Decision.HUMAN_APPROVAL, ("x",)),
        approved=True,
        context=uncaptured,
    )

    assert result.status is RemediationStatus.FAILED
    assert "not captured" in result.error


def test_restore_re_adds_the_captured_rule_description(ec2):
    client, stub = ec2
    permission = {
        **PERMISSION,
        "UserIdGroupPairs": [{"GroupId": ALB_SG, "Description": "Allow HTTP traffic from ALB"}],
    }
    stub.add_response(
        "authorize_security_group_ingress", {"Return": True},
        {"GroupId": ECS_SG, "IpPermissions": [permission]},
    )

    RestoreSecurityGroupRule(ec2_client=client).execute(DESCRIBED_CONTEXT)


def test_restore_through_the_engine_treats_an_existing_rule_as_success(ec2):
    client, stub = ec2
    stub.add_response(
        "describe_security_groups",
        {"SecurityGroups": [{"GroupId": ECS_SG}, {"GroupId": ALB_SG}]},
        {"GroupIds": [ECS_SG, ALB_SG]},
    )
    stub.add_client_error(
        "authorize_security_group_ingress",
        service_error_code="InvalidPermission.Duplicate",
    )
    engine = RemediationEngine([RestoreSecurityGroupRule(ec2_client=client)], DEFAULT_RULES)

    result = engine.execute(
        PolicyDecision("restore_security_group_rule", Decision.HUMAN_APPROVAL, ("x",)),
        approved=True,
        context=SG_CONTEXT,
    )

    assert result.succeeded
    assert result.error is None


@pytest.mark.parametrize("description", [None, ""])
def test_restore_omits_the_description_field_when_there_is_none(ec2, description):
    client, stub = ec2
    stub.add_response(
        "authorize_security_group_ingress", {"Return": True},
        {"GroupId": ECS_SG, "IpPermissions": [PERMISSION]},  # exact: no "Description" key
    )
    context = RemediationContext(
        security_group_id=ECS_SG, source_security_group_id=ALB_SG, port=80,
        known_good_rule_captured=True, rule_description=description,
    )

    RestoreSecurityGroupRule(ec2_client=client).execute(context)

    assert "Description" not in ingress_permission(ALB_SG, 80, description)["UserIdGroupPairs"][0]
