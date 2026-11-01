import { el, empty } from "../dom.js";
import { findIncident } from "../state.js";
import { scenarioLabel } from "../format.js";
import { topbar, chip } from "./common.js";
import { renderFlowchart } from "../components/flowchart.js";
import { headline, aiPanel, policyPanel, approvalCard, verificationCard, timelineCard, cleanupWarning } from "../components/panels.js";
import { renderReportCard } from "../components/reportcard.js";

let mode = "simple";
try { mode = localStorage.getItem("cp-view") || "simple"; } catch (e) { /* storage unavailable */ }
let opened = null, openedFor = null;

export function renderIncident(id, rerender) {
  const i = findIncident(id);
  if (!i) {
    return [topbar("Incident"), el("div", { cls: "card" }, [empty("Incident not found",
      "Incidents are kept in memory, so they disappear when the server restarts. Pick one from the list.")])];
  }
  if (openedFor !== id) { opened = null; openedFor = id; }
  const setMode = (m) => { mode = m; try { localStorage.setItem("cp-view", m); } catch (e) { /* ignore */ } rerender(); };
  const toggle = el("div", { cls: "toggle", attrs: { role: "group", "aria-label": "View" } },
    [["simple", "Simple view"], ["engineer", "Engineer view"]].map(([m, label]) =>
      el("button", { text: label, cls: mode === m ? "on" : "", attrs: { type: "button", "aria-pressed": String(mode === m) }, on: { click: () => setMode(m) } })));
  const flowCard = el("section", { cls: "card section" }, [
    el("div", { cls: "card-head" }, [el("h2", { text: "What happened" }), toggle]),
    renderFlowchart(i, mode, opened, (key) => { opened = key; rerender(); }),
  ]);
  const out = [
    el("p", { cls: "crumbs" }, [el("a", { text: "‹ All incidents", attrs: { href: "#/incidents" } })]),
    topbar(`${scenarioLabel(i.scenario)} · ${i.incident_id}`),
    headline(i),
    cleanupWarning(i),
    flowCard,
    i.outcome === "AWAITING_APPROVAL" ? approvalCard(i) : null,
    el("div", { cls: "two" }, [aiPanel(i), policyPanel(i)]),
    verificationCard(i),
    timelineCard(i),
    renderReportCard(i, { printTarget: true }),
  ];
  return out.filter(Boolean);
}
