import { el, jsonPre } from "../dom.js";
import { fmtDuration, fmtTime, pct, shorten, humanize } from "../format.js";

// node key, title, icon, state that starts it, matching stage_seconds key.
const NODES = [
  { key: "break", title: "Break", icon: "💥", state: "PENDING", stage: "break" },
  { key: "notice", title: "Notice", icon: "👁", state: "INJECTED", stage: "notice" },
  { key: "investigate", title: "Investigate (AI)", icon: "🔎", state: "INVESTIGATING", stage: "investigate" },
  { key: "decide", title: "Decide (Rules)", icon: "⚖", state: "POLICY_DECIDED", stage: "decide" },
  { key: "human", title: "Ask a human", icon: "🙋", state: "AWAITING_APPROVAL", stage: "approval_wait" },
  { key: "fix", title: "Fix", icon: "🔧", state: "REMEDIATING", stage: "fix" },
  { key: "check", title: "Double-check", icon: "✅", state: "VERIFYING", stage: "verify" },
  { key: "report", title: "Report", icon: "📄", state: null, stage: null },
];
const STAGE_NODE = { break: "break", notice: "notice", investigate: "investigate", decide: "decide", approval_wait: "human", fix: "fix", verify: "check" };
const TERMINAL = ["RESOLVED", "FAILED", "ABORTED", "ESCALATED", "REJECTED"];

export function stageTitle(stage) {
  const node = stage ? NODES.find((n) => n.stage === stage) : null;
  return node ? node.title : "an unknown step";
}

export function nodeStatuses(incident) {
  const seen = new Set(incident.timeline.map((e) => e.state));
  const nodes = NODES.filter((n) => n.key !== "human" || seen.has("AWAITING_APPROVAL"));
  const status = {};
  const terminal = TERMINAL.includes(incident.state);
  let current = -1;
  nodes.forEach((n, i) => { if (n.state && seen.has(n.state)) current = i; });
  nodes.forEach((n, i) => { status[n.key] = i < current ? "done" : i === current ? "active" : "pending"; });
  if (incident.state === "RESOLVED") {
    for (const n of nodes) status[n.key] = "done";
  } else if (terminal) {
    const stoppedNode = STAGE_NODE[incident.stopped_at] || (nodes[current] && nodes[current].key);
    const terminalStatus = { FAILED: "failed", ABORTED: "aborted", ESCALATED: "escalated",
      REJECTED: incident.outcome === "DENIED" ? "denied" : "rejected" }[incident.state];
    let after = false;
    for (const n of nodes) {
      if (n.key === "report") { status[n.key] = "done"; continue; }
      if (n.key === stoppedNode) { status[n.key] = terminalStatus; after = true; }
      else status[n.key] = after ? "skipped" : "done";
    }
  }
  return { nodes, status };
}

function caption(node, st, i) {
  const policy = i.policy_decision;
  const reason = i.policy_reasons && i.policy_reasons[0];
  switch (node.key) {
    case "break":
      if (st === "pending" || st === "active") return "Preparing to break the test workload…";
      return i.injected_fault ? `Injected: ${humanize(i.injected_fault.type || "fault")}` : "Fault injection did not complete";
    case "notice":
      if (st === "active") return "Watching AWS for symptoms…";
      return i.detected_signals.length ? `Spotted the problem from ${i.detected_signals.length} signals` : "Nothing was detected";
    case "investigate":
      return i.likely_cause ? `AI is ${pct(i.confidence)} confident: ${shorten(i.likely_cause, 80)}` : "AI is looking at the evidence…";
    case "decide":
      return policy ? `Rules chose ${policy}${reason ? `: ${shorten(reason, 70)}` : ""}` : "Rules are deciding…";
    case "human":
      if (st === "active") return "Waiting for a person to approve or reject";
      if (st === "rejected") return "A person rejected the fix";
      if (st === "failed") return "No answer in time";
      return "A person approved the fix";
    case "fix":
      return st === "pending" || st === "skipped" ? "Fix was not run" : (i.executed_remediation ? shorten(i.executed_remediation, 80) : `Applying ${humanize(i.policy_action || "the fix")}…`);
    case "check": {
      const total = i.verification.length, ok = i.verification.filter((c) => c.passed).length;
      return total ? `${ok}/${total} checks passed` : "Checking real AWS health…";
    }
    case "report":
      return "Report recorded";
    default:
      return "";
  }
}

function failureReason(node, st, i) {
  if (st === "failed") return i.failure_reason;
  if (st === "aborted") return i.abort_reason;
  if (st === "denied" || st === "rejected" || st === "escalated") {
    return (i.policy_reasons && i.policy_reasons.join("; ")) || i.timeline.filter((e) => e.state === i.state)[0]?.note;
  }
  return null;
}

function detail(node, i) {
  const events = i.timeline.filter((e) => e.state === node.state || (node.key === "investigate" && e.state === "DETECTED"));
  const box = el("div", { cls: "node-detail" }, [el("div", { cls: "label", text: `${node.title}: technical detail` })]);
  const evidence = {
    break: { injected_fault: i.injected_fault, pre_injection_state: i.pre_injection_state },
    notice: { detected_signals: i.detected_signals },
    investigate: { likely_cause: i.likely_cause, confidence: i.confidence, explanation: i.explanation, recommended_action: i.recommended_action, investigator: i.investigator },
    decide: { decision: i.policy_decision, action: i.policy_action, reasons: i.policy_reasons },
    human: { approval_deadline: i.approval_deadline, human_intervention: i.human_intervention },
    fix: { executed_remediation: i.executed_remediation, remediation_result: i.remediation_result },
    check: { verification: i.verification },
    report: { cleanup: i.cleanup, failure_reason: i.failure_reason, abort_reason: i.abort_reason },
  }[node.key];
  for (const e of events) {
    box.append(el("div", { cls: "small mono", text: `${fmtTime(e.at)}  [${e.state}] ${e.note}` }));
  }
  if (evidence) box.append(jsonPre(evidence));
  return box;
}

export function renderFlowchart(incident, view, opened, toggleOpen) {
  const { nodes, status } = nodeStatuses(incident);
  const row = el("div", { cls: "flow", attrs: { role: "list", "aria-label": "What happened" } });
  for (const node of nodes) {
    const st = status[node.key];
    const secs = node.stage ? incident.stage_seconds[node.stage] : null;
    const isOpen = opened === node.key;
    const wrap = el("div", { cls: `node-wrap st-${st}${st === "done" ? " done" : ""}`, attrs: { role: "listitem" } });
    const btn = el("button", { cls: "node", text: node.icon, attrs: {
      type: "button", "aria-expanded": String(isOpen), "aria-label": `${node.title}: ${st}. Click for technical detail`,
      title: "Click for technical detail" }, on: { click: () => toggleOpen(isOpen ? null : node.key) } });
    wrap.append(btn, el("div", { cls: "title", text: node.title }));
    wrap.append(el("div", { cls: "cap", text: caption(node, st, incident) }));
    const reason = failureReason(node, st, incident);
    if (reason) wrap.append(el("div", { cls: "cap why", text: reason }));
    if (secs !== null && secs !== undefined) wrap.append(el("div", { cls: "secs", text: fmtDuration(secs) }));
    else if (st === "active") wrap.append(el("div", { cls: "secs muted", text: "running…" }));
    if (view === "engineer") wrap.append(el("div", { cls: "cap mono", text: node.state || "terminal" }));
    row.append(wrap);
  }
  const out = el("div", {}, [row]);
  const openNode = nodes.find((n) => n.key === opened);
  if (openNode) out.append(detail(openNode, incident));
  return out;
}
