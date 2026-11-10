import { el, empty } from "../dom.js";
import { state, pendingApprovals } from "../state.js";
import { scenarioLabel } from "../format.js";
import { topbar, incidentTable } from "./common.js";
import { approvalCard } from "../components/panels.js";
import { scenarioBand } from "./dashboard.js";

const NONE = (title, hint) => el("div", { cls: "card" }, [empty(title, hint)]);

export function renderIncidents() {
  const rows = state.incidents.slice().reverse();
  return [topbar("Incidents"), el("div", { cls: "card" }, [
    incidentTable(rows, (i) => `#/incident/${encodeURIComponent(i.incident_id)}`,
      empty("No incidents yet – run a scenario", "Use RUN on the Dashboard to start one.")),
  ])];
}

let search = "", outcome = "all";

export function renderReports() {
  const holder = el("div", { cls: "card" });
  const q = el("input", { attrs: { type: "search", placeholder: "Search id, scenario or cause", "aria-label": "Search reports", value: search } });
  const f = el("select", { attrs: { "aria-label": "Filter by outcome" } },
    ["all", "RESOLVED", "FAILED", "DENIED", "REJECTED", "ESCALATED", "ABORTED", "AWAITING_APPROVAL", "IN_PROGRESS"]
      .map((o) => el("option", { text: o === "all" ? "All outcomes" : o.replace("_", " "), attrs: { value: o } })));
  f.value = outcome;
  const table = el("div");
  const draw = () => {
    const needle = search.trim().toLowerCase();
    const rows = state.incidents.slice().reverse().filter((i) =>
      (outcome === "all" || i.outcome === outcome) &&
      (!needle || [i.incident_id, i.scenario, scenarioLabel(i.scenario), i.likely_cause || ""].join(" ").toLowerCase().includes(needle)));
    table.replaceChildren(incidentTable(rows, (i) => `/reports/${encodeURIComponent(i.incident_id)}`,
      empty(state.incidents.length ? "No reports match" : "No reports yet – run a scenario",
        state.incidents.length ? "Clear the search or the outcome filter." : "Every run produces a report.")));
  };
  q.addEventListener("input", () => { search = q.value; draw(); });
  f.addEventListener("change", () => { outcome = f.value; draw(); });
  draw();
  holder.append(el("div", { cls: "filters" }, [q, f]), table);
  return [topbar("Reports"), holder];
}

export function renderApprovals() {
  const pending = pendingApprovals();
  const out = [topbar("Approval queue")];
  if (!pending.length) { out.push(NONE("Nothing is waiting for approval", "Config Change runs pause here for a human decision.")); return out; }
  for (const i of pending) {
    out.push(el("div", { cls: "section" }, [
      el("p", {}, [el("a", { text: `${i.incident_id} · ${scenarioLabel(i.scenario)}`, attrs: { href: `#/incident/${encodeURIComponent(i.incident_id)}` } })]),
      approvalCard(i),
    ]));
  }
  return out;
}

export function renderScenarios(onStarted) {
  const out = [topbar("Scenarios"), scenarioBand(onStarted)];
  for (const s of state.scenarios.filter((x) => x.runnable !== false && x.incident_scenario)) {
    out.push(el("section", { cls: "card section" }, [
      el("h2", { text: s.name }),
      el("dl", { cls: "fields" }, [
        el("dt", { text: "We will…" }), el("dd", { text: s.we_will }),
        el("dt", { text: "Expected symptom…" }), el("dd", { text: s.symptom }),
        el("dt", { text: "How Chaos-Pilot fixes it…" }), el("dd", { text: s.fix }),
        el("dt", { text: "Default policy" }), el("dd", { text: s.policy || "n/a" }),
      ]),
    ]));
  }
  return out;
}
