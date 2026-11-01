import { el, mount, empty } from "./dom.js";
import { api } from "./api.js";
import { renderReportCard } from "./components/reportcard.js";
import { headline } from "./components/panels.js";

const root = document.getElementById("report-root");
const id = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");

async function load() {
  try {
    const incident = await api(`/incidents/${encodeURIComponent(id)}`);
    document.title = `Chaos-Pilot report ${incident.incident_id}`;
    mount(root, el("h1", { text: `Report ${incident.incident_id}` }), headline(incident), renderReportCard(incident));
  } catch (e) {
    mount(root, el("div", { cls: "card" }, [empty(e.status === 404 ? "Report not found" : "Could not load the report",
      e.status === 404 ? "Incidents are kept in memory: they disappear when the server restarts." : e.message)]));
  }
}
load();
