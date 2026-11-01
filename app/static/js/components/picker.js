import { el, button } from "../dom.js";
import { state, startBlocker, refresh } from "../state.js";
import { api } from "../api.js";
import { openModal } from "../modal.js";

function explain(card) {
  const dl = el("dl", { cls: "explain" }, [
    el("dt", { text: "We will…" }), el("dd", { text: card.we_will }),
    el("dt", { text: "Expected symptom…" }), el("dd", { text: card.symptom }),
    el("dt", { text: "How Chaos-Pilot fixes it…" }), el("dd", { text: card.fix }),
    el("dt", { text: "Policy it will need" }),
    el("dd", { text: card.policy === "AUTO"
      ? "AUTO: the fix runs on its own because it is allow-listed, reversible and low risk (unless the AI is not confident enough, then a human decides)."
      : "HUMAN_APPROVAL: a person must approve before anything is changed." }),
  ]);
  return dl;
}

export function openScenarioPicker(onStarted, key = null) {
  const runnable = state.scenarios.filter((s) => s.runnable);
  let chosen = runnable.find((s) => s.key === key) || runnable[0] || null;
  let confirming = false;
  openModal("Break something safely", (close) => {
    const body = el("div");
    const draw = () => {
      body.replaceChildren();
      const blocker = startBlocker();
      if (!runnable.length) {
        body.append(el("p", { cls: "why", text: "No runnable scenarios are available." }));
        return;
      }
      if (confirming) {
        body.append(
          el("p", { text: `Run "${chosen.name}" against the isolated test workload now? It will inject a real fault there and then try to fix it.` }),
          el("div", { cls: "actions" }, [
            button("Back", () => { confirming = false; draw(); }),
            button("Yes, run it", async () => {
              try {
                const started = await api(`/scenarios/${encodeURIComponent(chosen.key)}`, "POST");
                close();
                await refresh();
                onStarted(started.incident_id);
              } catch (e) {
                confirming = false;
                draw();
                body.append(el("p", { cls: "why", text: `Start refused: ${e.message}` }));
              }
            }, "primary"),
          ]),
        );
        return;
      }
      const pick = el("div", { cls: "pick" }, runnable.map((s) =>
        el("button", { text: s.name, cls: s.key === chosen.key ? "on" : "", attrs: { type: "button" },
          on: { click: () => { chosen = s; draw(); } } })));
      const start = button("Start…", () => { confirming = true; draw(); }, "primary");
      start.disabled = blocker !== "";
      body.append(pick, explain(chosen), el("div", { cls: "actions" }, [button("Close", close), start]));
      if (blocker) body.append(el("p", { cls: "why", text: blocker }));
    };
    draw();
    return body;
  });
}
