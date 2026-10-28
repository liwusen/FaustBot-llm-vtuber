// DOM helpers for configer (global functions used by config-window.js)

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function makeButton(text, onClick, className = "btn btn-ghost") {
  const btn = el("button", className, text);
  btn.type = "button";
  btn.addEventListener("click", onClick);
  return btn;
}

// ── 分块渲染：大列表按帧分批 append，避免一次性同步创建大量 DOM 卡顿 ──
// 用法: appendChunked(container, items, (item, i) => el("div", "row", item.name), { onDone: fn });
function appendChunked(container, items, renderFn, opts) {
  const options = opts || {};
  const chunkSize = options.chunkSize || 50;
  const list = Array.isArray(items) ? items : [];
  let index = 0;
  let cancelled = false;
  let rafId = null;

  function nextChunk() {
    if (cancelled) return;
    const end = Math.min(index + chunkSize, list.length);
    for (; index < end; index++) {
      const node = renderFn(list[index], index);
      if (node) container.appendChild(node);
    }
    if (options.onProgress) options.onProgress(index, list.length);
    if (index < list.length) {
      rafId = requestAnimationFrame(nextChunk);
    } else if (options.onDone) {
      options.onDone();
    }
  }

  if (list.length === 0) {
    if (options.onDone) options.onDone();
  } else {
    nextChunk();
  }

  return {
    cancel() {
      cancelled = true;
      if (rafId !== null) cancelAnimationFrame(rafId);
    }
  };
}
