import { el, mount } from "./dom.js";
import { state, refresh, onChange, isActive } from "./state.js";
import { renderSidebar } from "./components/sidebar.js";
import { renderDashboard } from "./views/dashboard.js";
import { renderIncident } from "./views/incident.js";
import { renderIncidents, renderReports, renderApprovals, renderScenarios } from "./views/lists.js";
import { renderAnalytics } from "./views/analytics.js";

export function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const name = parts[0] || "dashboard";
  if (name === "incident" && parts[1]) return { name, id: decodeURIComponent(parts[1]) };
  return { name: ["incidents", "scenarios", "analytics", "reports", "approvals"].includes(name) ? name : "dashboard" };
}

const onStarted = (id) => { location.hash = `#/incident/${encodeURIComponent(id)}`; };

function render() {
  const route = parseRoute();
  renderSidebar(route);
  const banners = document.getElementById("banners");
  mount(banners,
    state.config && state.config.demo
      ? el("div", { cls: "demo-banner", text: "Demo data: sample incidents from mocked AWS", attrs: { role: "status" } }) : null,
    state.error ? el("div", { cls: "error-banner", text: "API unreachable, retrying…", attrs: { role: "alert" } }) : null);
  const view = document.getElementById("view");
  if (!state.config && !state.error) { mount(view, el("p", { cls: "muted", text: "Loading…" })); return; }
  const nodes = {
    dashboard: () => renderDashboard(onStarted),
    incidents: () => renderIncidents(),
    scenarios: () => renderScenarios(onStarted),
    analytics: () => renderAnalytics(),
    reports: () => renderReports(),
    approvals: () => renderApprovals(),
    incident: () => renderIncident(route.id, render),
  }[route.name]();
  mount(view, ...nodes);
  tickCountdowns();
}

function tickCountdowns() {
  const now = performance.now();
  for (const node of document.querySelectorAll("[data-countdown]")) {
    const c = state.countdowns[node.getAttribute("data-countdown")];
    if (!c) { node.textContent = "n/a"; continue; }
    const left = Math.max(0, c.remaining - (now - c.at) / 1000);
    const m = Math.floor(left / 60), s = Math.floor(left % 60);
    node.textContent = `${m}:${String(s).padStart(2, "0")}`;
  }
}

function schedule() {
  setTimeout(async () => {
    if (!document.hidden) await refresh();
    schedule();
  }, isActive() ? 2500 : 8000);
}

onChange(render);
window.addEventListener("hashchange", () => { window.scrollTo(0, 0); render(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
setInterval(tickCountdowns, 1000);
render();
refresh().then(schedule);
