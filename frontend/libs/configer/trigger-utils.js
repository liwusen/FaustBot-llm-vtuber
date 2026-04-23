function buildTriggerUpdatePayload(source) {
  const base = {
    id: String(source.id || "").trim(),
    type: String(source.type || "").trim(),
    recall_description: String(source.recall_description || ""),
  };
  if (source.lifespan !== null && source.lifespan !== undefined && String(source.lifespan).trim() !== "") {
    base.lifespan = Number(source.lifespan);
  }
  if (base.type === "interval") {
    base.interval_seconds = Number(source.interval_seconds || 60);
  } else if (base.type === "datetime") {
    base.target = String(source.target || "").trim();
  } else if (base.type === "py-eval") {
    base.eval_code = String(source.eval_code || "");
  }
  return base;
}

function openTriggerEditorModal(initialTrigger, onSubmit) {
  const source = initialTrigger || {
    id: "",
    type: "interval",
    interval_seconds: 60,
    target: "",
    eval_code: "",
    recall_description: "",
    lifespan: "",
  };

  const idInput = el("input", "input");
  idInput.placeholder = "Trigger ID";
  idInput.value = String(source.id || "");

  const typeSelect = el("select", "select");
  for (const t of ["interval", "datetime", "py-eval"]) {
    const opt = document.createElement("option");
    opt.value = t;
    opt.textContent = t;
    if (String(source.type || "interval") === t) opt.selected = true;
    typeSelect.append(opt);
  }

  const recallInput = el("input", "input");
  recallInput.placeholder = "描述（可选）";
  recallInput.value = String(source.recall_description || "");

  const lifespanInput = el("input", "number");
  lifespanInput.type = "number";
  lifespanInput.placeholder = "lifespan 秒（可选）";
  lifespanInput.value = source.lifespan === null || source.lifespan === undefined ? "" : String(source.lifespan);

  const dynamicWrap = el("div", "field-wrap");

  const intervalInput = el("input", "number");
  intervalInput.type = "number";
  intervalInput.min = "1";
  intervalInput.value = String(source.interval_seconds || 60);

  const targetInput = el("input", "input");
  targetInput.placeholder = "YYYY-MM-DD HH:mm:ss";
  targetInput.value = String(source.target || "");

  const evalArea = el("textarea", "textarea code-area");
  evalArea.value = String(source.eval_code || "");

  const renderDynamic = () => {
    dynamicWrap.innerHTML = "";
    const t = typeSelect.value;
    if (t === "interval") {
      dynamicWrap.append(el("div", "card-help", "interval_seconds"), intervalInput);
    } else if (t === "datetime") {
      dynamicWrap.append(el("div", "card-help", "target"), targetInput);
    } else {
      dynamicWrap.append(el("div", "card-help", "eval_code"), evalArea);
    }
  };

  typeSelect.addEventListener("change", renderDynamic);
  renderDynamic();

  const submitBtn = makeButton("保存", async () => {
    const payload = {
      id: idInput.value.trim(),
      type: typeSelect.value,
      recall_description: recallInput.value.trim(),
    };
    const lifespanRaw = lifespanInput.value.trim();
    if (lifespanRaw) payload.lifespan = Number(lifespanRaw);

    if (!payload.id) {
      showBanner("error", "Trigger ID 不能为空。");
      return;
    }
    if (payload.type === "interval") {
      payload.interval_seconds = Number(intervalInput.value || "60");
    } else if (payload.type === "datetime") {
      payload.target = targetInput.value.trim();
    } else {
      payload.eval_code = evalArea.value;
    }

    await onSubmit(payload);
    closeModal();
  }, "btn btn-primary");

  const bar = el("div", "toolbar");
  bar.append(submitBtn, makeButton("关闭", closeModal));

  const body = [
    el("div", "card-help", "编辑 Trigger（已适配 Electron，无需 prompt）"),
    idInput,
    typeSelect,
    recallInput,
    lifespanInput,
    dynamicWrap,
    bar,
  ];
  openModal(source.id ? `编辑 Trigger: ${source.id}` : "新建 Trigger", body);
}
