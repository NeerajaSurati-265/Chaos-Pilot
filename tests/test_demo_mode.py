import time

import boto3
import pytest

import chaos_pilot.core.aws as aws
from app import incident_view
from app.main import app, demo_enabled, get_engine
from chaos_pilot.core.engine import ChaosEngine
from chaos_pilot.demo.scenario import DEMO_ABORT_REASON, DemoRestoreRule, DemoRollback, DemoTimings
from chaos_pilot.demo.seed import BD, CC, DEFAULT_PLAN, DemoEngine, Step, build_demo_engine, seed_demo
from chaos_pilot.experiments.task_failure import TaskFailureExperiment
from chaos_pilot.incidents.models import IncidentState as S
from chaos_pilot.scenarios.bad_deployment import BadDeploymentScenario
from chaos_pilot.scenarios.config_change import ConfigChangeScenario
from fastapi.testclient import TestClient
from tests.test_pipeline import make_pipeline


def outcomes(engine):
    return [incident_view.outcome(i) for i in engine.store.list()]


def forbid_aws(monkeypatch):
    """Any boto3 client, client-factory call, real scenario or task-failure experiment fails loudly."""
    def boom(*args, **kwargs):
        raise AssertionError("demo mode touched AWS or built a real scenario")

    monkeypatch.setattr(boto3, "client", boom)
    monkeypatch.setattr(aws, "make_client", boom)
    for cls in (BadDeploymentScenario, ConfigChangeScenario, TaskFailureExperiment):
        monkeypatch.setattr(cls, "__init__", boom)
    # Real executors are built lazily by ChaosEngine.__init__ without a client; make any
    # attempt to *use* a client through them fail too (they go through core.aws -> boto3).
    for name in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.setenv(name, "us-east-1")
    monkeypatch.setenv("CHAOS_PILOT_TARGET_GROUP_ARN", "arn:aws:tg")
    monkeypatch.setenv("CHAOS_PILOT_ECS_SECURITY_GROUP_ID", "sg-ecs")
    monkeypatch.setenv("CHAOS_PILOT_ALB_SECURITY_GROUP_ID", "sg-alb")
    monkeypatch.setenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", "true")


@pytest.fixture(scope="module")
def seeded():
    engine = DemoEngine(DemoTimings.instant())
    spy = []
    original = engine.answer_approval
    engine.answer_approval = lambda i, approved: spy.append((i, approved)) or original(i, approved)
    result = seed_demo(engine, max_wait=2)
    return engine, result, spy


def test_demo_builds_and_seeds_without_aws_even_when_aws_env_is_set(monkeypatch):
    forbid_aws(monkeypatch)
    engine = DemoEngine(DemoTimings.instant())
    result = seed_demo(engine, max_wait=2)
    assert result["stopped_early"] is None and len(result["started"]) == len(DEFAULT_PLAN)
    assert len(engine.store.list()) == len(DEFAULT_PLAN)
    assert all(isinstance(e, (DemoRollback, DemoRestoreRule)) for e in engine.pipeline.remediation.executors.values())
    assert type(engine.task_failure).__name__ == "DemoTaskFailure"
    with pytest.raises(Exception, match="Not available in demo mode"):
        engine.run_task_failure("t-1")


def test_seeded_incidents_have_mixed_outcomes_from_the_real_pipeline(seeded):
    engine, _, _ = seeded
    got = outcomes(engine)
    for expected in ("RESOLVED", "FAILED", "DENIED", "REJECTED", "ESCALATED", "ABORTED"):
        assert expected in got, expected
    assert len(got) == len(DEFAULT_PLAN)
    assert not any(o in ("IN_PROGRESS", "AWAITING_APPROVAL") for o in got)


def test_demo_denial_is_a_real_policy_decision_not_canned_json(seeded):
    engine, _, _ = seeded
    denied = next(i for i in engine.store.list() if incident_view.outcome(i) == "DENIED")
    assert denied.scenario == "bad_deployment"
    assert denied.diagnosis.recommended_action == "restore_security_group_rule"
    assert "does not fit incident type" in denied.policy_decision.reasons[0]
    assert denied.remediation_result is None


def test_human_approvals_go_through_the_real_answer_path(seeded):
    engine, _, spy = seeded
    answered = {i for i, _ in spy}
    waited = {i.id for i in engine.store.list()
              if any(e.state is S.AWAITING_APPROVAL for e in i.timeline)}
    assert answered == waited and len(waited) == 4
    assert sorted(approved for _, approved in spy) == [False, True, True, True]
    assert any(view_has_wait(i) for i in engine.store.list())


def view_has_wait(incident):
    return incident_view.stage_seconds(incident)["approval_wait"] is not None


def test_seeding_is_sequential(seeded):
    engine, _, _ = seeded
    incidents = engine.store.list()
    for earlier, later in zip(incidents, incidents[1:]):
        assert earlier.timeline[-1].at <= later.timeline[0].at


def test_demo_store_is_separate_from_a_real_engine(seeded):
    engine, _, _ = seeded
    real = ChaosEngine(task_failure=object(), pipeline=make_pipeline())
    assert real.store is not engine.store and real.kill_switch is not engine.kill_switch
    assert real.store.list() == [] and getattr(real, "demo", False) is False


def test_get_engine_returns_the_demo_engine_only_when_the_flag_is_on(monkeypatch):
    forbid_aws(monkeypatch)
    monkeypatch.setenv("CHAOS_PILOT_DEMO_DATA", "true")
    monkeypatch.setattr("chaos_pilot.demo.seed.build_demo_engine",
                        lambda: build_demo_engine(DemoTimings.instant(), seed=False))
    get_engine.cache_clear()
    try:
        engine = get_engine()
        assert isinstance(engine, DemoEngine) and demo_enabled()
    finally:
        get_engine.cache_clear()


def test_abort_variant_releases_the_switch_the_demo_engaged():
    engine = DemoEngine(DemoTimings.instant())
    seed_demo(engine, plan=[Step(BD, "abort")], max_wait=2)
    assert outcomes(engine) == ["ABORTED"]
    assert engine.kill_switch.engaged is False


def test_seeder_never_releases_a_switch_the_user_engaged():
    engaged = []
    holder = {}

    def user_hits_the_switch(seconds):
        if not engaged:
            engaged.append(holder["engine"].kill_switch.engage("user stop"))

    timings = DemoTimings(sleep=user_hits_the_switch, poll_interval=0.001)
    timings.ranges = {k: (0, 0) for k in timings.ranges}
    engine = holder["engine"] = DemoEngine(timings)
    result = seed_demo(engine, plan=[Step(BD), Step(BD)], max_wait=0.05, run_timeout=5)
    assert engaged == [True]
    assert outcomes(engine) == ["ABORTED"]  # the user's engage aborted the run in flight
    assert engine.kill_switch.engaged is True and engine.kill_switch.reason == "user stop"
    assert "Kill switch engaged" in result["stopped_early"] and len(result["started"]) == 1


def test_a_release_only_happens_for_the_demo_reason():
    engine = DemoEngine(DemoTimings.instant())
    from chaos_pilot.demo.seed import _release_if_ours

    engine.kill_switch.engage("someone else")
    assert _release_if_ours(engine) is False and engine.kill_switch.engaged
    engine.kill_switch.release()
    engine.kill_switch.engage(DEMO_ABORT_REASON)
    assert _release_if_ours(engine) is True and not engine.kill_switch.engaged


def test_injected_sleeps_are_used_and_seeding_is_fast():
    slept = []
    timings = DemoTimings(sleep=slept.append, poll_interval=0.001)
    engine = DemoEngine(timings)
    started = time.monotonic()
    seed_demo(engine, plan=DEFAULT_PLAN[:2], max_wait=2)
    assert slept and time.monotonic() - started < 5


def test_a_user_started_demo_config_change_waits_for_a_human():
    engine = DemoEngine(DemoTimings.instant())
    incident = engine.start_scenario(CC)
    deadline = time.monotonic() + 5
    while incident.state is not S.AWAITING_APPROVAL and time.monotonic() < deadline:
        time.sleep(0.005)
    assert incident.state is S.AWAITING_APPROVAL and incident.human_intervention
    assert engine.answer_approval(incident.id, True)
    deadline = time.monotonic() + 5
    while incident.state is not S.RESOLVED and time.monotonic() < deadline:
        time.sleep(0.005)
    assert incident.state is S.RESOLVED


def demo_client(monkeypatch):
    monkeypatch.delenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", raising=False)
    engine = DemoEngine(DemoTimings.instant())
    seed_demo(engine, plan=DEFAULT_PLAN[:3], max_wait=2)
    app.dependency_overrides[get_engine] = lambda: engine
    return TestClient(app), engine


def test_api_marks_demo_data_everywhere(monkeypatch):
    try:
        client, engine = demo_client(monkeypatch)
        config = client.get("/api/config").json()
        assert config["demo"] is True and config["experiments_enabled"] is True
        assert config["workload"] == {"cluster": "demo-cluster", "service": "demo-service", "region": "mocked"}
        assert client.get("/api/analytics").json()["demo"] is True
        first = client.get("/incidents").json()[0]
        assert first["demo"] is True and first["demo_notice"].startswith("DEMO DATA")
        md = client.get(f"/api/reports/{first['incident_id']}.md").text
        assert "DEMO DATA: sample incident from mocked AWS" in md
        body = client.get(f"/api/reports/{first['incident_id']}.json").json()
        assert body["demo"] is True and body["demo_notice"].startswith("DEMO DATA")
    finally:
        app.dependency_overrides.clear()


def test_real_mode_exports_carry_no_demo_marker(monkeypatch):
    from tests.test_scenario_api import make_client

    try:
        client = make_client(monkeypatch)
        incident_id = client.post("/scenarios/bad-deployment").json()["incident_id"]
        assert client.get("/api/config").json()["demo"] is False
        assert client.get("/api/analytics").json()["demo"] is False
        assert "DEMO DATA" not in client.get(f"/api/reports/{incident_id}.md").text
        assert client.get(f"/api/reports/{incident_id}.json").json()["demo_notice"] is None
    finally:
        app.dependency_overrides.clear()


def test_demo_analytics_reflect_the_mixed_outcomes(seeded):
    from app import analytics

    engine, _, _ = seeded
    data = analytics.compute([incident_view.build(i, 900, demo=True) for i in engine.store.list()], demo=True)
    assert data["demo"] is True and data["totals"]["incidents"] == len(DEFAULT_PLAN)
    assert data["ai"]["escalated"] == 1 and data["ai"]["wrong"] == 1
    assert data["ai"]["accuracy"] == pytest.approx(data["ai"]["correct"] / data["ai"]["diagnosed"])
    assert data["stages"]["samples"]["approval_wait"] == 3
