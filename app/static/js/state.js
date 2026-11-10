import { api } from "./api.js";

export const state = {
  config: null,
  incidents: [],
  scenarios: [],
  analytics: null,
  error: false,
  // Server-computed seconds left for the incident awaiting approval, with the local time it was read.
  countdowns: {},
};

const listeners = [];
export function onChange(fn) {
  listeners.push(fn);
}

// Signature ignores values that tick every poll, so views re-render only on real changes.
function signature() {
  return JSON.stringify(
    [state.config, state.incidents, state.scenarios, state.analytics, state.error],
    (key, value) => (key === "approval_seconds_remaining" ? undefined : value),
  );
}

let last = "";
let inFlight = false;

export async function refresh() {
  if (inFlight) return;
  inFlight = true;
  try {
    const [config, incidents, scenarios, analytics] = await Promise.all([
      api("/api/config"), api("/incidents"), api("/api/scenarios"), api("/api/analytics"),
    ]);
    state.config = config;
    state.analytics = analytics;
    state.incidents = incidents;
    state.scenarios = scenarios;
    state.error = false;
    const now = performance.now();
    state.countdowns = {};
    for (const i of incidents) {
      if (i.approval_seconds_remaining !== null && i.approval_seconds_remaining !== undefined) {
        state.countdowns[i.incident_id] = { remaining: i.approval_seconds_remaining, at: now };
      }
    }
  } catch (e) {
    state.error = true;
  } finally {
    inFlight = false;
  }
  const sig = signature();
  if (sig !== last) {
    last = sig;
    for (const fn of listeners) fn();
  }
}

export function isActive() {
  return state.incidents.some((i) => i.outcome === "IN_PROGRESS" || i.outcome === "AWAITING_APPROVAL");
}

export function pendingApprovals() {
  return state.incidents.filter((i) => i.outcome === "AWAITING_APPROVAL");
}

export function findIncident(id) {
  return state.incidents.find((i) => i.incident_id === id) || null;
}

// Why Start is disabled, in plain words ("" when it can start).
export function startBlocker() {
  const c = state.config;
  if (!c) return "Waiting for the API…";
  if (!c.experiments_enabled) return "Experiments disabled: set CHAOS_PILOT_EXPERIMENTS_ENABLED=true and restart the app";
  if (c.kill_switch.engaged) return "Kill switch engaged: release it to start experiments";
  if (c.busy) return "An experiment is already running";
  return "";
}
