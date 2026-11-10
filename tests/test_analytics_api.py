import pytest

from app.main import app, get_engine
from chaos_pilot.incidents.models import Decision, Incident, IncidentState as S, PolicyDecision
from tests.test_scenario_api import make_client

TOP_KEYS = {"demo", "totals", "mttr", "outcomes", "stages", "by_policy", "ai", "scatter", "trends", "takeaways"}


@pytest.fixture
def client(monkeypatch):
    try:
        yield make_client(monkeypatch)
    finally:
        app.dependency_overrides.clear()


def test_empty_store_has_the_full_shape_and_no_errors(client):
    data = client.get("/api/analytics").json()
    assert set(data) == TOP_KEYS and data["demo"] is False
    assert data["totals"]["incidents"] == 0 and data["totals"]["resolved_pct"] is None
    assert data["mttr"]["runs"] == [] and data["ai"]["accuracy"] is None
    assert set(data["outcomes"]) == {"RESOLVED", "FAILED", "DENIED", "REJECTED", "ESCALATED", "ABORTED"}
    assert set(data["by_policy"]) == {"AUTO", "HUMAN_APPROVAL"}
    assert set(data["stages"]["avg_seconds"]) == {"detect", "investigate", "decide", "approval_wait", "fix", "verify"}
    assert set(data["takeaways"]) == {"mttr", "outcomes", "stages", "by_policy", "ai", "override", "scatter"}
    assert all(t["enough"] is False for t in data["takeaways"].values())


def test_after_a_real_mocked_run_the_numbers_come_from_the_store(client):
    incident_id = client.post("/scenarios/bad-deployment").json()["incident_id"]
    data = client.get("/api/analytics").json()
    assert data["totals"]["incidents"] == 1 and data["totals"]["resolved"] == 1
    assert data["totals"]["resolved_pct"] == 1.0
    run = data["mttr"]["runs"][0]
    assert run["incident_id"] == incident_id and run["outcome"] == "RESOLVED" and run["n"] == 1
    assert run["recovery_seconds"] == data["mttr"]["avg_seconds"] >= 0
    assert data["ai"]["correct"] == 1 and data["ai"]["accuracy"] == 1.0
    assert data["by_policy"]["AUTO"]["n"] == 1
    assert data["scatter"][0]["incident_id"] == incident_id


def test_a_running_incident_is_counted_but_excluded(client):
    engine = app.dependency_overrides[get_engine]()
    incident = engine.store.add(Incident(scenario="config_change"))
    for state in (S.INJECTED, S.DETECTED, S.INVESTIGATING):
        incident.transition(state)
    incident.policy_decision = PolicyDecision("restore_security_group_rule", Decision.HUMAN_APPROVAL, ("r",))
    incident.transition(S.POLICY_DECIDED)
    incident.transition(S.AWAITING_APPROVAL)
    data = client.get("/api/analytics").json()
    assert data["totals"]["running"] == 1 and data["totals"]["awaiting_approval"] == 1
    assert sum(data["outcomes"].values()) == 0 and data["totals"]["resolved_pct"] is None
