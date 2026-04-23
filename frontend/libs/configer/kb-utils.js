// KB utilities

function normalizeKbPath(raw) {
  const input = String(raw || "").replace(/\\/g, "/").trim();
  if (!input || input === "/") return "/";
  const withLeading = input.startsWith("/") ? input : `/${input}`;
  const compact = withLeading.replace(/\/{2,}/g, "/");
  return compact.endsWith("/") ? compact.slice(0, -1) : compact;
}

function kbParentPath(path) {
  const p = normalizeKbPath(path);
  if (p === "/") return "/";
  const idx = p.lastIndexOf("/");
  return idx <= 0 ? "/" : p.slice(0, idx);
}

function findKbNodeByPath(root, path) {
  if (!root || typeof root !== "object") return null;
  const target = normalizeKbPath(path);
  const stack = [root];
  while (stack.length) {
    const cur = stack.pop();
    const curPath = normalizeKbPath(cur.path || "/");
    if (curPath === target) return cur;
    for (const child of cur.children || []) stack.push(child);
  }
  return null;
}

function getKbChildren(root, dirPath) {
  const dirNode = findKbNodeByPath(root, dirPath);
  if (!dirNode || String(dirNode.type || "dir") === "file") return [];
  const rows = (dirNode.children || []).map((child) => ({
    path: normalizeKbPath(child.path || "/"),
    type: String(child.type || "dir"),
    name: String(child.name || child.path || "") || "/",
  }));
  rows.sort((a, b) => {
    if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
  return rows;
}
