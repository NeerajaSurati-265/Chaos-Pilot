import { el } from "./dom.js";

// Chart.js comes from a CDN. If it did not load, the chart box says so instead of staying blank.
export function chartsAvailable() {
  return typeof window.Chart === "function";
}

export function unavailableNote() {
  return el("div", { cls: "chart-note", text: "Charts are unavailable (the Chart.js CDN could not be loaded, e.g. you are offline). The numbers elsewhere on the page are unaffected." });
}

const instances = new Map();

export function lineChart(canvas, key, points, filterLabel) {
  const old = instances.get(key);
  if (old) old.destroy();
  const chart = new window.Chart(canvas, {
    type: "line",
    data: {
      datasets: [{
        label: "Recovery time (s)",
        data: points.map((p) => ({ x: p.n, y: p.seconds, meta: p })),
        borderColor: "#e0a526", backgroundColor: "rgba(224,165,38,.18)", fill: true,
        tension: 0.4, pointRadius: 5, pointBackgroundColor: "#0f7b72",
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 400 },
      parsing: false,
      scales: {
        x: { type: "linear", title: { display: true, text: "Incident #" }, ticks: { precision: 0 } },
        y: { beginAtZero: true, title: { display: true, text: "Seconds to recover" } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => `Incident #${items[0].raw.x} (${items[0].raw.meta.id})`,
            label: (item) => [
              item.raw.meta.scenario,
              `Outcome: ${item.raw.meta.outcome}`,
              `Recovery: ${item.raw.meta.text}`,
            ],
          },
        },
        title: { display: false, text: filterLabel },
      },
    },
  });
  instances.set(key, chart);
}

export function destroyChart(key) {
  const chart = instances.get(key);
  if (chart) chart.destroy();
  instances.delete(key);
}
