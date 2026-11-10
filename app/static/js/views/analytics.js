import { el, empty } from "../dom.js";
import { state } from "../state.js";
import { fmtDuration, humanize, pct, scenarioLabel } from "../format.js";
import { chartsAvailable, unavailableNote, trendChart, donutChart, stackedStageChart, barChart, scatterChart, destroyChart } from "../charts.js";
import { topbar } from "./common.js";

const STAGES = ["detect", "investigate", "decide", "approval_wait", "fix", "verify"];
const STAGE_LABEL = (s) => ({ detect: "Detect", investigate: "Investigate (AI)", decide: "Decide (rules)",
  approval_wait: "Approval wait", fix: "Fix", verify: "Verify" }[s]);

// A panel: title, question, chart box (or an honest empty state), and the server's one-sentence takeaway.
function panel(title, question, takeaway, draw) {
  const body = el("div", { cls: "chart-box" });
  const card = el("section", { cls: "card" }, [el("h2", { text: title }), question ? el("p", { cls: "muted small", text: question }) : null]);
  if (!takeaway.enough) {
    body.append(el("div", { cls: "chart-note" }, [el("span", { text: takeaway.text })]));
  } else if (!chartsAvailable()) {
    body.append(unavailableNote());
  } else {
    const canvas = el("canvas", { attrs: { role: "img", "aria-label": title } });
    body.append(canvas);
    queueMicrotask(() => draw(canvas));
  }
  card.append(body, el("p", { cls: "takeaway", text: takeaway.enough ? takeaway.text : "" }));
  return card;
}

function confusionTable(ai) {
  const actions = [...new Set(Object.values(ai.confusion).flatMap((row) => Object.keys(row)))];
  const head = el("tr", {}, [el("th", { text: "Actual scenario ↓ / AI recommended →" }), ...actions.map((a) => el("th", { text: humanize(a) }))]);
  const rows = Object.entries(ai.confusion).map(([scenario, row]) => el("tr", {}, [
    el("td", { text: scenarioLabel(scenario) }), ...actions.map((a) => el("td", { text: row[a] || 0 }))]));
  return el("table", {}, [el("thead", {}, [head]), el("tbody", {}, rows)]);
}

function aiPanel(a) {
  const ai = a.ai, take = a.takeaways.ai;
  const card = el("section", { cls: "card" }, [
    el("h2", { text: "Did the AI identify the right problem?" }),
    el("p", { cls: "muted small", text: "Ground truth is the scenario we injected: the AI is right when it recommends the one action permitted for that scenario." }),
  ]);
  const box = el("div", { cls: "chart-box" });
  if (!take.enough) {
    box.append(el("div", { cls: "chart-note" }, [el("span", { text: take.text })]));
    card.append(box);
    return card;
  }
  const scenarios = Object.keys(ai.per_scenario);
  if (chartsAvailable()) {
    const canvas = el("canvas", { attrs: { role: "img", "aria-label": "AI accuracy per scenario" } });
    box.append(canvas);
    queueMicrotask(() => barChart(canvas, "ai", scenarios.map(scenarioLabel),
      scenarios.map((s) => (ai.per_scenario[s].accuracy === null ? 0 : Math.round(ai.per_scenario[s].accuracy * 100))),
      scenarios.map(() => "#0f7b72"), "Accuracy (%)", true));
  } else box.append(unavailableNote());
  card.append(box, el("p", { cls: "takeaway", text: take.text }),
    el("p", { cls: "small", text: `Right: ${ai.correct} · Wrong: ${ai.wrong} · Escalated (own bucket, not counted as correct): ${ai.escalated} · Not diagnosed: ${ai.no_diagnosis}` }),
    confusionTable(ai));
  const rules = Object.keys(ai.investigators).some((n) => n.startsWith("rule_based"));
  if (rules) {
    card.append(el("p", { cls: "callout info", text: "With the rule-based investigator, this measures the rules, not an LLM." }));
  }
  return card;
}

function overridePanel(a) {
  const o = a.ai.overridden, take = a.takeaways.override;
  const card = el("section", { cls: "card" }, [
    el("h2", { text: "How often does policy overrule the AI?" }),
    el("p", { cls: "muted small", text: "Advice is not authority: the rules can refuse the AI's action or require a human first." }),
  ]);
  if (!take.enough) { card.append(empty("Not enough data yet", take.text)); return card; }
  card.append(
    el("div", { cls: "kpi" }, [el("span", { cls: "value", text: pct(o.rate) }), el("span", { cls: "sub", text: ` refused (${o.count} of ${o.of})` })]),
    el("p", { cls: "small", text: `Required a human instead: ${o.required_approval}` }),
    el("p", { cls: "takeaway", text: take.text }));
  return card;
}

export function renderAnalytics() {
  const a = state.analytics;
  const out = [topbar("Analytics")];
  if (!a) { out.push(el("p", { cls: "muted", text: "Loading…" })); return out; }
  for (const key of ["trend", "outcomes", "stages", "policy", "scatter"]) destroyChart(key);
  if (!a.totals.incidents) {
    out.push(el("div", { cls: "card" }, [empty("No incidents yet – run a scenario",
      "Every chart here is computed from real incidents. Use RUN on the Dashboard, or start the app in demo mode (CHAOS_PILOT_DEMO_DATA=true).")]));
  }
  const t = a.takeaways;
  const resolved = a.mttr.runs.filter((r) => r.outcome === "RESOLVED" && r.recovery_seconds !== null);
  const points = resolved.map((r) => ({ n: r.n, seconds: r.recovery_seconds, id: r.incident_id, scenario: scenarioLabel(r.scenario), text: fmtDuration(r.recovery_seconds) }));
  const stageRows = [{ label: "All recovered runs", avg: a.stages.avg_seconds },
    ...Object.entries(a.stages.by_scenario).map(([s, v]) => ({ label: scenarioLabel(s), avg: v.avg_seconds }))];
  const policyLabels = ["AUTO", "HUMAN_APPROVAL"].filter((k) => a.by_policy[k].avg_recovery !== null);
  out.push(
    el("div", { cls: "two" }, [
      panel("Is recovery getting faster?", "Recovery time per recovered incident, with the average.", t.mttr, (c) => trendChart(c, "trend", points, a.mttr.avg_seconds)),
      panel("How do incidents end?", "Only verified recovery counts as Resolved.", t.outcomes, (c) => donutChart(c, "outcomes", a.outcomes)),
    ]),
    el("div", { cls: "two" }, [
      panel("Where does recovery time go?", "Average time per step in recovered runs.", t.stages,
        (c) => stackedStageChart(c, "stages", stageRows, STAGES, STAGE_LABEL)),
      panel("What does waiting for a human cost?", "Average recovery time by policy decision (recovered runs).", t.by_policy,
        (c) => barChart(c, "policy", policyLabels, policyLabels.map((k) => +a.by_policy[k].avg_recovery.toFixed(2)),
          policyLabels.map((k) => (k === "AUTO" ? "#0f7b72" : "#d9402f")), "Seconds to recover")),
    ]),
    el("div", { cls: "two" }, [aiPanel(a), overridePanel(a)]),
    panel("Is the AI more confident when it is right?", "AI confidence against how each finished incident ended.", t.scatter,
      (c) => scatterChart(c, "scatter", a.scatter)),
  );
  if (a.totals.running) out.push(el("p", { cls: "small muted", text: `${a.totals.running} incident(s) still running are excluded from these numbers.` }));
  return out;
}
