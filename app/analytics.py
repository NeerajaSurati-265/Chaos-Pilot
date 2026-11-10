"""Analytics over the incident store: pure functions on incident views.

A "view" is the dict from app.incident_view.build(). Nothing here reads AWS or mutates an
incident. Definitions:

- finished: a terminal state. Running and awaiting-approval incidents are only counted in
  totals.running and left out of everything else.
- MTTR and stage averages use RESOLVED runs only (only those have a recovery time).
- AI ground truth is SCENARIO_ACTIONS[scenario]. correct = the recommended action is the one
  permitted for that scenario; escalate_to_human is its own bucket and never counts as correct
  (it stays in the accuracy denominator); anything else is wrong; no diagnosis is excluded.
"""
from chaos_pilot.policy.engine import ESCALATE_ACTION, SCENARIO_ACTIONS

FINISHED = ("RESOLVED", "FAILED", "DENIED", "REJECTED", "ESCALATED", "ABORTED")
RUNNING = ("IN_PROGRESS", "AWAITING_APPROVAL")
STAGES = ("detect", "investigate", "decide", "approval_wait", "fix", "verify")
# Our name for each stage -> the key in incident_view.stage_seconds (detect = INJECTED -> DETECTED).
STAGE_KEY = {"detect": "notice", "investigate": "investigate", "decide": "decide",
             "approval_wait": "approval_wait", "fix": "fix", "verify": "verify"}
MIN_TREND_RUNS = 4
TREND_TOLERANCE = 0.10
POLICIES = ("AUTO", "HUMAN_APPROVAL")


def _avg(values):
    values = list(values)
    return sum(values) / len(values) if values else None


def _ratio(part, whole):
    return part / whole if whole else None


def _direction(before, after):
    """'up' / 'down' / 'flat' / None (not enough data) with the change, later vs earlier."""
    if before is None or after is None:
        return {"direction": None, "delta": None}
    delta = after - before
    if before and abs(delta) / abs(before) <= TREND_TOLERANCE:
        return {"direction": "flat", "delta": delta}
    if not before and delta == 0:
        return {"direction": "flat", "delta": 0.0}
    return {"direction": "up" if delta > 0 else "down", "delta": delta}


def _halves(items):
    half = len(items) // 2
    return items[:half], items[half:]


def _resolved_pct(views):
    return _ratio(sum(v["outcome"] == "RESOLVED" for v in views), len(views))


def _mttr(views):
    return _avg(v["recovery_seconds"] for v in views
                if v["outcome"] == "RESOLVED" and v["recovery_seconds"] is not None)


def _human_rate(views):
    return _ratio(sum(bool(v["human_intervention"]) for v in views), len(views))


def trends(finished):
    if len(finished) < MIN_TREND_RUNS:
        empty = {"direction": None, "delta": None}
        return {"resolved_pct": empty, "mttr": dict(empty), "human_interventions": dict(empty)}
    early, late = _halves(finished)
    return {
        "resolved_pct": _direction(_resolved_pct(early), _resolved_pct(late)),
        "mttr": _direction(_mttr(early), _mttr(late)),
        "human_interventions": _direction(_human_rate(early), _human_rate(late)),
    }


def mttr_trend(resolved_seconds):
    """faster / slower / flat / insufficient_data, from recovery times in run order."""
    if len(resolved_seconds) < MIN_TREND_RUNS:
        return "insufficient_data"
    early, late = _halves(resolved_seconds)
    change = _direction(_avg(early), _avg(late))["direction"]
    return {"down": "faster", "up": "slower", "flat": "flat"}.get(change, "insufficient_data")


def _stage_stats(views):
    sums = {s: 0.0 for s in STAGES}
    samples = {s: 0 for s in STAGES}
    for v in views:
        for stage in STAGES:
            seconds = v["stage_seconds"].get(STAGE_KEY[stage])
            if seconds is not None:
                sums[stage] += seconds
                samples[stage] += 1
    return {
        "avg_seconds": {s: (sums[s] / samples[s] if samples[s] else None) for s in STAGES},
        "samples": samples,
    }


def _classify(view):
    """correct / wrong / escalated / None (no diagnosis)."""
    action = view["recommended_action"]
    if action is None:
        return None
    if action == ESCALATE_ACTION:
        return "escalated"
    return "correct" if action in SCENARIO_ACTIONS.get(view["scenario"], frozenset()) else "wrong"


def _ai(finished):
    per = {}
    confusion = {}
    investigators = {}
    totals = {"correct": 0, "wrong": 0, "escalated": 0}
    no_diagnosis = denied = required = 0
    for v in finished:
        bucket = _classify(v)
        if bucket is None:
            no_diagnosis += 1
            continue
        totals[bucket] += 1
        row = per.setdefault(v["scenario"], {"n": 0, "correct": 0, "wrong": 0, "escalated": 0})
        row["n"] += 1
        row[bucket] += 1
        by_action = confusion.setdefault(v["scenario"], {})
        by_action[v["recommended_action"]] = by_action.get(v["recommended_action"], 0) + 1
        name = v["investigator"] or "unknown"
        investigators[name] = investigators.get(name, 0) + 1
        if v["policy_denied_ai_action"]:
            denied += 1
        elif v["policy_decision"] == "HUMAN_APPROVAL":
            required += 1
    diagnosed = sum(totals.values())
    for row in per.values():
        row["accuracy"] = _ratio(row["correct"], row["n"])
    return {
        "diagnosed": diagnosed, **totals, "no_diagnosis": no_diagnosis,
        "accuracy": _ratio(totals["correct"], diagnosed),
        "per_scenario": per, "confusion": confusion,
        "overridden": {"count": denied, "required_approval": required, "of": diagnosed,
                       "rate": _ratio(denied, diagnosed)},
        "investigators": investigators,
    }


def _scatter(finished):
    return [{"incident_id": v["incident_id"], "confidence": v["confidence"], "outcome": v["outcome"]}
            for v in finished if v["confidence"] is not None]


def _by_policy(finished):
    result = {}
    for name in POLICIES:
        rows = [v for v in finished if v["policy_decision"] == name]
        resolved = [v for v in rows if v["outcome"] == "RESOLVED"]
        result[name] = {"n": len(rows), "resolved": len(resolved), "avg_recovery": _mttr(rows)}
    return result


def compute(views, demo=False):
    """Everything the dashboard and analytics page show, from a list of incident views."""
    finished = [v for v in views if v["outcome"] in FINISHED]
    running = [v for v in views if v["outcome"] in RUNNING]
    resolved = [v for v in finished if v["outcome"] == "RESOLVED"]
    counts = {o: sum(v["outcome"] == o for v in finished) for o in FINISHED}

    runs = [{"n": n, "incident_id": v["incident_id"], "scenario": v["scenario"],
             "outcome": v["outcome"], "recovery_seconds": v["recovery_seconds"]}
            for n, v in enumerate(views, start=1)]
    resolved_seconds = [v["recovery_seconds"] for v in resolved if v["recovery_seconds"] is not None]
    scenarios = sorted({v["scenario"] for v in resolved})

    data = {
        "demo": demo,
        "totals": {
            "incidents": len(views),
            "running": len(running),
            "awaiting_approval": sum(v["outcome"] == "AWAITING_APPROVAL" for v in views),
            "resolved": counts["RESOLVED"], "failed": counts["FAILED"], "denied": counts["DENIED"],
            "rejected": counts["REJECTED"], "escalated": counts["ESCALATED"],
            "aborted": counts["ABORTED"],
            "resolved_pct": _resolved_pct(finished),
            "human_interventions": sum(bool(v["human_intervention"]) for v in views),
        },
        "mttr": {"avg_seconds": _avg(resolved_seconds), "trend": mttr_trend(resolved_seconds),
                 "runs": runs},
        "outcomes": counts,
        "stages": {**_stage_stats(resolved),
                   "by_scenario": {s: _stage_stats([v for v in resolved if v["scenario"] == s])
                                   for s in scenarios}},
        "by_policy": _by_policy(finished),
        "ai": _ai(finished),
        "scatter": _scatter(finished),
        "trends": trends(finished),
    }
    data["takeaways"] = takeaways(data)
    return data


# ---- plain-English takeaways -------------------------------------------------

def _t(enough, text):
    return {"enough": enough, "text": text}


def _secs(value):
    if value is None:
        return "n/a"
    if value < 60:
        return f"{value:.1f} s"
    return f"{int(value // 60)}m {round(value % 60)}s"


def _pct(value):
    return f"{round(value * 100)}%"


def takeaways(data):
    totals, finished_n = data["totals"], sum(data["outcomes"].values())
    resolved = totals["resolved"]
    out = {}

    trend = data["mttr"]["trend"]
    if trend == "insufficient_data":
        out["mttr"] = _t(False, f"Not enough data yet: {resolved} recovered run(s); "
                                f"run at least {MIN_TREND_RUNS} scenarios that recover to see a trend.")
    else:
        text = {"faster": "Yes: later recoveries were quicker than earlier ones",
                "slower": "No: later recoveries were slower than earlier ones",
                "flat": "Not really: recovery time is about the same as before"}[trend]
        out["mttr"] = _t(True, f"{text} (average {_secs(data['mttr']['avg_seconds'])} across {resolved} runs).")

    if not finished_n:
        out["outcomes"] = _t(False, "Not enough data yet: run a scenario to see how incidents end.")
    else:
        parts = [f"{n} {o.lower()}" for o, n in data["outcomes"].items() if n]
        out["outcomes"] = _t(True, f"{_pct(totals['resolved_pct'])} of {finished_n} finished incidents "
                                   f"were verified as recovered ({', '.join(parts)}).")

    avg = data["stages"]["avg_seconds"]
    timed = {s: v for s, v in avg.items() if v is not None}
    if not timed:
        out["stages"] = _t(False, "Not enough data yet: stage timings appear after a run recovers.")
    else:
        slowest = max(timed, key=timed.get)
        total = sum(timed.values())
        share = _pct(timed[slowest] / total) if total else "0%"
        out["stages"] = _t(True, f"Most recovery time goes to {slowest.replace('_', ' ')} "
                                 f"({_secs(timed[slowest])}, {share} of the timed steps) in recovered runs.")

    auto, human = data["by_policy"]["AUTO"], data["by_policy"]["HUMAN_APPROVAL"]
    if auto["avg_recovery"] is None or human["avg_recovery"] is None:
        out["by_policy"] = _t(False, "Not enough data yet: needs a recovered AUTO run and a recovered "
                                     "human-approved run to compare.")
    else:
        gap = human["avg_recovery"] - auto["avg_recovery"]
        word = "longer" if gap > 0 else "shorter"
        out["by_policy"] = _t(True, f"Runs that waited for a human took {_secs(abs(gap))} {word} on "
                                    f"average ({_secs(human['avg_recovery'])} vs {_secs(auto['avg_recovery'])}).")

    ai = data["ai"]
    if not ai["diagnosed"]:
        out["ai"] = _t(False, "Not enough data yet: the AI's accuracy appears once incidents have been diagnosed.")
    else:
        out["ai"] = _t(True, f"The AI recommended the right action in {ai['correct']} of {ai['diagnosed']} "
                             f"incidents ({_pct(ai['accuracy'])}); {ai['escalated']} escalation(s) are not "
                             f"counted as correct.")

    over = ai["overridden"]
    if not over["of"]:
        out["override"] = _t(False, "Not enough data yet: needs diagnosed incidents to see how often policy steps in.")
    else:
        out["override"] = _t(True, f"Policy refused the AI's action in {over['count']} of {over['of']} incidents "
                                   f"and required a human in {over['required_approval']} more.")

    scatter = data["scatter"]
    if len(scatter) < MIN_TREND_RUNS:
        out["scatter"] = _t(False, f"Not enough data yet: {len(scatter)} finished incident(s) with a confidence; "
                                   f"run at least {MIN_TREND_RUNS}.")
    else:
        hits = [p["confidence"] for p in scatter if p["outcome"] == "RESOLVED"]
        misses = [p["confidence"] for p in scatter if p["outcome"] != "RESOLVED"]
        if hits and misses:
            out["scatter"] = _t(True, f"Recovered runs averaged {_pct(_avg(hits))} AI confidence, "
                                      f"the others {_pct(_avg(misses))}.")
        else:
            out["scatter"] = _t(True, "Every plotted run ended the same way, so confidence cannot be compared "
                                      "with outcome yet.")
    return out
