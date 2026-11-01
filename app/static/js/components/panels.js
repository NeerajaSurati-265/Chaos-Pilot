import { el, button, empty, field, jsonPre } from "../dom.js";
import { api } from "../api.js";
import { state, refresh } from "../state.js";
import { confirmDialog } from "../modal.js";
import { fmtDuration, fmtTime, pct, humanize, OUTCOMES } from "../format.js";
import { stageTitle } from "./flowchart.js";

export function headline(i) {
  const o = i.outcome;
  const at = stageTitle(i.stopped_at);
  let title, sub;
  if (o === "RESOLVED") {
    title = `Recovered in ${fmtDuration(i.recovery_seconds)}`;
    sub = "Verified against real AWS state. Measured from detection to verified recovery.";
  } else if (o === "FAILED") {
    title = `Not recovered – FAILED at ${at}`; sub = i.failure_reason;
  } else if (o === "ABORTED") {
    title = `Not recovered – ABORTED at ${at} (kill switch)`; sub = i.abort_reason;
  } else if (o === "ESCALATED") {
    title = `Not recovered – ESCALATED to a human at ${at}`; sub = "Nothing was executed.";
  } else if (o === "DENIED") {
    title = "Not recovered – DENIED by policy"; sub = (i.policy_reasons || []).join("; ");
  } else if (o === "REJECTED") {
    title = "Not recovered – REJECTED by a human"; sub = "No remediation was run.";
  } else if (o === "AWAITING_APPROVAL") {
    title = "Waiting for a human decision"; sub = "Nothing changes until someone approves or rejects.";
  } else {
    title = "Incident in progress…"; sub = "Recovery is only reported once verification passes.";
  }
  return el("div", { cls: `headline hl-${o}` }, [
    el("h2", { text: title }),
    sub ? el("p", { text: sub }) : null,
    el("p", { cls: "small", text: `${OUTCOMES[o] || o}${i.detect_seconds !== null ? ` · detected in ${fmtDuration(i.detect_seconds)}` : ""}` }),
  ]);
}

export function aiPanel(i) {
  const card = el("section", { cls: "card panel-ai" }, [
    el("h2", {}, [document.createTextNode("AI Investigator says"), el("span", { cls: "tag", text: "Advice only" })]),
  ]);
  if (!i.likely_cause) {
    card.append(empty("No diagnosis yet", "The AI looks at the evidence after the problem is detected."));
    return card;
  }
  const value = Math.round((i.confidence || 0) * 100);
  const meter = el("div", { cls: "meter", attrs: { role: "meter", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": value, "aria-label": "AI confidence" } },
    [el("span", { attrs: { style: `width:${value}%` } })]);
  const dl = el("dl", { cls: "fields" });
  field(dl, "Likely cause", i.likely_cause);
  field(dl, "Confidence", el("div", {}, [el("strong", { text: pct(i.confidence) }), meter]));
  field(dl, "Explanation", i.explanation);
  field(dl, "Recommended action", i.recommended_action);
  field(dl, "Investigator", i.investigator);
  card.append(dl, el("p", { cls: "small muted", text: "The AI never decides whether an incident exists and never authorizes an action." }));
  return card;
}

export function policyPanel(i) {
  const card = el("section", { cls: "card panel-policy" }, [
    el("h2", {}, [document.createTextNode("Policy Engine decided"), el("span", { cls: "tag", text: "Deterministic rules" })]),
  ]);
  if (!i.policy_decision) {
    card.append(empty("No decision yet", "The rules decide after the AI has diagnosed the problem."));
    return card;
  }
  const dl = el("dl", { cls: "fields" });
  field(dl, "Decision", el("strong", { text: i.policy_decision }));
  field(dl, "Action considered", i.policy_action);
  field(dl, "Reasons", el("ul", {}, (i.policy_reasons || []).map((r) => el("li", { text: r }))));
  card.append(dl);
  if (i.policy_denied_ai_action) {
    card.append(el("div", { cls: "callout denied", text: `Policy DENIED the AI's recommended action "${i.recommended_action}". Advice is not authority: the rules decide, and nothing was executed.` }));
  } else if (i.policy_decision === "HUMAN_APPROVAL") {
    card.append(el("div", { cls: "callout info", text: `Policy did not let "${i.recommended_action}" run automatically: a human has to approve it first.` }));
  } else if (i.policy_decision === "ESCALATE") {
    card.append(el("div", { cls: "callout info", text: "The AI itself asked for human review. Policy escalated; nothing was executed." }));
  }
  return card;
}

async function answer(i, approve, note) {
  const ok = await confirmDialog({
    title: approve ? "Approve this fix?" : "Reject this fix?",
    message: approve
      ? `Chaos-Pilot will run "${i.policy_action || i.recommended_action}" on the test workload and then verify recovery.`
      : "Nothing will be changed and the incident ends as REJECTED. The injected fault is cleaned up.",
    confirmLabel: approve ? "Approve" : "Reject",
    danger: !approve,
  });
  if (!ok) return;
  try {
    await api(`/incidents/${encodeURIComponent(i.incident_id)}/${approve ? "approve" : "reject"}`, "POST");
  } catch (e) {
    note.textContent = e.message;
  }
  refresh();
}

export function approvalCard(i) {
  const action = i.policy_action || i.recommended_action;
  const risk = state.config && state.config.actions[action];
  const note = el("div", { cls: "why" });
  const countdown = el("span", { cls: "countdown", attrs: { "data-countdown": i.incident_id } });
  return el("section", { cls: "approval", attrs: { "aria-live": "polite" } }, [
    el("h2", { text: "A human decision is needed" }),
    el("div", { text: `Proposed action: ${humanize(action || "unknown")}` }),
    el("div", { text: risk
      ? `Risk: ${risk.reversible ? "reversible" : "NOT reversible"}, blast radius ${risk.blast_radius}`
      : "Risk: not available" }),
    el("div", { cls: "row" }, [el("span", { text: "Time left to decide: " }), countdown,
      el("span", { cls: "muted small", text: "(if it runs out, the incident ends FAILED and nothing runs)" })]),
    el("div", { cls: "row" }, [
      button("Approve", () => answer(i, true, note), "teal"),
      button("Reject", () => answer(i, false, note), "danger"),
    ]),
    note,
  ]);
}

export function verificationCard(i) {
  const card = el("section", { cls: "card" }, [el("h2", { text: "Double-check: did it really recover?" })]);
  if (!i.verification.length) {
    card.append(empty("No verification ran",
      i.outcome === "IN_PROGRESS" || i.outcome === "AWAITING_APPROVAL"
        ? "Checks run after the fix is applied."
        : "The run ended before the fix was verified, so recovery is NOT claimed."));
    return card;
  }
  card.append(el("ul", { cls: "checks" }, i.verification.map((c) =>
    el("li", {}, [
      el("span", { cls: c.passed ? "ok" : "no", text: c.passed ? "✔ PASS" : "✖ FAIL" }),
      el("span", { text: humanize(c.name) }),
      el("span", { cls: "muted mono", text: c.detail ? `measured: ${c.detail}` : "" }),
    ]))));
  return card;
}

export function timelineCard(i) {
  const card = el("section", { cls: "card" }, [el("h2", { text: "Full timeline" })]);
  let previous = null;
  const list = el("ol", { cls: "timeline" });
  for (const e of i.timeline) {
    const t = new Date(e.at).getTime();
    const gap = previous === null ? null : (t - previous) / 1000;
    previous = t;
    list.append(el("li", { cls: `t-${e.state}` }, [
      el("strong", { text: e.state }), document.createTextNode(" "),
      el("span", { cls: "mono muted", text: fmtTime(e.at) }),
      gap !== null ? el("span", { cls: "elapsed", text: `  +${fmtDuration(gap)}` }) : null,
      el("div", { text: e.note }),
    ]));
  }
  card.append(list);
  return card;
}

export function cleanupWarning(i) {
  const c = i.cleanup;
  if (!c || !c.attempted || (c.verified !== false && c.succeeded !== false)) return null;
  return el("div", { cls: "cleanup-warning", attrs: { role: "alert" },
    text: "⚠ Cleanup NOT verified: the injected fault may still be present. Check AWS manually before starting another run." });
}
