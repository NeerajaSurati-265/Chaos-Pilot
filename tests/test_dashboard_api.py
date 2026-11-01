from datetime import datetime

import pytest

from app.main import app, get_engine
from chaos_pilot.incidents.models import (
    Decision,
    Incident,
    IncidentState as S,
    PolicyDecision,
)
from tests.test_scenario_api import make_client


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    try:
        yield make_client(monkeypatch)
    finally:
        app.dependency_overrides.clear()


def engine():
    return app.dependency_overrides[get_engine]()


def run_one(client):
    return client.post("/scenarios/bad-deployment").json()["incident_id"]


def test_config_reports_gate_workload_actions_and_timeout(client, monkeypatch):
    config = client.get("/api/config").json()
    assert config["experiments_enabled"] is True and config["busy"] is False
    assert config["kill_switch"]["engaged"] is False
    assert config["workload"]["region"] is None
    assert config["workload"]["service"] == engine().service
    assert config["approval_timeout_seconds"] == 5
    assert config["actions"]["rollback_to_previous_revision"] == {
        "reversible": True, "blast_radius": "LOW", "auto_allowed": True}
    assert config["actions"]["restore_security_group_rule"]["auto_allowed"] is False


def test_config_region_comes_from_the_environment(client, monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    assert client.get("/api/config").json()["workload"]["region"] == "ap-south-1"


def test_config_reflects_disabled_gate_busy_and_kill_switch(client, monkeypatch):
    monkeypatch.delenv("CHAOS_PILOT_EXPERIMENTS_ENABLED")
    assert client.get("/api/config").json()["experiments_enabled"] is False
    client.post("/kill-switch/engage", params={"reason": "tests"})
    assert client.get("/api/config").json()["kill_switch"]["reason"] == "tests"
    engine()._running.acquire()
    assert client.get("/api/config").json()["busy"] is True


def test_scenarios_list_runnable_and_coming_soon_honestly(client):
    cards = {c["key"]: c for c in client.get("/api/scenarios").json()}
    assert list(cards) == ["bad-deployment", "config-change", "dependency-failure", "cascading-failure"]
    assert cards["bad-deployment"]["runnable"] is True
    assert cards["config-change"]["runnable"] is False  # no factory in this test engine
    for key in ("dependency-failure", "cascading-failure"):
        card = cards[key]
        assert card["runnable"] is False and card["policy"] is None
        assert card["runs"] is None and card["avg_recovery_seconds"] is None


def test_scenario_policy_is_derived_from_the_policy_rules(client):
    cards = {c["key"]: c for c in client.get("/api/scenarios").json()}
    assert cards["bad-deployment"]["policy"] == "AUTO"
    assert cards["config-change"]["policy"] == "HUMAN_APPROVAL"
    assert cards["bad-deployment"]["we_will"] and cards["bad-deployment"]["fix"]


def test_scenario_runs_and_average_come_from_the_store(client):
    before = client.get("/api/scenarios").json()[0]
    assert before["runs"] == 0 and before["avg_recovery_seconds"] is None
    run_one(client)
    after = client.get("/api/scenarios").json()[0]
    assert after["runs"] == 1 and after["recovered"] == 1
    assert after["avg_recovery_seconds"] is not None and after["avg_recovery_seconds"] >= 0


def test_incident_endpoints_carry_derived_fields(client):
    incident_id = run_one(client)
    one = client.get(f"/incidents/{incident_id}").json()
    assert one["outcome"] == "RESOLVED" and one["stopped_at"] is None
    assert set(one["stage_seconds"]) == {"break", "notice", "investigate", "decide", "approval_wait", "fix", "verify"}
    assert one["approval_deadline"] is None
    assert client.get("/incidents").json()[0]["outcome"] == "RESOLVED"


def test_approval_deadline_and_remaining_are_server_side(client):
    store = engine().store
    incident = store.add(Incident(scenario="config_change"))
    for state in (S.INJECTED, S.DETECTED, S.INVESTIGATING):
        incident.transition(state)
    incident.policy_decision = PolicyDecision("restore_security_group_rule", Decision.HUMAN_APPROVAL, ("needs approval",))
    incident.transition(S.POLICY_DECIDED)
    incident.transition(S.AWAITING_APPROVAL)
    view = client.get(f"/incidents/{incident.id}").json()
    assert view["outcome"] == "AWAITING_APPROVAL"
    assert 0 < view["approval_seconds_remaining"] <= 5
    assert datetime.fromisoformat(view["approval_deadline"]) > datetime.fromisoformat(view["timeline"][-1]["at"])
    assert view["approval_timeout_seconds"] == 5


def test_markdown_download(client):
    incident_id = run_one(client)
    response = client.get(f"/api/reports/{incident_id}.md")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert f'filename="{incident_id}.md"' in response.headers["content-disposition"]
    assert f"# Incident report {incident_id}" in response.text
    assert "Verification" in response.text


def test_json_download(client):
    incident_id = run_one(client)
    response = client.get(f"/api/reports/{incident_id}.json")
    assert response.status_code == 200
    assert f'filename="{incident_id}.json"' in response.headers["content-disposition"]
    body = response.json()
    assert body["incident_id"] == incident_id and body["outcome"] == "RESOLVED"


def test_downloads_404_for_unknown_incident(client):
    assert client.get("/api/reports/nope.md").status_code == 404
    assert client.get("/api/reports/nope.json").status_code == 404


def test_report_page_is_served_for_any_id(client):
    page = client.get("/reports/inc-whatever")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "report-root" in page.text
