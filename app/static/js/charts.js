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

const COLORS = { RESOLVED: "#0f7b72", FAILED: "#b3301f", DENIED: "#6f6a67", REJECTED: "#4b5767",
  ESCALATED: "#6a3fb5", ABORTED: "#e0a526", AWAITING_APPROVAL: "#d9402f", IN_PROGRESS: "#4a6a9c" };
const STAGE_COLORS = ["#4a6a9c", "#6a3fb5", "#6f6a67", "#d9402f", "#0f7b72", "#e0a526"];
const base = { responsive: true, maintainAspectRatio: false, animation: { duration: 400 } };

function make(canvas, key, config) {
  destroyChart(key);
  instances.set(key, new window.Chart(canvas, config));
}

export function trendChart(canvas, key, points, average) {
  make(canvas, key, {
    type: "line",
    data: { datasets: [
      { label: "Recovery time (s)", data: points.map((p) => ({ x: p.n, y: p.seconds, meta: p })), borderColor: "#e0a526",
        backgroundColor: "rgba(224,165,38,.18)", fill: true, tension: 0.4, pointRadius: 5, pointBackgroundColor: "#0f7b72" },
      ...(average === null ? [] : [{ label: "Average", data: points.map((p) => ({ x: p.n, y: average })), borderColor: "#4a6a9c",
        borderDash: [6, 6], pointRadius: 0, fill: false }]),
    ] },
    options: { ...base, parsing: false,
      scales: { x: { type: "linear", title: { display: true, text: "Incident #" }, ticks: { precision: 0 } },
        y: { beginAtZero: true, title: { display: true, text: "Seconds to recover" } } },
      plugins: { tooltip: { callbacks: {
        title: (items) => `Incident #${items[0].raw.x}`,
        label: (item) => (item.raw.meta ? [`${item.raw.meta.scenario} (${item.raw.meta.id})`, `Recovery: ${item.raw.meta.text}`] : `Average ${item.raw.y.toFixed(1)} s`),
      } } } },
  });
}

export function donutChart(canvas, key, counts) {
  const labels = Object.keys(counts).filter((k) => counts[k] > 0);
  make(canvas, key, {
    type: "doughnut",
    data: { labels, datasets: [{ data: labels.map((k) => counts[k]), backgroundColor: labels.map((k) => COLORS[k]), borderWidth: 2 }] },
    options: { ...base, cutout: "62%", plugins: { legend: { position: "bottom" } } },
  });
}

export function stackedStageChart(canvas, key, rows, stages, labelFor) {
  make(canvas, key, {
    type: "bar",
    data: { labels: rows.map((r) => r.label),
      datasets: stages.map((stage, i) => ({ label: labelFor(stage), data: rows.map((r) => r.avg[stage] || 0),
        backgroundColor: STAGE_COLORS[i % STAGE_COLORS.length] })) },
    options: { ...base, indexAxis: "y",
      scales: { x: { stacked: true, title: { display: true, text: "Average seconds (recovered runs)" } }, y: { stacked: true } },
      plugins: { legend: { position: "bottom" } } },
  });
}

export function barChart(canvas, key, labels, values, colors, yTitle, percent = false) {
  make(canvas, key, {
    type: "bar",
    data: { labels, datasets: [{ data: values, backgroundColor: colors }] },
    options: { ...base, scales: { y: { beginAtZero: true, ...(percent ? { max: 100 } : {}), title: { display: true, text: yTitle } } },
      plugins: { legend: { display: false } } },
  });
}

export function scatterChart(canvas, key, points) {
  const outcomes = [...new Set(points.map((p) => p.outcome))];
  make(canvas, key, {
    type: "scatter",
    data: { datasets: outcomes.map((o) => ({ label: o, backgroundColor: COLORS[o] || "#888",
      data: points.filter((p) => p.outcome === o).map((p, i) => ({ x: p.confidence * 100, y: o, id: p.incident_id })) })) },
    options: { ...base, parsing: false,
      scales: { x: { min: 0, max: 100, title: { display: true, text: "AI confidence (%)" } },
        y: { type: "category", labels: outcomes, title: { display: true, text: "Outcome" } } },
      plugins: { tooltip: { callbacks: { label: (item) => `${item.raw.id}: ${item.raw.x.toFixed(0)}% confident, ${item.dataset.label}` } } } },
  });
}
