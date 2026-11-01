import { el, button, empty } from "../dom.js";
import { pendingApprovals } from "../state.js";
import { scenarioLabel, scenarioIcon, fmtDateTime, openedAt, OUTCOMES, pct } from "../format.js";

export function topbar(title) {
  const n = pendingApprovals().length;
  const bell = el("a", { cls: `bell${n ? "" : " zero"}`, text: n ? `🔔 ${n} awaiting approval` : "🔔 0 pending",
    attrs: { href: "#/approvals", title: "Open the approval queue" } });
  return el("div", { cls: "topbar" }, [el("h1", { text: title }), bell]);
}

export function chip(outcome) {
  return el("span", { cls: `chip o-${outcome}`, text: OUTCOMES[outcome] || outcome });
}

export function scenarioCircle(name) {
  const icon = scenarioIcon(name);
  return el("span", { cls: `icon-circle ${icon.cls}`, text: icon.glyph, attrs: { "aria-hidden": "true" } });
}

export function incidentTable(rows, hrefFor, emptyMessage) {
  if (!rows.length) return emptyMessage;
  const head = el("tr", {}, ["Incident", "Scenario", "Opened", "AI confidence", "Outcome"].map((h) => el("th", { text: h })));
  const body = rows.map((i) => {
    const tr = el("tr", { cls: "click", attrs: { tabindex: "0" } }, [
      el("td", {}, [el("span", { cls: "mono", text: i.incident_id })]),
      el("td", { text: scenarioLabel(i.scenario) }),
      el("td", { text: openedAt(i) ? fmtDateTime(openedAt(i)) : "n/a" }),
      el("td", { text: pct(i.confidence) || "n/a" }),
      el("td", {}, [chip(i.outcome)]),
    ]);
    const go = () => { location.href = hrefFor(i); };
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    return tr;
  });
  return el("table", {}, [el("thead", {}, [head]), el("tbody", {}, body)]);
}
