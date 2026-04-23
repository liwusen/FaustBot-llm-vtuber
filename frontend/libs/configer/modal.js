function ensureModalRoot() {
  let overlay = document.getElementById("cfgModalOverlay");
  if (overlay) return overlay;

  overlay = el("div", "cfg-modal-overlay hidden");
  overlay.id = "cfgModalOverlay";
  const dialog = el("div", "cfg-modal");
  const header = el("div", "cfg-modal-head");
  const title = el("h3", "cfg-modal-title", "弹窗");
  title.id = "cfgModalTitle";
  const closeBtn = makeButton("关闭", () => closeModal());
  closeBtn.className = "btn btn-ghost";
  header.append(title, closeBtn);
  const body = el("div", "cfg-modal-body");
  body.id = "cfgModalBody";
  dialog.append(header, body);
  overlay.append(dialog);
  overlay.addEventListener("click", (evt) => {
    if (evt.target === overlay) closeModal();
  });
  document.body.append(overlay);
  return overlay;
}

function openModal(title, bodyNodes) {
  const overlay = ensureModalRoot();
  const titleEl = document.getElementById("cfgModalTitle");
  const bodyEl = document.getElementById("cfgModalBody");
  titleEl.textContent = title;
  bodyEl.innerHTML = "";
  for (const n of bodyNodes) bodyEl.append(n);
  overlay.classList.remove("hidden");
}

function closeModal() {
  const overlay = document.getElementById("cfgModalOverlay");
  if (overlay) overlay.classList.add("hidden");
}
