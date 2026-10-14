import boto3
import pytest
from botocore.stub import Stubber
from fastapi.testclient import TestClient

from app.main import app, get_engine
from chaos_pilot.core.engine import ChaosEngine
from chaos_pilot.experiments.task_failure import (
    ExperimentNotAllowed,
    TaskFailureExperiment,
)

CLUSTER = "chaos-pilot-cluster"
SERVICE = "chaos-pilot-service"
TASK_ID = "abc123"


@pytest.fixture
def ecs():
    client = boto3.client("ecs", region_name="ap-south-1")
    with Stubber(client) as stubber:
        yield client, stubber
        stubber.assert_no_pending_responses()


@pytest.fixture
def experiment(ecs):
    client, _ = ecs
    return TaskFailureExperiment(CLUSTER, SERVICE, ecs_client=client)


def stub_describe(stubber, group):
    tasks = [] if group is None else [{"taskArn": TASK_ID, "group": group}]
    stubber.add_response(
        "describe_tasks",
        {"tasks": tasks, "failures": []},
        {"cluster": CLUSTER, "tasks": [TASK_ID]}
    )


def test_stops_task_that_belongs_to_test_service(ecs, experiment):
    _, stubber = ecs
    stub_describe(stubber, f"service:{SERVICE}")
    stubber.add_response(
        "stop_task",
        {"task": {"lastStatus": "RUNNING",
                  "stoppedReason": "Chaos-Pilot task failure experiment"}},
        {"cluster": CLUSTER, "task": TASK_ID,
         "reason": "Chaos-Pilot task failure experiment"}
    )

    result = experiment.stop_task(TASK_ID)

    assert result["task_id"] == TASK_ID
    assert result["status"] == "RUNNING"


def test_refuses_task_from_another_service(ecs, experiment):
    _, stubber = ecs
    stub_describe(stubber, "service:something-else")

    with pytest.raises(ExperimentNotAllowed):
        experiment.stop_task(TASK_ID)


def test_refuses_unknown_task(ecs, experiment):
    _, stubber = ecs
    stub_describe(stubber, None)

    with pytest.raises(ExperimentNotAllowed):
        experiment.stop_task(TASK_ID)


def test_kill_switch_blocks_experiments_by_default(monkeypatch, experiment):
    monkeypatch.delenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", raising=False)
    engine = ChaosEngine(task_failure=experiment)

    with pytest.raises(ExperimentNotAllowed):
        engine.run_task_failure(TASK_ID)


def test_api_returns_403_when_experiments_disabled(monkeypatch, experiment):
    monkeypatch.delenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", raising=False)
    app.dependency_overrides[get_engine] = lambda: ChaosEngine(task_failure=experiment)
    try:
        client = TestClient(app)
        assert client.get("/health").status_code == 200
        response = client.post("/experiments/task-failure", params={"task_id": TASK_ID})
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
