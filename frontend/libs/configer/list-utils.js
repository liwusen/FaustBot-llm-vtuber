// List UI helper utilities

function makeListBox() {
  return el("div", "list-box");
}

function makeListRow(textOrNode, options = {}) {
  const cls = `list-row clickable ${options.extraClass || ""}`.trim();
  const row = el("div", cls);
  if (typeof textOrNode === "string") row.append(el("span", "mono", textOrNode));
  else if (textOrNode instanceof Node) row.append(textOrNode);
  else if (Array.isArray(textOrNode)) for (const n of textOrNode) row.append(n);
  if (options.onClick) row.addEventListener("click", options.onClick);
  return row;
}

function makeOpsToolbar(...buttons) {
  const ops = el("div", "toolbar compact");
  ops.addEventListener("click", (evt) => evt.stopPropagation());
  for (const b of buttons) {
    if (typeof b === "string") ops.append(makeButton(b, () => {}));
    else if (b instanceof Node) ops.append(b);
    else if (typeof b === "object" && typeof b.render === "function") ops.append(b.render());
  }
  return ops;
}
