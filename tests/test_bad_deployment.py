import boto3
import pytest
from botocore.stub import Stubber

from chaos_pilot.scenarios.bad_deployment import BadDeploymentScenario

CLUSTER = "chaos-pilot-cluster"
SERVICE = "chaos-pilot-service"
TG = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:targetgroup/tg/abc"
GOOD = "arn:aws:ecs:ap-south-1:123456789012:task-definition/chaos-pilot-app:1"
BAD = "arn:aws:ecs:ap-south-1:123456789012:task-definition/chaos-pilot-app:2"


@pytest.fixture
def clients():
    ecs = boto3.client("ecs", region_name="ap-south-1")
    elbv2 = boto3.client("elbv2", region_name="ap-south-1")
    with Stubber(ecs) as ecs_stub, Stubber(elbv2) as elb_stub:
        yield ecs, ecs_stub, elbv2, elb_stub
        ecs_stub.assert_no_pending_responses()
        elb_stub.assert_no_pending_responses()


@pytest.fixture
def scenario(clients):
    ecs, _, elbv2, _ = clients
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    return BadDeploymentScenario(
        CLUSTER, SERVICE, TG,
        ecs_client=ecs, elbv2_client=elbv2,
        detect_timeout=30, verify_timeout=30, poll_interval=15,
        sleep=sleep, clock=lambda: now[0],
    )


def service(task_definition, rollout="IN_PROGRESS", running=2, failed=0):
    return {"services": [{
        "serviceName": SERVICE,
        "taskDefinition": task_definition,
        "desiredCount": 2,
        "deployments": [{
            "status": "PRIMARY",
            "taskDefinition": task_definition,
            "rolloutState": rollout,
            "runningCount": running,
            "failedTasks": failed,
        }],
    }]}


def targets(*states):
    return {"TargetHealthDescriptions": [
        {"Target": {"Id": f"10.0.2.{i}"}, "TargetHealth": {"State": s}}
        for i, s in enumerate(states)
    ]}


def expect_service(ecs_stub, body):
    ecs_stub.add_response(
        "describe_services", body, {"cluster": CLUSTER, "services": [SERVICE]}
    )


def expect_targets(elb_stub, body):
    elb_stub.add_response("describe_target_health", body, {"TargetGroupArn": TG})


def test_inject_registers_unhealthy_revision_and_deploys_it(clients, scenario):
    _, ecs_stub, _, _ = clients
    expect_service(ecs_stub, service(GOOD, rollout="COMPLETED"))
    ecs_stub.add_response(
        "describe_task_definition",
        {"taskDefinition": {
            "taskDefinitionArn": GOOD,
            "family": "chaos-pilot-app",
            "revision": 1,
            "status": "ACTIVE",
            "containerDefinitions": [{
                "name": "chaos-pilot-app",
                "image": "repo/test-app:1",
                "environment": [{"name": "HEALTHY", "value": "true"}],
            }],
        }},
        {"taskDefinition": GOOD},
    )
    ecs_stub.add_response(
        "register_task_definition",
        {"taskDefinition": {"taskDefinitionArn": BAD}},
        {
            "family": "chaos-pilot-app",
            "containerDefinitions": [{
                "name": "chaos-pilot-app",
                "image": "repo/test-app:1",
                "environment": [{"name": "HEALTHY", "value": "false"}],
            }],
        },
    )
    ecs_stub.add_response(
        "update_service", {},
        {"cluster": CLUSTER, "service": SERVICE, "taskDefinition": BAD},
    )

    scenario.inject()

    assert scenario.known_good_task_definition == GOOD
    assert scenario.bad_task_definition == BAD


def test_detects_unhealthy_targets_on_new_revision(clients, scenario):
    _, ecs_stub, _, elb_stub = clients
    scenario.known_good_task_definition = GOOD
    expect_service(ecs_stub, service(BAD))
    expect_targets(elb_stub, targets("healthy", "healthy"))
    expect_service(ecs_stub, service(BAD, failed=1))
    expect_targets(elb_stub, targets("healthy", "unhealthy"))

    detection = scenario.detect()

    assert detection.incident_found
    values = {s.name: s.value for s in detection.signals}
    assert values["previous_task_definition"] == GOOD
    assert values["unhealthy_targets"] == 1


def test_detection_gives_up_after_timeout(clients, scenario):
    _, ecs_stub, _, elb_stub = clients
    scenario.known_good_task_definition = GOOD
    for _ in range(3):
        expect_service(ecs_stub, service(BAD))
        expect_targets(elb_stub, targets("healthy", "healthy"))

    assert not scenario.detect().incident_found


def test_rollback_and_verify(clients, scenario):
    _, ecs_stub, _, elb_stub = clients
    scenario.known_good_task_definition = GOOD
    ecs_stub.add_response(
        "update_service", {},
        {"cluster": CLUSTER, "service": SERVICE, "taskDefinition": GOOD},
    )
    expect_service(ecs_stub, service(GOOD, rollout="IN_PROGRESS", running=1))
    expect_targets(elb_stub, targets("healthy", "draining"))
    expect_service(ecs_stub, service(GOOD, rollout="COMPLETED"))
    expect_targets(elb_stub, targets("healthy", "healthy"))

    scenario.remediate("rollback_to_previous_revision")
    result = scenario.verify()

    assert result.passed


def test_verify_reports_failure_when_service_never_recovers(clients, scenario):
    _, ecs_stub, _, elb_stub = clients
    scenario.known_good_task_definition = GOOD
    for _ in range(3):
        expect_service(ecs_stub, service(GOOD, rollout="IN_PROGRESS", running=0))
        expect_targets(elb_stub, targets("unhealthy", "unhealthy"))

    result = scenario.verify()

    assert not result.passed
    failed = {c.name for c in result.checks if not c.passed}
    assert "alb_targets_healthy" in failed


def test_refuses_actions_it_does_not_own(scenario):
    scenario.known_good_task_definition = GOOD
    with pytest.raises(ValueError):
        scenario.remediate("restore_security_group_rule")


def test_cleanup_restores_known_good_and_deregisters_bad(clients, scenario):
    _, ecs_stub, _, _ = clients
    scenario.known_good_task_definition = GOOD
    scenario.bad_task_definition = BAD
    expect_service(ecs_stub, service(BAD))
    ecs_stub.add_response(
        "update_service", {},
        {"cluster": CLUSTER, "service": SERVICE, "taskDefinition": GOOD},
    )
    ecs_stub.add_response(
        "deregister_task_definition", {"taskDefinition": {}}, {"taskDefinition": BAD}
    )

    scenario.cleanup()
