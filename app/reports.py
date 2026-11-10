"""Markdown rendering of one incident report (the dict from app.incident_view.build)."""
import json
import re

NOT_RECORDED = "Not recorded yet"


def _fence(text: str, lang: str = "") -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{lang}\n{text}\n{ticks}"


def _json_block(value) -> str:
    return NOT_RECORDED if value is None else _fence(json.dumps(value, indent=2, default=str), "json")


def _text(value) -> str:
    return NOT_RECORDED if value is None or value == "" else str(value)


def _seconds(value) -> str:
    return NOT_RECORDED if value is None else f"{value:.1f} s"


def _outcome_line(r: dict) -> str:
    if r["outcome"] == "RESOLVED":
        return f"RESOLVED, recovered in {_seconds(r['recovery_seconds'])} (verified)"
    return r["outcome"].replace("_", " ")


def render_markdown(r: dict) -> str:
    confidence = r["confidence"]
    cleanup = r["cleanup"]
    lines = [
        f"# Incident report {r['incident_id']}",
        "",
        *([f"> **{r['demo_notice']}**", ""] if r.get("demo_notice") else []),
        f"- **Scenario:** {r['scenario']}",
        f"- **Outcome:** {_outcome_line(r)}",
        f"- **Human intervention required:** {'yes' if r['human_intervention'] else 'no'}",
        f"- **Detect time:** {_seconds(r['detect_seconds'])}",
        f"- **Recovery time:** {_seconds(r['recovery_seconds'])}",
        "",
        "## AI diagnosis (advisory)",
        f"- **Likely cause:** {_text(r['likely_cause'])}",
        f"- **Confidence:** {NOT_RECORDED if confidence is None else f'{confidence:.0%}'}",
        f"- **Explanation:** {_text(r.get('explanation'))}",
        f"- **Recommended action:** {_text(r['recommended_action'])}",
        f"- **Investigator:** {_text(r['investigator'])}",
        "",
        "## Policy decision (deterministic)",
        f"- **Decision:** {_text(r['policy_decision'])}",
        f"- **Reasons:** {'; '.join(r['policy_reasons']) if r['policy_reasons'] else NOT_RECORDED}",
        "",
        "## Remediation",
        f"- **Executed:** {_text(r['executed_remediation'])}",
        "",
        "## Verification",
    ]
    if r["verification"]:
        lines += [
            f"- {'PASS' if c['passed'] else 'FAIL'}: {c['name']}"
            + (f" ({c['detail']})" if c["detail"] else "")
            for c in r["verification"]
        ]
    else:
        lines.append(NOT_RECORDED)
    lines += [
        "",
        "## Evidence",
        "### Detected signals",
        _json_block(r["detected_signals"] or None),
        "### Injected fault",
        _json_block(r["injected_fault"]),
        "### Pre-injection state",
        _json_block(r["pre_injection_state"]),
        "",
        "## Cleanup",
    ]
    if cleanup is None:
        lines.append(NOT_RECORDED)
    elif not cleanup["attempted"]:
        lines.append(f"Skipped: {_text(cleanup['skipped_reason'])}")
    else:
        lines += [
            f"- **Completed:** {_text(cleanup['succeeded'])}",
            f"- **Revert verified:** {_text(cleanup['verified'])}"
            + ("" if cleanup["verified"] else "  (WARNING: the injected fault may still be present)"),
            f"- **Detail:** {_text(cleanup['verification_detail'])}",
            f"- **Error:** {_text(cleanup['error'])}",
        ]
    if r["failure_reason"] or r["abort_reason"]:
        lines += ["", "## Why it did not recover"]
        if r["failure_reason"]:
            lines.append(f"- **Failure reason:** {r['failure_reason']}")
        if r["abort_reason"]:
            lines.append(f"- **Abort reason:** {r['abort_reason']}")
    lines += ["", "## Timeline"]
    lines += [f"- {e['at']} [{e['state']}] {e['note']}".rstrip() for e in r["timeline"]]
    return "\n".join(lines) + "\n"
