import { el, mount, button } from "./dom.js";

const root = () => document.getElementById("modal-root");

export function closeModal() {
  mount(root());
}

// Generic dialog. `build(close)` returns the body node(s); Esc and a backdrop click dismiss it.
export function openModal(title, build, onDismiss) {
  const close = () => {
    closeModal();
    document.removeEventListener("keydown", onKey);
  };
  const dismiss = () => {
    close();
    if (onDismiss) onDismiss();
  };
  function onKey(event) {
    if (event.key === "Escape") dismiss();
  }
  const dialog = el("div", { cls: "modal", attrs: { role: "dialog", "aria-modal": "true", "aria-label": title } });
  dialog.append(el("h2", { text: title }), build(close, dismiss));
  const backdrop = el("div", { cls: "modal-backdrop", on: { mousedown: (e) => { if (e.target === backdrop) dismiss(); } } }, [dialog]);
  mount(root(), backdrop);
  document.addEventListener("keydown", onKey);
  const first = dialog.querySelector("input, button");
  if (first) first.focus();
  return close;
}

export function confirmDialog({ title, message, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    openModal(title, (close) =>
      el("div", {}, [
        el("p", { text: message }),
        el("div", { cls: "actions" }, [
          button("Cancel", () => { close(); resolve(false); }),
          button(confirmLabel, () => { close(); resolve(true); }, danger ? "danger" : "primary"),
        ]),
      ]), () => resolve(false));
  });
}

export function reasonDialog({ title, message, confirmLabel }) {
  return new Promise((resolve) => {
    openModal(title, (close) => {
      const input = el("input", { attrs: { type: "text", maxlength: "200", "aria-label": "Reason", placeholder: "Reason (required)" } });
      const error = el("p", { cls: "why", text: "" });
      const submit = () => {
        const value = input.value.trim();
        if (!value) { error.textContent = "A reason is required."; return; }
        close();
        resolve(value);
      };
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
      return el("div", {}, [
        el("p", { text: message }), input, error,
        el("div", { cls: "actions" }, [
          button("Cancel", () => { close(); resolve(null); }),
          button(confirmLabel, submit, "danger"),
        ]),
      ]);
    }, () => resolve(null));
  });
}
