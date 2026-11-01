import { el, mount, button } from "../dom.js";
import { state, pendingApprovals, refresh } from "../state.js";
import { api } from "../api.js";
import { confirmDialog, reasonDialog } from "../modal.js";

const NAV = [
  ["#/", "Dashboard"], ["#/incidents", "Incidents"], ["#/scenarios", "Scenarios"],
  ["#/analytics", "Analytics"], ["#/reports", "Reports"],
];

async function killCall(path) {
  const note = document.getElementById("kill-note");
  try {
    await api(path, "POST");
    await refresh();
  } catch (e) {
    if (note) note.textContent = e.message;
  }
}

async function engage() {
  const reason = await reasonDialog({
    title: "Engage kill switch",
    message: "This blocks new experiments and aborts a running one at its next checkpoint. Why are you stopping?",
    confirmLabel: "Engage kill switch",
  });
  if (reason) killCall(`/kill-switch/engage?reason=${encodeURIComponent(reason)}`);
}

async function release() {
  const ok = await confirmDialog({
    title: "Release kill switch?",
    message: "New experiments will be allowed again. A run that was aborted does not resume.",
    confirmLabel: "Release",
  });
  if (ok) killCall("/kill-switch/release");
}

function workloadCard(config, error) {
  const w = config ? config.workload : null;
  let dotCls = "dot", text = "Waiting for the API";
  if (error) { dotCls = "dot bad"; text = "API unreachable"; }
  else if (config) { dotCls = config.busy ? "dot busy" : "dot ok"; text = config.busy ? "Experiment running" : "Idle"; }
  const dl = el("dl", {}, [
    el("dt", { text: "Cluster" }), el("dd", { text: w ? w.cluster : "…" }),
    el("dt", { text: "Service" }), el("dd", { text: w ? w.service : "…" }),
    el("dt", { text: "Region" }), el("dd", { text: w && w.region ? w.region : "not set" }),
  ]);
  return el("div", { cls: "workload" }, [
    el("div", { cls: "label", text: "Test workload" }),
    dl,
    el("div", { cls: "small", attrs: { title: "Chaos-Pilot's own state, not live ECS status" } }, [
      el("span", { cls: dotCls }), el("span", { text: text }),
    ]),
  ]);
}

export function renderSidebar(route) {
  const config = state.config;
  const pending = pendingApprovals().length;
  const nav = el("nav", { cls: "nav", attrs: { "aria-label": "Main" } });
  for (const [href, label] of NAV) {
    const active = route.name === "dashboard" ? href === "#/" : href === `#/${route.name}` || (route.name === "incident" && href === "#/incidents");
    const children = [el("span", { text: label })];
    if (label === "Incidents" && pending > 0) children.push(el("span", { cls: "count", text: pending, attrs: { title: "Awaiting approval" } }));
    nav.append(el("a", { cls: active ? "active" : "", attrs: { href, ...(active ? { "aria-current": "page" } : {}) } }, children));
  }

  const enabled = config ? config.experiments_enabled : false;
  const pill = el("span", { cls: `pill ${enabled ? "on" : "off"}`, text: config ? (enabled ? "✔ Experiments enabled" : "✖ Experiments disabled") : "Experiments: …" });
  const ks = config ? config.kill_switch : null;
  const kill = el("div", { cls: "kill" }, [
    el("div", { cls: "label", text: "Kill switch" }),
    el("div", { text: ks ? (ks.engaged ? `ENGAGED: ${ks.reason || ""}` : "Released") : "…", cls: ks && ks.engaged ? "why" : "" }),
    el("div", { cls: "row" }, [
      (() => { const b = button("Engage", engage, "danger small-btn"); b.id = "btn-engage"; b.disabled = !ks || ks.engaged; return b; })(),
      (() => { const b = button("Release", release, "small-btn"); b.id = "btn-release"; b.disabled = !ks || !ks.engaged; return b; })(),
    ]),
    el("div", { cls: "small why", attrs: { id: "kill-note" } }),
  ]);

  const logo = el("div", { cls: "logo" }, [el("span", { cls: "logo-mark", text: "✦" }), el("span", { text: "Chaos-Pilot" })]);
  mount(document.getElementById("sidebar"), logo, workloadCard(config, state.error), nav,
    el("div", { cls: "sidebar-foot" }, [pill, kill]));
}
