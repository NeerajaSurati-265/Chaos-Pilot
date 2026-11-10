import { el, button, empty } from "../dom.js";
import { state } from "../state.js";
import { fmtDuration, scenarioLabel, pct, humanize } from "../format.js";
import { chartsAvailable, unavailableNote, lineChart, destroyChart } from "../charts.js";
import { openScenarioPicker } from "../components/picker.js";
import { topbar, chip, scenarioCircle } from "./common.js";

let chartFilter = "all";

function trendBadge(trend, risingIsGood, format) {
  if (!trend || trend.direction === null) {
    return el("span", { cls: "trend flat", text: "–", attrs: { title: "Needs at least 4 finished runs for a trend" } });
  }
  if (trend.direction === "flat") return el("span", { cls: "trend flat", text: "▬ no change", attrs: { title: "Within 10% of earlier runs" } });
  const up = trend.direction === "up";
  return el("span", { cls: `trend ${up === risingIsGood ? "good" : "bad"}`, text: `${up ? "▲" : "▼"} ${format(Math.abs(trend.delta))}`,
    attrs: { title: "Later runs compared with earlier runs" } });
}

function kpiTile(label, value, sub, badge) {
  return el("div", { cls: "card kpi" }, [
    el("div", { cls: "label", text: label }),
    el("div", {}, [el("span", { cls: "value", text: value }), badge]),
    el("div", { cls: "sub", text: sub }),
  ]);
}

function kpiRow() {
  const a = state.analytics;
  if (!a) return el("div", { cls: "kpis" }, [kpiTile("Total incidents", "–", "Loading…", null)]);
  const t = a.totals;
  const points = (d) => `${Math.round(d * 100)} pts`;
  return el("div", { cls: "kpis" }, [
    kpiTile("Total incidents", String(t.incidents), t.incidents ? "since the app started" : "No incidents yet – run a scenario",
      el("span", { cls: "trend flat", text: t.running ? `${t.running} active` : "none active" })),
    kpiTile("Resolved", t.resolved_pct === null ? "–" : pct(t.resolved_pct), "of finished runs, verified recovery only",
      trendBadge(a.trends.resolved_pct, true, points)),
    kpiTile("Avg time to recover", a.mttr.avg_seconds === null ? "–" : fmtDuration(a.mttr.avg_seconds), "MTTR, resolved runs only",
      trendBadge(a.trends.mttr, false, fmtDuration)),
    kpiTile("Human interventions", String(t.human_interventions), "runs that needed a person",
      trendBadge(a.trends.human_interventions, false, points)),
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
  const runs = state.analytics ? state.analytics.mttr.runs : [];
  const scenarios = [...new Set(runs.map((r) => r.scenario))];
  if (chartFilter !== "all" && !scenarios.includes(chartFilter)) chartFilter = "all";
  const select = el("select", { attrs: { "aria-label": "Filter by scenario" } }, [
    el("option", { text: "All scenarios", attrs: { value: "all" } }),
    ...scenarios.map((s) => el("option", { text: scenarioLabel(s), attrs: { value: s } })),
  ]);
  select.value = chartFilter;
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
    const rows = runs.filter((r) => chartFilter === "all" || r.scenario === chartFilter);
    const points = rows.filter((r) => r.outcome === "RESOLVED" && r.recovery_seconds !== null)
      .map((r) => ({ n: r.n, seconds: r.recovery_seconds, id: r.incident_id, scenario: scenarioLabel(r.scenario),
        outcome: r.outcome, text: fmtDuration(r.recovery_seconds) }));
    const skipped = rows.length - points.length;
    const avg = points.length ? points.reduce((sum, p) => sum + p.seconds, 0) / points.length : null;
    note.textContent = [
      avg === null ? "" : `Average ${fmtDuration(avg)} over ${points.length} recovered run(s).`,
      skipped ? `${skipped} run(s) not recovered or still running are not plotted.` : "",
    ].filter(Boolean).join(" ");
    if (!points.length) {
      box.append(el("div", { cls: "chart-note" }, [el("span", { text: runs.length
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
    el("div", { cls: "top-grid" }, [kpiRow(), hero(onStarted)]),
    el("div", { cls: "mid-grid" }, [recoveryChart(), recentList()]),
    scenarioBand(onStarted),
  ];
}
