function buildTriggerUpdatePayload(source) {
  const base = {
    id: String(source.id || "").trim(),
    type: String(source.type || "").trim(),
    recall_description: String(source.recall_description || ""),
    run_background: Boolean(source.run_background),
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
