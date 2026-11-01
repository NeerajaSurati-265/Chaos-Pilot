const SCENARIO_LABELS = { bad_deployment: "Bad Deployment", config_change: "Config Change" };

// Incident reports use bad_deployment, the start API uses bad-deployment.
export function scenarioLabel(name) {
  const key = String(name || "").replace(/-/g, "_");
  return SCENARIO_LABELS[key] || String(name || "unknown");
}

export function scenarioIcon(name) {
  const key = String(name || "").replace(/-/g, "_");
  return { cls: SCENARIO_LABELS[key] ? `ic-${key}` : "ic-other", glyph: key === "config_change" ? "⚙" : key === "bad_deployment" ? "⚠" : "?" };
}

export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return "n/a";
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const m = Math.floor(seconds / 60);
  return `${m}m ${Math.round(seconds - m * 60)}s`;
}

export function fmtTime(iso) {
  const d = new Date(iso);
  return isNaN(d) ? String(iso) : d.toLocaleTimeString();
}

export function fmtDateTime(iso) {
  const d = new Date(iso);
  return isNaN(d) ? String(iso) : d.toLocaleString();
}

export function pct(value) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : null;
}

export const OUTCOMES = {
  RESOLVED: "✔ RESOLVED",
  FAILED: "✖ FAILED",
  DENIED: "⊘ DENIED",
  REJECTED: "⊘ REJECTED",
  ESCALATED: "▲ ESCALATED",
  ABORTED: "■ ABORTED",
  AWAITING_APPROVAL: "⏸ AWAITING",
  IN_PROGRESS: "● RUNNING",
};

export function humanize(name) {
  return String(name).replace(/_/g, " ");
}

export function shorten(text, max = 90) {
  const s = String(text || "");
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

export function openedAt(incident) {
  return incident.timeline.length ? incident.timeline[0].at : null;
}
