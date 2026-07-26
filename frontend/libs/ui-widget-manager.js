export function createUiWidgetManager({ getModelBounds, onWidgetChange } = {}) {
  const widgets = new Map();
  let editMode = false;

  function normalizeNumber(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function registerWidget(spec) {
    if (!spec || !spec.id) throw new Error('widget id is required');
    const id = String(spec.id);
    const prev = widgets.get(id) || {};
    const next = {
      id,
      element: spec.element || prev.element || null,
      bindingType: spec.bindingType || prev.bindingType || 'screen',
      coord: {
        x: normalizeNumber(spec.coord && spec.coord.x, prev.coord && prev.coord.x),
        y: normalizeNumber(spec.coord && spec.coord.y, prev.coord && prev.coord.y),
      },
      offset: {
        x: normalizeNumber(spec.offset && spec.offset.x, prev.offset && prev.offset.x),
        y: normalizeNumber(spec.offset && spec.offset.y, prev.offset && prev.offset.y),
      },
      scale: Math.max(0.2, normalizeNumber(spec.scale, prev.scale || 1)),
      hidden: spec.hidden === undefined ? !!prev.hidden : !!spec.hidden,
      schema: spec.schema || prev.schema || {},
      props: { ...(prev.props || {}), ...(spec.props || {}) },
    };
    widgets.set(id, next);
    if (typeof onWidgetChange === 'function') onWidgetChange(next, { reason: 'register' });
    return next;
  }

  function updateWidget(id, patch = {}) {
    const current = widgets.get(String(id));
    if (!current) throw new Error(`widget not found: ${id}`);
    const next = registerWidget({ ...current, ...patch, id: current.id });
    if (typeof onWidgetChange === 'function') onWidgetChange(next, { reason: 'update' });
    return next;
  }

  function getWidget(id) {
    const widget = widgets.get(String(id));
    if (!widget) return null;
    return {
      ...widget,
      coord: { ...widget.coord },
      offset: { ...widget.offset },
      schema: { ...(widget.schema || {}) },
      props: { ...(widget.props || {}) },
    };
  }

  function listWidgets() {
    return Array.from(widgets.values()).map((widget) => getWidget(widget.id));
  }

  function getWidgetAnchor(id) {
    const widget = widgets.get(String(id));
    if (!widget) return null;
    if (widget.bindingType === 'model') {
      const bounds = typeof getModelBounds === 'function' ? getModelBounds() : null;
      if (!bounds) return null;
      return {
        x: bounds.left + bounds.width * widget.coord.x + widget.offset.x,
        y: bounds.top + bounds.height * widget.coord.y + widget.offset.y,
        scale: widget.scale,
      };
    }
    return {
      x: widget.coord.x + widget.offset.x,
      y: widget.coord.y + widget.offset.y,
      scale: widget.scale,
    };
  }

  function setEditMode(enabled) {
    editMode = !!enabled;
    return editMode;
  }

  function isEditMode() {
    return editMode;
  }

  function readModelBounds() {
    return typeof getModelBounds === 'function' ? getModelBounds() : null;
  }

  return {
    registerWidget,
    updateWidget,
    getWidget,
    listWidgets,
    getWidgetAnchor,
    setEditMode,
    isEditMode,
    getModelBounds: readModelBounds,
  };
}