import { el, button, empty } from "../dom.js";
import { state } from "../state.js";
import { fmtDuration, scenarioLabel, pct, humanize } from "../format.js";
import { chartsAvailable, unavailableNote, lineChart, destroyChart } from "../charts.js";
import { openScenarioPicker } from "../components/picker.js";
import { topbar, chip, scenarioCircle } from "./common.js";

let chartFilter = "all";
const FINISHED = ["RESOLVED", "FAILED", "DENIED", "REJECTED", "ESCALATED", "ABORTED"];

const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);

function metrics(finished) {
  const resolved = finished.filter((i) => i.outcome === "RESOLVED");
  return {
    resolvedRate: finished.length ? resolved.length / finished.length : null,
    mttr: avg(resolved.map((i) => i.recovery_seconds).filter((s) => s !== null)),
    humanRate: finished.length ? finished.filter((i) => i.human_intervention).length / finished.length : null,
  };
}

// good: whether a rise is good news. Needs 4+ finished runs: later half vs earlier half.
function trend(finished, key, risingIsGood, format) {
  if (finished.length < 4) return el("span", { cls: "trend flat", text: "–", attrs: { title: "Needs at least 4 finished runs for a trend" } });
  const half = Math.floor(finished.length / 2);
  const before = metrics(finished.slice(0, half))[key], after = metrics(finished.slice(half))[key];
  if (before === null || after === null) return el("span", { cls: "trend flat", text: "–", attrs: { title: "Not enough data in one half" } });
  const delta = after - before;
  if (Math.abs(delta) < 1e-9) return el("span", { cls: "trend flat", text: "▬ no change" });
  const good = (delta > 0) === risingIsGood;
  return el("span", { cls: `trend ${good ? "good" : "bad"}`, text: `${delta > 0 ? "▲" : "▼"} ${format(Math.abs(delta))}`,
    attrs: { title: "Later runs compared with earlier runs" } });
}

function kpiTile(label, value, sub, badge) {
  return el("div", { cls: "card kpi" }, [
    el("div", { cls: "label", text: label }),
    el("div", {}, [el("span", { cls: "value", text: value }), badge]),
    el("div", { cls: "sub", text: sub }),
  ]);
}

function kpiRow(incidents) {
  const finished = incidents.filter((i) => FINISHED.includes(i.outcome));
  const m = metrics(finished);
  const active = incidents.length - finished.length;
  const humans = incidents.filter((i) => i.human_intervention).length;
  return el("div", { cls: "kpis" }, [
    kpiTile("Total incidents", String(incidents.length), incidents.length ? "since the app started" : "No incidents yet – run a scenario",
      el("span", { cls: "trend flat", text: active ? `${active} active` : "none active" })),
    kpiTile("Resolved", m.resolvedRate === null ? "–" : pct(m.resolvedRate), "of finished runs, verified recovery only",
      trend(finished, "resolvedRate", true, (d) => `${Math.round(d * 100)} pts`)),
    kpiTile("Avg time to recover", m.mttr === null ? "–" : fmtDuration(m.mttr), "MTTR, resolved runs only",
      trend(finished, "mttr", false, (d) => fmtDuration(d))),
    kpiTile("Human interventions", String(humans), "runs that needed a person",
      trend(finished, "humanRate", false, (d) => `${Math.round(d * 100)} pts`)),
  ]);
}

function hero(onStarted) {
  const run = button("RUN", () => openScenarioPicker(onStarted), "run-btn");
  run.setAttribute("aria-label", "Open the scenario picker");
  return el("div", { cls: "card hero" }, [
    el("div", { cls: "label", text: "Chaos experiment" }),
    el("h2", { text: "Break something safely" }),
    el("p", { cls: "muted", text: "Inject a controlled fault into the isolated test workload and watch Chaos-Pilot investigate, decide, fix and verify." }),
    run,
  ]);
}

function recoveryChart() {
  const incidents = state.incidents;
  const scenarios = [...new Set(incidents.map((i) => i.scenario))];
  const select = el("select", { attrs: { "aria-label": "Filter by scenario" } }, [
    el("option", { text: "All scenarios", attrs: { value: "all" } }),
    ...scenarios.map((s) => el("option", { text: scenarioLabel(s), attrs: { value: s } })),
  ]);
  select.value = scenarios.includes(chartFilter) ? chartFilter : "all";
  select.addEventListener("change", () => { chartFilter = select.value; draw(); });

  const box = el("div", { cls: "chart-box" });
  const note = el("p", { cls: "small muted" });
  const card = el("div", { cls: "card" }, [
    el("div", { cls: "card-head" }, [el("h2", { text: "Recovery time" }), select]),
    box, note,
  ]);

  function draw() {
    box.replaceChildren();
    destroyChart("recovery");
    const rows = incidents.map((i, idx) => ({ i, n: idx + 1 }))
      .filter(({ i }) => chartFilter === "all" || i.scenario === chartFilter);
    const points = rows.filter(({ i }) => i.outcome === "RESOLVED" && i.recovery_seconds !== null)
      .map(({ i, n }) => ({ n, seconds: i.recovery_seconds, id: i.incident_id, scenario: scenarioLabel(i.scenario), outcome: i.outcome, text: fmtDuration(i.recovery_seconds) }));
    const skipped = rows.length - points.length;
    note.textContent = skipped ? `${skipped} run(s) not recovered or still running are not plotted.` : "";
    if (!points.length) {
      box.append(el("div", { cls: "chart-note" }, [el("span", { text: incidents.length
        ? "No recovered incidents to plot yet. Failed or running incidents have no recovery time."
        : "No incidents yet – run a scenario and its recovery time will appear here." })]));
      return;
    }
    if (!chartsAvailable()) { box.append(unavailableNote()); return; }
    const canvas = el("canvas", { attrs: { "aria-label": "Recovery time per incident", role: "img" } });
    box.append(canvas);
    lineChart(canvas, "recovery", points, chartFilter);
  }
  queueMicrotask(draw);
  return card;
}

function recentList() {
  const recent = state.incidents.slice().reverse().slice(0, 6);
  const card = el("div", { cls: "card" }, [
    el("div", { cls: "card-head" }, [el("h2", { text: "Recent incidents" }), el("a", { text: "View all ›", attrs: { href: "#/incidents" } })]),
  ]);
  if (!recent.length) { card.append(empty("No incidents yet – run a scenario", "Press RUN to start one.")); return card; }
  card.append(el("ul", { cls: "list" }, recent.map((i) =>
    el("li", {}, [el("a", { cls: "item", attrs: { href: `#/incident/${encodeURIComponent(i.incident_id)}` } }, [
      scenarioCircle(i.scenario),
      el("div", { cls: "grow" }, [el("div", { cls: "mono", text: i.incident_id }),
        el("div", { cls: "small muted", text: `${scenarioLabel(i.scenario)} · AI ${pct(i.confidence) || "n/a"}` })]),
      chip(i.outcome),
    ])]))));
  return card;
}

export function scenarioBand(onStarted) {
  const band = el("section", { cls: "band" }, [el("h2", { text: "Scenarios" })]);
  const cards = el("div", { cls: "cards" });
  for (const s of state.scenarios) {
    const soon = s.incident_scenario === null;
    const card = el("div", { cls: `scard${soon ? " soon" : ""}` }, [
      scenarioCircle(s.incident_scenario || s.key),
      el("h3", { text: s.name }),
      el("p", { cls: "small muted", text: s.description }),
    ]);
    if (soon) {
      card.append(el("span", { cls: "ribbon", text: "Coming soon" }));
      const b = button("Not available", () => {}, "small-btn"); b.disabled = true;
      card.append(b);
    } else {
      card.append(
        el("div", {}, [el("span", { cls: `badge policy-${s.policy}`, text: s.policy ? `${s.policy}` : "no policy" })]),
        el("div", { cls: "stat-row" }, [
          el("span", {}, [el("b", { text: s.runs }), document.createTextNode(" runs")]),
          el("span", {}, [document.createTextNode("avg "), el("b", { text: s.avg_recovery_seconds === null ? "–" : fmtDuration(s.avg_recovery_seconds) })]),
        ]),
        s.runs ? null : el("div", { cls: "small muted", text: "No runs yet" }),
      );
      const run = button("Run…", () => openScenarioPicker(onStarted, s.key), "primary small-btn");
      run.disabled = !s.runnable;
      card.append(run);
    }
    cards.append(card);
  }
  cards.append(el("a", { cls: "scard analytics", attrs: { href: "#/analytics" } }, [
    el("span", { cls: "icon-circle", text: "📊" }), el("h3", { text: "Full Analytics ›" }),
    el("p", { cls: "small", text: "Trends, outcomes and where recovery time goes." }),
  ]));
  band.append(cards);
  return band;
}

export function renderDashboard(onStarted) {
  return [
    topbar("Dashboard"),
    el("div", { cls: "top-grid" }, [kpiRow(state.incidents), hero(onStarted)]),
    el("div", { cls: "mid-grid" }, [recoveryChart(), recentList()]),
    scenarioBand(onStarted),
  ];
}
