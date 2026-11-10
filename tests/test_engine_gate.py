import pytest

from chaos_pilot.core.engine import ChaosEngine
from chaos_pilot.experiments.task_failure import ExperimentNotAllowed
from tests.test_pipeline import FakeScenario, make_pipeline


def make_engine(**kwargs):
    return ChaosEngine(
        task_failure=object(),
        pipeline=make_pipeline(),
        scenario_factories={"bad-deployment": FakeScenario},
        **kwargs,
    )


def test_default_engine_still_uses_the_env_gate(monkeypatch):
    monkeypatch.delenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", raising=False)
    engine = make_engine()
    assert engine.get_status()["experiments_enabled"] is False
    with pytest.raises(ExperimentNotAllowed, match="Experiments are disabled"):
        engine.start_scenario("bad-deployment", background=False)
    with pytest.raises(ExperimentNotAllowed, match="Experiments are disabled"):
        engine.run_task_failure("t-1")


def test_default_engine_reads_the_env_gate_at_call_time(monkeypatch):
    monkeypatch.delenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", raising=False)
    engine = make_engine()
    monkeypatch.setenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", "true")
    assert engine.get_status()["experiments_enabled"] is True
    assert engine.start_scenario("bad-deployment", background=False).state.value == "RESOLVED"
    monkeypatch.setenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", "false")
    assert engine.get_status()["experiments_enabled"] is False


def test_default_pipeline_built_by_the_engine_uses_the_env_gate(monkeypatch):
    monkeypatch.delenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", raising=False)
    engine = ChaosEngine(task_failure=object())
    assert engine.pipeline.experiments_enabled() is False
    monkeypatch.setenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", "true")
    assert engine.pipeline.experiments_enabled() is True


def test_an_explicit_gate_overrides_the_env(monkeypatch):
    monkeypatch.delenv("CHAOS_PILOT_EXPERIMENTS_ENABLED", raising=False)
    engine = make_engine(gate=lambda: True)
    assert engine.get_status()["experiments_enabled"] is True
    assert engine.start_scenario("bad-deployment", background=False).state.value == "RESOLVED"
