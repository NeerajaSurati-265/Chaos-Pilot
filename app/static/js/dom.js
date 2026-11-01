// The only DOM builder: API text always goes in through textContent / text nodes, never as HTML.
export function el(tag, opts = {}, children = []) {
  const node = document.createElement(tag);
  if (opts.text !== undefined && opts.text !== null) node.textContent = String(opts.text);
  if (opts.cls) node.className = opts.cls;
  if (opts.attrs) for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, String(v));
  if (opts.on) for (const [k, fn] of Object.entries(opts.on)) node.addEventListener(k, fn);
  for (const child of children) if (child) node.append(child);
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function mount(node, ...children) {
  clear(node);
  for (const child of children) if (child) node.append(child);
}

export function button(text, onClick, cls = "") {
  return el("button", { text, cls, attrs: { type: "button" }, on: { click: onClick } });
}

export function link(text, href, cls = "") {
  return el("a", { text, cls, attrs: { href } });
}

export function empty(title, hint) {
  return el("div", { cls: "empty" }, [el("strong", { text: title }), el("span", { text: hint })]);
}

export function field(dl, label, value) {
  dl.append(el("dt", { text: label }));
  const dd = el("dd");
  if (value instanceof Node) dd.append(value);
  else if (value === null || value === undefined || value === "") {
    dd.className = "not-recorded";
    dd.textContent = "Not recorded yet";
  } else dd.textContent = String(value);
  dl.append(dd);
}

export function jsonPre(value) {
  return el("pre", { text: JSON.stringify(value, null, 2) });
}
