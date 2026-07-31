// Trigger editor modal (openTriggerEditorModal) — 现代风格表单

function openTriggerEditorModal(initialTrigger, onSubmit) {
  const source = initialTrigger || {
    id: "",
    type: "interval",
    interval_seconds: 60,
    target: "",
    eval_code: "",
    recall_description: "",
    lifespan: "",
    run_background: false,
  };

  const makeField = (labelText, control, helpText) => {
    const wrap = el("div", "trigger-field");
    wrap.append(el("label", "trigger-field-label", labelText));
    if (helpText) wrap.append(el("p", "card-help", helpText));
    wrap.append(control);
    return wrap;
  };

  const idInput = el("input", "input");
  idInput.placeholder = "例如 daily-report";
  idInput.value = String(source.id || "");
  if (initialTrigger && initialTrigger.id) idInput.disabled = true;

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

  const lifespanInput = el("input", "input");
  lifespanInput.type = "number";
  lifespanInput.placeholder = "存活秒数（可选，超时自动删除）";
  lifespanInput.value = source.lifespan === null || source.lifespan === undefined ? "" : String(source.lifespan);

  // run_background 开关
  const bgRow = el("div", "switch-row");
  const bgText = el("span", "switch-text", source.run_background ? "后台任务" : "前台任务");
  const bgLabel = el("label", "switch");
  const bgInput = document.createElement("input");
  bgInput.type = "checkbox";
  bgInput.checked = Boolean(source.run_background);
  const bgSlider = el("span", "switch-slider");
  bgInput.addEventListener("change", () => {
    bgText.textContent = bgInput.checked ? "后台任务" : "前台任务";
  });
  bgLabel.append(bgInput, bgSlider);
  bgRow.append(bgText, bgLabel);

  const dynamicWrap = el("div", "field-wrap");

  const intervalInput = el("input", "input");
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
      dynamicWrap.append(makeField("间隔秒数", intervalInput));
    } else if (t === "datetime") {
      dynamicWrap.append(makeField("触发时间", targetInput, "格式 YYYY-MM-DD HH:mm:ss"));
    } else {
      dynamicWrap.append(makeField("eval 表达式", evalArea, "返回真值时触发"));
    }
  };

  typeSelect.addEventListener("change", renderDynamic);
  renderDynamic();

  const submitBtn = makeButton("保存", async () => {
    const payload = {
      id: idInput.value.trim(),
      type: typeSelect.value,
      recall_description: recallInput.value.trim(),
      run_background: Boolean(bgInput.checked),
    };
    const lifespanRaw = lifespanInput.value.trim();
    if (lifespanRaw) payload.lifespan = Number(lifespanRaw);

    if (!payload.id) {
      showBanner("error", "触发器 ID 不能为空。");
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
  bar.append(submitBtn, makeButton("取消", closeModal, "btn btn-ghost"));

  const form = el("div", "trigger-form");
  form.append(
    makeField("触发器 ID", idInput),
    makeField("类型", typeSelect),
    makeField("描述", recallInput),
    makeField("有效期", lifespanInput),
    makeField("运行方式", bgRow, "后台任务的处理过程与结果不会推送给前端"),
    dynamicWrap,
  );

  openModal(source.id ? `编辑 Trigger: ${source.id}` : "新建 Trigger", [form, bar]);
}
