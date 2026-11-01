import { el, button, field, jsonPre } from "../dom.js";
import { fmtDuration, scenarioLabel, pct, OUTCOMES } from "../format.js";
import { cleanupWarning } from "./panels.js";

function cleanupText(c) {
  if (!c) return null;
  if (!c.attempted) return `Skipped: ${c.skipped_reason || "no reason recorded"}`;
  const parts = [c.succeeded ? "completed" : `FAILED: ${c.error || "unknown error"}`, c.verified ? "revert verified" : "revert NOT verified"];
  if (c.verification_detail) parts.push(c.verification_detail);
  return parts.join(" · ");
}

async function share(i, url, hint) {
  if (navigator.share) {
    try { await navigator.share({ title: `Chaos-Pilot report ${i.incident_id}`, url }); return; } catch (e) { if (e.name === "AbortError") return; }
  }
  copy(url, hint);
}

async function copy(url, hint) {
  try {
    await navigator.clipboard.writeText(url);
    hint.textContent = "Link copied. Link works on this machine while the server is running.";
  } catch (e) {
    hint.textContent = `Copy failed. Link: ${url}`;
  }
}

export function renderReportCard(i, { printTarget = false } = {}) {
  const id = encodeURIComponent(i.incident_id);
  const url = `${location.origin}/reports/${id}`;
  const hint = el("span", { cls: "hint", text: "Link works on this machine while the server is running." });
  const print = () => {
    if (printTarget) document.body.classList.add("print-report");
    window.print();
    document.body.classList.remove("print-report");
  };
  const actions = el("div", { cls: "actions-row no-print" }, [
    el("a", { cls: "btn", text: "⬇ Markdown", attrs: { href: `/api/reports/${id}.md`, download: `${i.incident_id}.md` } }),
    el("a", { cls: "btn", text: "⬇ JSON", attrs: { href: `/api/reports/${id}.json`, download: `${i.incident_id}.json` } }),
    button("🖨 PDF (print)", print),
    button("🔗 Copy link", () => copy(url, hint)),
    navigator.share ? button("Share…", () => share(i, url, hint)) : null,
  ]);

  const dl = el("dl", { cls: "fields" });
  const checks = i.verification.length
    ? el("ul", {}, i.verification.map((c) => el("li", { text: `${c.passed ? "PASS" : "FAIL"}: ${c.name}${c.detail ? ` (${c.detail})` : ""}` })))
    : null;
  field(dl, "Incident ID", i.incident_id);
  field(dl, "Scenario", scenarioLabel(i.scenario));
  field(dl, "Outcome", OUTCOMES[i.outcome] || i.outcome);
  field(dl, "Detected signals", i.detected_signals.length ? jsonPre(i.detected_signals) : null);
  field(dl, "Likely cause (AI, advisory)", i.likely_cause);
  field(dl, "Confidence", pct(i.confidence));
  field(dl, "Recommended action (AI)", i.recommended_action);
  field(dl, "Policy decision", i.policy_decision);
  field(dl, "Policy reason", i.policy_reasons.length ? i.policy_reasons.join("; ") : null);
  field(dl, "Executed remediation", i.executed_remediation);
  field(dl, "Verification checks", checks);
  field(dl, "Detect time", i.detect_seconds === null ? null : fmtDuration(i.detect_seconds));
  field(dl, "Recovery time", i.recovery_seconds === null ? null : fmtDuration(i.recovery_seconds));
  field(dl, "Human intervention", i.human_intervention ? "yes" : "no");
  field(dl, "Injected fault", i.injected_fault ? jsonPre(i.injected_fault) : null);
  field(dl, "Pre-injection state", i.pre_injection_state ? jsonPre(i.pre_injection_state) : null);
  field(dl, "Cleanup", cleanupText(i.cleanup));
  if (i.failure_reason) field(dl, "Failure reason", i.failure_reason);
  if (i.abort_reason) field(dl, "Abort reason", i.abort_reason);

  return el("section", { cls: "card section", attrs: { id: "report-section" } }, [
    el("h2", { text: "Report" }), actions, hint, cleanupWarning(i), dl,
  ]);
}
