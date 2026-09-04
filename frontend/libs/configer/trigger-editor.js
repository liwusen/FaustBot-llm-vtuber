// Trigger editor modal (openTriggerEditorModal) — 现代风格表单

function openTriggerEditorModal(initialTrigger, onSubmit) {
  const source = initialTrigger || {
    id: "",
    type: "interval",
    interval_seconds: 60,
    target: "",
    eval_code: "",
    remove_when: "",
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
  const TYPE_LABELS = { interval: "间隔触发（每隔固定秒数）", datetime: "定时触发（指定时间点）", "py-eval": "表达式触发（条件满足时）" };
  for (const t of ["interval", "datetime", "py-eval"]) {
    const opt = document.createElement("option");
    opt.value = t;
    opt.textContent = TYPE_LABELS[t] || t;
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

  const removeWhenInput = el("input", "input");
  removeWhenInput.placeholder = "例如 after 24 hours, at midnight, 或时条件满足";
  removeWhenInput.value = String(source.remove_when || "");

  // 优先级选择
  const prioritySelect = el("select", "input");
  [
    ["normal", "常规（默认）"],
    ["interrupt", "立即唤醒"],
    ["batched", "低优先级（合并）"],
  ].forEach(([value, label]) => {
    const opt = el("option", "", label);
    opt.value = value;
    prioritySelect.append(opt);
  });
  prioritySelect.value = String(source.priority || "normal");

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
      dynamicWrap.append(makeField("间隔秒数", intervalInput, "每隔多少秒触发一次，最小 1 秒。"));
    } else if (t === "datetime") {
      dynamicWrap.append(makeField("触发时间", targetInput, "在指定的单一时间点触发，格式 YYYY-MM-DD HH:mm:ss（24 小时制）。"));
    } else {
      dynamicWrap.append(makeField("eval 表达式", evalArea, "Python 表达式，由系统周期性求值，返回真值时触发；可访问运行时上下文变量。"));
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
      priority: prioritySelect.value,
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
    makeField("触发器 ID", idInput, initialTrigger && initialTrigger.id ? "触发器的唯一标识，创建后不可修改。" : "触发器的唯一标识，用于区分与管理不同触发器，创建后不可修改。"),
    makeField("类型", typeSelect, "决定触发方式：间隔、指定时间点或表达式条件。切换后下方参数会自动变化。"),
    makeField("描述", recallInput, "触发器的备注说明，触发时会作为提示回传给 Agent，便于其理解本次触发的意图。"),
    makeField("有效期", lifespanInput, "触发器的存活秒数，超时后自动删除；留空表示长期有效。"),
    makeField("删除时间", removeWhenInput, "自然语言，表示应该在什么时候删除它，例如 'after 24 hours' 或 'at midnight'"),
    makeField("优先级", prioritySelect, "立即唤醒=告警/紧急事件；常规=默认；低优先级=高频感知事件，消费时按 30 秒窗口合并为一次唤醒。"),
    makeField("运行方式", bgRow, "后台任务的处理过程与结果不会推送给前端界面，适合静默执行的定时作业。"),
    dynamicWrap,
  );

  openModal(source.id ? `编辑 Trigger: ${source.id}` : "新建 Trigger", [form, bar]);
}
