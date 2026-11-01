import threading
import time

import boto3
import pytest
from botocore.stub import Stubber
from fastapi.testclient import TestClient

from app.main import app, get_engine
from chaos_pilot.core.engine import ChaosEngine
from chaos_pilot.core.killswitch import Aborted, KillSwitch
from chaos_pilot.experiments.task_failure import ExperimentNotAllowed
from chaos_pilot.incidents.models import (
    TERMINAL_STATES,
    Incident,
    IncidentState,
    InvalidTransition,
)
from chaos_pilot.scenarios.bad_deployment import BadDeploymentScenario
from chaos_pilot.scenarios.config_change import ConfigChangeScenario
from tests.test_bad_deployment import CLUSTER, GOOD, SERVICE, TG, service, targets
from tests.test_pipeline import (
    CONTEXT,
    UNCLEAR_SIGNALS,
    ApprovalPolicy,
    FakeScenario,
    executed,
    make_pipeline,
    run,
)

S = IncidentState


class HookedScenario(FakeScenario):
    """FakeScenario with the abort_guard attribute and a hook per step."""

    def __init__(self, hooks=None, **kwargs):
        super().__init__(**kwargs)
        self.abort_guard = None
        self.hooks = hooks or {}
        self.injected = False
        self.verify_calls = 0

    def _hook(self, step):
        if step in self.hooks:
            self.hooks[step](self)

    def inject(self):
        self.injected = True
        self._hook("inject")
        return super().inject()

    def detect(self):
        self._hook("detect")
        return super().detect()

    def verify(self):
        self.verify_calls += 1
        self._hook("verify")
        return super().verify()


def wait_for(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert condition()


def notes(incident):
    return [event.note for event in incident.timeline]


# --- KillSwitch ------------------------------------------------------------


def test_engage_and_release_are_idempotent_and_logged(caplog):
    switch = KillSwitch()
    with caplog.at_level("INFO", logger="chaos_pilot.core.killswitch"):
        assert switch.engage("first") is True
        assert switch.engage("second") is False
        assert switch.status()["reason"] == "first"
        assert switch.release() is True
        assert switch.release() is False

    messages = [record.getMessage() for record in caplog.records]
    assert any("ENGAGED at" in m and "first" in m for m in messages)
    assert any("RELEASED at" in m for m in messages)
    assert switch.engage_count == 1
    assert switch.status() == {"engaged": False, "reason": None, "engaged_at": None}


def test_engage_sticks_for_a_run_even_if_released_before_the_checkpoint():
    switch = KillSwitch()
    guard = switch.guard()
    switch.engage("operator")
    switch.release()

    assert not switch.engaged
    with pytest.raises(Aborted):
        guard.checkpoint("somewhere")
    switch.guard().checkpoint("a later run is unaffected")


# --- engage blocks new starts ----------------------------------------------


def make_engine(monkeypatch):
    monkeypatch.setenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", "true")
    return ChaosEngine(
        task_failure=object(),
        pipeline=make_pipeline(),
        scenario_factories={"bad-deployment": FakeScenario},
    )


def test_engaged_switch_refuses_new_scenarios(monkeypatch):
    engine = make_engine(monkeypatch)
    engine.kill_switch.engage("maintenance")

    with pytest.raises(ExperimentNotAllowed, match="Kill switch engaged: maintenance"):
        engine.start_scenario("bad-deployment", background=False)
    with pytest.raises(ExperimentNotAllowed, match="Kill switch engaged"):
        engine.run_task_failure("task-1")
    assert engine.store.list() == []
    assert not engine._running.locked()


def test_release_then_start_works(monkeypatch):
    engine = make_engine(monkeypatch)
    engine.kill_switch.engage("maintenance")
    engine.kill_switch.release()

    incident = engine.start_scenario("bad-deployment", background=False)

    assert incident.state is S.RESOLVED


def test_kill_switch_endpoints(monkeypatch):
    engine = make_engine(monkeypatch)
    original = engine.start_scenario
    engine.start_scenario = lambda name: original(name, background=False)
    app.dependency_overrides[get_engine] = lambda: engine
    try:
        client = TestClient(app)
        assert client.get("/kill-switch").json()["engaged"] is False

        engaged = client.post("/kill-switch/engage", params={"reason": "drill"}).json()
        assert engaged["engaged"] is True and engaged["reason"] == "drill"
        assert client.get("/status").json()["kill_switch_engaged"] is True

        refused = client.post("/scenarios/bad-deployment")
        assert refused.status_code == 403
        assert "Kill switch engaged: drill" in refused.json()["detail"]

        assert client.post("/kill-switch/release").json()["engaged"] is False
        assert client.post("/scenarios/bad-deployment").status_code == 202
    finally:
        app.dependency_overrides.clear()


# --- aborting a run in flight ----------------------------------------------


def engage(pipeline):
    pipeline.kill_switch.engage("operator stop")


def assert_aborted_and_cleaned(incident, scenario):
    assert incident.state is S.ABORTED
    assert incident.state in TERMINAL_STATES
    assert incident.abort_reason.startswith("Aborted by kill switch")
    assert "operator stop" in incident.abort_reason
    assert incident.report()["abort_reason"] == incident.abort_reason
    assert incident.failure_reason is None
    assert scenario.cleaned_up
    assert notes(incident)[-1].startswith("Cleanup completed")


def test_abort_before_inject_skips_cleanup_and_injects_nothing():
    pipeline = make_pipeline()
    scenario = HookedScenario()
    incident = pipeline.start(scenario)
    engage(pipeline)
    pipeline.run(incident, scenario)

    assert incident.state is S.ABORTED
    assert not scenario.injected
    assert not scenario.cleaned_up
    assert notes(incident)[-1] == "Nothing was injected; cleanup skipped"
    assert executed(pipeline) == []


def test_aborted_inject_still_gets_cleanup():
    pipeline = make_pipeline()

    def failing_inject(scenario):
        raise RuntimeError("AWS timed out mid-inject")

    scenario = HookedScenario({"inject": failing_inject})
    incident = run(pipeline, scenario)

    assert incident.state is S.FAILED
    assert scenario.cleaned_up


def test_abort_during_detect_polling():
    pipeline = make_pipeline()

    class PollingScenario(HookedScenario):
        def detect(self):
            threading.Timer(0.05, pipeline.kill_switch.engage, ["operator stop"]).start()
            while True:  # what _poll does with a guard
                self.abort_guard.checkpoint("polling loop")
                self.abort_guard.pause(30, "polling loop")

    scenario = PollingScenario()
    started = time.monotonic()
    incident = run(pipeline, scenario)

    assert time.monotonic() - started < 5  # the 30 s pause was cut short
    assert_aborted_and_cleaned(incident, scenario)
    assert "polling loop" in incident.abort_reason
    assert executed(pipeline) == []
    assert scenario.verify_calls == 0


def test_abort_during_approval_wait_is_immediate_and_late_approve_is_refused():
    pipeline = make_pipeline(approval_timeout=60)
    pipeline.policy = ApprovalPolicy()
    scenario = HookedScenario()
    incident = pipeline.start(scenario)
    worker = threading.Thread(target=pipeline.run, args=(incident, scenario))
    worker.start()
    wait_for(lambda: incident.state is S.AWAITING_APPROVAL)

    started = time.monotonic()
    engage(pipeline)
    worker.join(5)

    assert not worker.is_alive()
    assert time.monotonic() - started < 5
    assert_aborted_and_cleaned(incident, scenario)
    assert "approval wait" in incident.abort_reason
    assert executed(pipeline) == []
    assert S.REMEDIATING not in [event.state for event in incident.timeline]
    assert not pipeline.answer_approval(incident.id, True)


def test_abort_after_approval_but_before_remediation():
    pipeline = make_pipeline(approval_timeout=60)
    pipeline.policy = ApprovalPolicy()
    scenario = HookedScenario()
    incident = pipeline.start(scenario)
    worker = threading.Thread(target=pipeline.run, args=(incident, scenario))
    worker.start()
    wait_for(lambda: incident.state is S.AWAITING_APPROVAL)

    assert pipeline.answer_approval(incident.id, True)
    engage(pipeline)
    worker.join(5)

    assert_aborted_and_cleaned(incident, scenario)
    assert executed(pipeline) == []
    assert incident.remediation_result is None


def test_abort_before_remediation_on_an_automatic_decision():
    pipeline = make_pipeline()
    scenario = HookedScenario({"detect": lambda s: engage(pipeline)})
    incident = run(pipeline, scenario)

    assert_aborted_and_cleaned(incident, scenario)
    assert executed(pipeline) == []
    assert S.REMEDIATING not in [event.state for event in incident.timeline]


def test_abort_between_remediation_and_verification_keeps_the_remediation_record():
    pipeline = make_pipeline()
    executor = next(iter(pipeline.remediation.executors.values()))
    original = executor.execute

    def execute_then_engage(context):
        result = original(context)
        engage(pipeline)  # noticed at the next checkpoint, the call itself is not interrupted
        return result

    executor.execute = execute_then_engage
    scenario = HookedScenario()
    incident = run(pipeline, scenario)

    assert_aborted_and_cleaned(incident, scenario)
    assert executed(pipeline) == [("rollback_to_previous_revision", CONTEXT)]
    assert incident.remediation_result.succeeded
    assert scenario.verify_calls == 0
    assert incident.verification is None
    assert "remediation already executed" in incident.abort_reason
    assert "recovery NOT verified" in incident.abort_reason


def test_abort_during_verify_polling():
    pipeline = make_pipeline()

    def polling_verify(scenario):
        threading.Timer(0.05, pipeline.kill_switch.engage, ["operator stop"]).start()
        while True:
            scenario.abort_guard.checkpoint("polling loop")
            scenario.abort_guard.pause(30, "polling loop")

    scenario = HookedScenario({"verify": polling_verify})
    started = time.monotonic()
    incident = run(pipeline, scenario)

    assert time.monotonic() - started < 5
    assert_aborted_and_cleaned(incident, scenario)
    assert incident.state is not S.RESOLVED
    assert executed(pipeline) == [("rollback_to_previous_revision", CONTEXT)]
    assert incident.verification is None
    assert "recovery NOT verified" in incident.abort_reason


def test_engage_released_before_the_next_checkpoint_still_aborts():
    pipeline = make_pipeline()

    def blip(scenario):
        pipeline.kill_switch.engage("operator stop")
        pipeline.kill_switch.release()

    scenario = HookedScenario({"detect": blip})
    incident = run(pipeline, scenario)

    assert not pipeline.kill_switch.engaged
    assert_aborted_and_cleaned(incident, scenario)
    assert executed(pipeline) == []


def test_engage_released_before_the_run_started_still_aborts_it():
    pipeline = make_pipeline()
    scenario = HookedScenario()
    incident = pipeline.start(scenario)
    pipeline.kill_switch.engage("operator stop")
    pipeline.kill_switch.release()
    pipeline.run(incident, scenario)

    assert incident.state is S.ABORTED


def test_cleanup_failure_during_abort_is_recorded_and_state_stays_aborted():
    pipeline = make_pipeline()

    def broken_cleanup():
        raise RuntimeError("AWS throttled")

    scenario = HookedScenario({"detect": lambda s: engage(pipeline)})
    scenario.cleanup = broken_cleanup
    incident = run(pipeline, scenario)

    assert incident.state is S.ABORTED
    assert notes(incident)[-1] == "Cleanup failed: AWS throttled"


def test_release_does_not_resume_an_aborted_incident():
    pipeline = make_pipeline()
    scenario = HookedScenario({"detect": lambda s: engage(pipeline)})
    incident = run(pipeline, scenario)
    timeline_length = len(incident.timeline)

    pipeline.kill_switch.release()
    time.sleep(0.05)

    assert incident.state is S.ABORTED
    assert len(incident.timeline) == timeline_length
    assert executed(pipeline) == []
    # and a fresh run on the same pipeline works
    assert run(pipeline, HookedScenario()).state is S.RESOLVED


def test_unclear_incident_still_escalates_when_not_engaged():
    assert run(make_pipeline(), HookedScenario(signals=UNCLEAR_SIGNALS)).state is S.ESCALATED


# --- incident model ----------------------------------------------------------


@pytest.mark.parametrize("state", [s for s in S if s not in TERMINAL_STATES])
def test_abort_is_allowed_from_every_non_terminal_state(state):
    incident = Incident(scenario="bad_deployment", state=state)
    incident.abort("operator stop")

    assert incident.state is S.ABORTED
    assert incident.abort_reason == "operator stop"
    assert incident.timeline[-1].state is S.ABORTED


@pytest.mark.parametrize("target", list(S))
def test_nothing_leaves_aborted(target):
    incident = Incident(scenario="bad_deployment")
    incident.abort("operator stop")

    with pytest.raises(InvalidTransition):
        incident.transition(target)
    with pytest.raises(InvalidTransition):
        incident.fail("late")
    with pytest.raises(InvalidTransition):
        incident.abort("again")


def test_aborted_is_not_reachable_through_transition():
    incident = Incident(scenario="bad_deployment")
    with pytest.raises(InvalidTransition):
        incident.transition(S.ABORTED)


# --- real scenarios: _poll honours the guard and Aborted is not swallowed -----


@pytest.fixture
def bad_deployment():
    ecs = boto3.client("ecs", region_name="ap-south-1")
    elbv2 = boto3.client("elbv2", region_name="ap-south-1")
    with Stubber(ecs) as ecs_stub, Stubber(elbv2) as elb_stub:
        scenario = BadDeploymentScenario(
            CLUSTER, SERVICE, TG, ecs_client=ecs, elbv2_client=elbv2,
            detect_timeout=600, poll_interval=30,
        )
        scenario.known_good_task_definition = GOOD
        yield scenario, ecs_stub, elb_stub
        ecs_stub.assert_no_pending_responses()
        elb_stub.assert_no_pending_responses()


def test_bad_deployment_detect_aborts_immediately_without_further_aws_calls(bad_deployment):
    scenario, ecs_stub, elb_stub = bad_deployment
    switch = KillSwitch()
    scenario.abort_guard = switch.guard()
    # One healthy poll is queued; a second poll would fail the Stubber (no response queued).
    ecs_stub.add_response(
        "describe_services", service(GOOD, rollout="COMPLETED"),
        {"cluster": CLUSTER, "services": [SERVICE]},
    )
    elb_stub.add_response(
        "describe_target_health", targets("healthy", "healthy"), {"TargetGroupArn": TG}
    )
    threading.Timer(0.05, switch.engage, ["operator stop"]).start()

    started = time.monotonic()
    with pytest.raises(Aborted) as aborted:
        scenario.detect()  # the 30 s interval is cut short by event.wait

    assert time.monotonic() - started < 5
    assert aborted.value.where == "polling loop"


def test_polling_checks_the_guard_before_any_aws_call(bad_deployment):
    scenario, _, _ = bad_deployment  # no responses queued: any AWS call fails
    switch = KillSwitch()
    scenario.abort_guard = switch.guard()
    switch.engage("operator stop")

    with pytest.raises(Aborted):
        scenario.detect()
    with pytest.raises(Aborted):
        scenario.verify()


def test_config_change_poll_aborts_and_is_not_swallowed():
    switch = KillSwitch()
    scenario = ConfigChangeScenario(
        "c", "s", TG, "sg-ecs", "sg-alb",
        ec2_client=object(), ecs_client=object(), elbv2_client=object(),
        poll_interval=30,
    )
    scenario.abort_guard = switch.guard()
    calls = []

    def check():
        calls.append(1)
        return False

    threading.Timer(0.05, switch.engage, ["operator stop"]).start()
    with pytest.raises(Aborted):
        scenario._poll(check, timeout=600)
    assert calls == [1]


def test_scenarios_without_a_guard_still_use_the_injected_sleep():
    slept = []
    scenario = ConfigChangeScenario(
        "c", "s", TG, "sg-ecs", "sg-alb",
        ec2_client=object(), ecs_client=object(), elbv2_client=object(),
        poll_interval=15, sleep=slept.append, clock=iter([0, 0, 100]).__next__,
    )

    assert scenario._poll(lambda: False, timeout=10) is False
    assert slept == [15]


def test_every_registered_scenario_is_abortable(monkeypatch):
    # Pipeline.run only hands the guard to scenarios that have this attribute.
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    monkeypatch.setenv("CHAOS_PILOT_TARGET_GROUP_ARN", TG)
    monkeypatch.setenv("CHAOS_PILOT_ECS_SECURITY_GROUP_ID", "sg-ecs")
    monkeypatch.setenv("CHAOS_PILOT_ALB_SECURITY_GROUP_ID", "sg-alb")
    engine = ChaosEngine(task_failure=object())

    assert engine.scenario_factories
    for name, factory in engine.scenario_factories.items():
        assert hasattr(factory(), "abort_guard"), f"{name} cannot be aborted mid-poll"
