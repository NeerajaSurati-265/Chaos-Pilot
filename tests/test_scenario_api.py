from fastapi.testclient import TestClient

from app.main import app, get_engine
from chaos_pilot.core.engine import ChaosEngine
from tests.test_pipeline import FakeScenario, make_pipeline


def make_client(monkeypatch, enabled=True):
    if enabled:
        monkeypatch.setenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", "true")
    else:
        monkeypatch.delenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", raising=False)
    engine = ChaosEngine(
        task_failure=object(),
        pipeline=make_pipeline(),
        scenario_factories={"bad-deployment": FakeScenario},
    )
    original = engine.start_scenario
    engine.start_scenario = lambda name: original(name, background=False)
    app.dependency_overrides[get_engine] = lambda: engine
    return TestClient(app)


def test_run_scenario_and_read_report(monkeypatch):
    try:
        client = make_client(monkeypatch)
        started = client.post("/scenarios/bad-deployment")
        assert started.status_code == 202

        report = client.get(f"/incidents/{started.json()['incident_id']}").json()
        assert report["state"] == "RESOLVED"
        assert report["policy_decision"] == "AUTO"
        assert report["executed_remediation"] == "ran rollback_to_previous_revision"
        assert len(client.get("/incidents").json()) == 1
    finally:
        app.dependency_overrides.clear()


def test_scenarios_refused_when_kill_switch_is_on(monkeypatch):
    try:
        client = make_client(monkeypatch, enabled=False)
        assert client.post("/scenarios/bad-deployment").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_unknown_scenario_and_incident(monkeypatch):
    try:
        client = make_client(monkeypatch)
        assert client.post("/scenarios/delete-everything").status_code == 403
        assert client.get("/incidents/nope").status_code == 404
        assert client.post("/incidents/nope/approve").status_code == 409
    finally:
        app.dependency_overrides.clear()
