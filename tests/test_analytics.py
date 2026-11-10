from datetime import datetime, timedelta, timezone

import pytest

from app import analytics, incident_view
from chaos_pilot.incidents.models import (
    Decision,
    Diagnosis,
    Incident,
    IncidentState as S,
    PolicyDecision,
    TimelineEvent,
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
ROLLBACK = "rollback_to_previous_revision"
RESTORE = "restore_security_group_rule"
ESCALATE = "escalate_to_human"


def build_timeline(steps):
    """steps: [(state, seconds_since_previous)] -> TimelineEvents."""
    now, events = 0.0, []
    for state, gap in steps:
        now += gap
        events.append(TimelineEvent(state, BASE + timedelta(seconds=now), ""))
    return events


def make(scenario="bad_deployment", *, steps, state, action=ROLLBACK, decision=Decision.AUTO,
         confidence=0.9, human=False, investigator="rule_based", diagnosed=True):
    incident = Incident(scenario=scenario, state=state, timeline=build_timeline(steps))
    incident.human_intervention = human
    if diagnosed:
        incident.diagnosis = Diagnosis("cause", confidence, "why", action, investigator)
    if decision is not None:
        incident.policy_decision = PolicyDecision(action, decision, ("r",))
    return incident


def resolved(scenario="bad_deployment", *, notice=2, investigate=1, decide=1, wait=None, fix=3,
             verify=2, action=ROLLBACK, decision=Decision.AUTO, confidence=0.9):
    steps = [(S.PENDING, 0), (S.INJECTED, 1), (S.DETECTED, notice), (S.INVESTIGATING, 0),
             (S.POLICY_DECIDED, investigate)]
    if wait is not None:
        steps += [(S.AWAITING_APPROVAL, decide), (S.REMEDIATING, wait)]
    else:
        steps += [(S.REMEDIATING, decide)]
    steps += [(S.VERIFYING, fix), (S.RESOLVED, verify)]
    return make(scenario, steps=steps, state=S.RESOLVED, action=action, decision=decision,
                confidence=confidence, human=wait is not None)


def failed_at_verify(**kw):
    steps = [(S.PENDING, 0), (S.INJECTED, 1), (S.DETECTED, 2), (S.INVESTIGATING, 0),
             (S.POLICY_DECIDED, 1), (S.REMEDIATING, 1), (S.VERIFYING, 3), (S.FAILED, 2)]
    return make(steps=steps, state=S.FAILED, **kw)


def denied(**kw):
    steps = [(S.PENDING, 0), (S.INJECTED, 1), (S.DETECTED, 2), (S.INVESTIGATING, 0),
             (S.POLICY_DECIDED, 1), (S.REJECTED, 0)]
    kw.setdefault("action", RESTORE)
    return make(steps=steps, state=S.REJECTED, decision=Decision.DENY, **kw)


def human_rejected():
    steps = [(S.PENDING, 0), (S.INJECTED, 1), (S.DETECTED, 2), (S.INVESTIGATING, 0),
             (S.POLICY_DECIDED, 1), (S.AWAITING_APPROVAL, 1), (S.REJECTED, 5)]
    return make("config_change", steps=steps, state=S.REJECTED, action=RESTORE,
                decision=Decision.HUMAN_APPROVAL, human=True)


def escalated():
    steps = [(S.PENDING, 0), (S.INJECTED, 1), (S.DETECTED, 2), (S.INVESTIGATING, 0),
             (S.POLICY_DECIDED, 1), (S.ESCALATED, 0)]
    return make(steps=steps, state=S.ESCALATED, action=ESCALATE, decision=Decision.ESCALATE,
                confidence=0.3, human=True)


def running(state=S.INVESTIGATING, decision=None, **kw):
    steps = [(S.PENDING, 0), (S.INJECTED, 1), (S.DETECTED, 2), (S.INVESTIGATING, 0)]
    if state is S.AWAITING_APPROVAL:
        steps += [(S.POLICY_DECIDED, 1), (S.AWAITING_APPROVAL, 1)]
        decision = Decision.HUMAN_APPROVAL
    return make(steps=steps, state=state, decision=decision, **kw)


def compute(*incidents, demo=False):
    return analytics.compute([incident_view.build(i, 900) for i in incidents], demo=demo)


def test_zero_incidents_means_no_division_errors():
    data = compute()
    totals = data["totals"]
    assert totals["incidents"] == 0 and totals["resolved_pct"] is None
    assert data["mttr"] == {"avg_seconds": None, "trend": "insufficient_data", "runs": []}
    assert data["ai"]["accuracy"] is None and data["ai"]["overridden"]["rate"] is None
    assert all(v["avg_recovery"] is None for v in data["by_policy"].values())
    assert all(v is None for v in data["stages"]["avg_seconds"].values())
    assert data["trends"]["mttr"]["direction"] is None
    assert data["scatter"] == [] and data["outcomes"]["RESOLVED"] == 0
    for take in data["takeaways"].values():
        assert take["enough"] is False and take["text"].startswith("Not enough data yet")


def test_totals_resolved_percent_and_mttr():
    data = compute(resolved(), resolved(fix=5), resolved(fix=7), failed_at_verify())
    assert data["totals"]["resolved"] == 3 and data["totals"]["failed"] == 1
    assert data["totals"]["resolved_pct"] == 0.75
    # recovery = DETECTED -> RESOLVED = investigate + decide + fix + verify
    assert [r["recovery_seconds"] for r in data["mttr"]["runs"][:3]] == [7, 9, 11]
    assert data["mttr"]["avg_seconds"] == 9
    assert data["mttr"]["runs"][3]["recovery_seconds"] is None
    assert [r["n"] for r in data["mttr"]["runs"]] == [1, 2, 3, 4]


def test_mttr_trend_faster_slower_flat_and_insufficient():
    assert compute(*[resolved(fix=f) for f in (9, 8, 3, 2)])["mttr"]["trend"] == "faster"
    assert compute(*[resolved(fix=f) for f in (2, 3, 8, 9)])["mttr"]["trend"] == "slower"
    assert compute(*[resolved(fix=f) for f in (5, 5, 5, 5)])["mttr"]["trend"] == "flat"
    assert compute(resolved(), resolved(), resolved())["mttr"]["trend"] == "insufficient_data"


def test_still_running_is_excluded_and_counted():
    data = compute(resolved(), running(), running(S.AWAITING_APPROVAL))
    totals = data["totals"]
    assert totals["incidents"] == 3 and totals["running"] == 2 and totals["awaiting_approval"] == 1
    assert sum(data["outcomes"].values()) == 1 and totals["resolved_pct"] == 1.0
    assert data["ai"]["diagnosed"] == 1 and len(data["scatter"]) == 1
    assert data["mttr"]["avg_seconds"] == 7
    assert data["mttr"]["runs"][1]["recovery_seconds"] is None


def test_all_failed_has_no_mttr_but_still_has_accuracy():
    data = compute(failed_at_verify(), failed_at_verify())
    assert data["totals"]["resolved_pct"] == 0.0
    assert data["mttr"]["avg_seconds"] is None
    assert data["ai"]["accuracy"] == 1.0
    assert all(v is None for v in data["stages"]["avg_seconds"].values())
    assert data["takeaways"]["mttr"]["enough"] is False
    assert data["takeaways"]["outcomes"]["enough"] is True


def test_stage_durations_come_from_the_timeline_and_use_resolved_runs_only():
    data = compute(
        resolved(notice=2, investigate=1, decide=1, fix=3, verify=2),
        resolved(notice=4, investigate=3, decide=1, wait=30, fix=5, verify=4, decision=Decision.HUMAN_APPROVAL),
        failed_at_verify(),
    )
    avg, samples = data["stages"]["avg_seconds"], data["stages"]["samples"]
    assert avg["detect"] == 3 and avg["investigate"] == 2 and avg["decide"] == 1
    assert avg["fix"] == 4 and avg["verify"] == 3
    assert avg["approval_wait"] == 30 and samples["approval_wait"] == 1 and samples["fix"] == 2
    assert set(data["stages"]["by_scenario"]) == {"bad_deployment"}


def test_missing_timeline_stages_are_none_not_zero():
    steps = [(S.PENDING, 0), (S.INJECTED, 1), (S.FAILED, 1)]
    incident = make(steps=steps, state=S.FAILED, diagnosed=False, decision=None)
    data = compute(incident)
    assert data["totals"]["failed"] == 1
    assert data["ai"]["no_diagnosis"] == 1 and data["ai"]["diagnosed"] == 0
    assert data["ai"]["accuracy"] is None and data["scatter"] == []


def test_denied_by_policy_is_not_the_same_as_rejected_by_a_human():
    data = compute(denied(), human_rejected())
    assert data["outcomes"]["DENIED"] == 1 and data["outcomes"]["REJECTED"] == 1
    assert data["totals"]["denied"] == 1 and data["totals"]["rejected"] == 1


def test_ai_accuracy_confusion_and_the_escalated_bucket():
    data = compute(
        resolved(),                                              # correct
        resolved("config_change", action=RESTORE, decision=Decision.HUMAN_APPROVAL, wait=5),  # correct
        denied(),                                                # wrong: restore under bad_deployment
        escalated(),                                             # own bucket, not correct
    )
    ai = data["ai"]
    assert (ai["correct"], ai["wrong"], ai["escalated"], ai["diagnosed"]) == (2, 1, 1, 4)
    assert ai["accuracy"] == 0.5  # escalation stays in the denominator
    assert ai["per_scenario"]["bad_deployment"] == {
        "n": 3, "correct": 1, "wrong": 1, "escalated": 1, "accuracy": pytest.approx(1 / 3)}
    assert ai["per_scenario"]["config_change"]["accuracy"] == 1.0
    assert ai["confusion"]["bad_deployment"] == {ROLLBACK: 1, RESTORE: 1, ESCALATE: 1}
    assert "not counted as correct" in data["takeaways"]["ai"]["text"]


def test_an_allow_listed_but_misfit_action_is_wrong_not_correct():
    data = compute(make("config_change", steps=[(S.PENDING, 0), (S.INJECTED, 1), (S.DETECTED, 1),
                                                  (S.INVESTIGATING, 0), (S.POLICY_DECIDED, 1), (S.REJECTED, 0)],
                        state=S.REJECTED, action=ROLLBACK, decision=Decision.DENY))
    assert data["ai"]["wrong"] == 1 and data["ai"]["correct"] == 0


def test_policy_override_rate_counts_denials_and_separates_required_approval():
    data = compute(resolved(), denied(), human_rejected(), escalated())
    over = data["ai"]["overridden"]
    assert over == {"count": 1, "required_approval": 1, "of": 4, "rate": 0.25}
    assert "1 of 4" in data["takeaways"]["override"]["text"]


def test_by_policy_averages_show_the_cost_of_waiting():
    data = compute(
        resolved(),  # AUTO, recovery 7
        resolved(decision=Decision.HUMAN_APPROVAL, wait=60, action=RESTORE, scenario="config_change"),
        human_rejected(),
    )
    auto, human = data["by_policy"]["AUTO"], data["by_policy"]["HUMAN_APPROVAL"]
    assert auto == {"n": 1, "resolved": 1, "avg_recovery": 7}
    assert human["n"] == 2 and human["resolved"] == 1 and human["avg_recovery"] == 67
    assert "1m 0s longer" in data["takeaways"]["by_policy"]["text"]


def test_by_policy_needs_both_sides_to_compare():
    take = compute(resolved())["takeaways"]["by_policy"]
    assert take["enough"] is False and take["text"].startswith("Not enough data yet")


def test_scatter_lists_finished_runs_with_a_confidence():
    data = compute(resolved(confidence=0.9), failed_at_verify(confidence=0.4), running())
    assert data["scatter"] == [
        {"incident_id": data["mttr"]["runs"][0]["incident_id"], "confidence": 0.9, "outcome": "RESOLVED"},
        {"incident_id": data["mttr"]["runs"][1]["incident_id"], "confidence": 0.4, "outcome": "FAILED"},
    ]


def test_investigator_names_are_counted_for_the_honesty_note():
    data = compute(resolved(), failed_at_verify())
    assert data["ai"]["investigators"] == {"rule_based": 2}


def test_kpi_trends_need_four_finished_runs_then_compare_halves():
    assert compute(resolved(), failed_at_verify())["trends"]["resolved_pct"]["direction"] is None
    improving = compute(failed_at_verify(), failed_at_verify(), resolved(), resolved())
    assert improving["trends"]["resolved_pct"]["direction"] == "up"
    worsening = compute(resolved(), resolved(), failed_at_verify(), failed_at_verify())
    assert worsening["trends"]["resolved_pct"]["direction"] == "down"
    faster = compute(*[resolved(fix=f) for f in (9, 8, 3, 2)])
    assert faster["trends"]["mttr"]["direction"] == "down"


def test_takeaways_are_plain_sentences_with_numbers_when_there_is_data():
    data = compute(*[resolved(fix=f) for f in (9, 8, 3, 2)], failed_at_verify())
    take = data["takeaways"]
    assert take["mttr"]["enough"] and take["mttr"]["text"].startswith("Yes")
    assert take["outcomes"]["enough"] and "80%" in take["outcomes"]["text"]
    assert take["stages"]["enough"] and "fix" in take["stages"]["text"]


def test_demo_flag_is_passed_through():
    assert compute(demo=True)["demo"] is True and compute()["demo"] is False
