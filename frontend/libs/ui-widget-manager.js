const widgetSizes = new Map();

function readWidgetSize(el) {
  if (!el) return null;
  const width = el.offsetWidth;
  const height = el.offsetHeight;
  return { width: width > 0 ? width : 0, height: height > 0 ? height : 0 };
}

export function createUiWidgetManager({ getModelBounds, onWidgetChange, onLayoutTick } = {}) {
  const widgets = new Map();
  let editMode = false;
  let layoutRafId = null;

  function getWidgetSize(id, fallback = null) {
    const widget = widgets.get(String(id));
    if (!widget || !widget.element) return fallback;
    if (!widgetSizes.has(String(id))) {
      const size = readWidgetSize(widget.element);
      // 修订（Main 确认）：隐藏元素（display:none）读到的 {0,0} 不缓存，直接返回
      // fallback；元素可见后下次读取自然缓存真实尺寸，避免 ASR 气泡等组件永久命中 fallback。
      if (size && size.width === 0 && size.height === 0) return fallback;
      widgetSizes.set(String(id), size);
    }
    return widgetSizes.get(String(id));
  }

  function invalidateWidgetSize(id) {
    widgetSizes.delete(String(id));
  }

  function invalidateAllWidgetSizes() {
    widgetSizes.clear();
  }

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
      // managed=true 时由管理器统一处理显隐/定位/拖动等通用逻辑，默认开启
      managed: spec.managed === undefined ? (prev.managed === undefined ? true : prev.managed) : !!spec.managed,
      // 可选钩子：特殊组件在通用显隐判定后自定义定位（VRM 分支/缩放/平滑等）
      onLayout: spec.onLayout !== undefined ? spec.onLayout : (prev.onLayout || null),
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

  function removeWidget(id) {
    const current = widgets.get(String(id));
    if (!current) return false;
    widgets.delete(String(id));
    if (typeof onWidgetChange === 'function') onWidgetChange(current, { reason: 'remove' });
    return true;
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
    const clampX = (v) => Math.min(Math.max(0, window.innerWidth - 16), Math.max(0, v));
    const clampY = (v) => Math.min(Math.max(0, window.innerHeight - 16), Math.max(0, v));
    if (widget.bindingType === 'model') {
      const bounds = typeof getModelBounds === 'function' ? getModelBounds() : null;
      if (!bounds) return null;
      return {
        x: clampX(bounds.left + bounds.width * widget.coord.x + widget.offset.x),
        y: clampY(bounds.top + bounds.height * widget.coord.y + widget.offset.y),
        scale: widget.scale,
      };
    }
    return {
      x: clampX(window.innerWidth * widget.coord.x + widget.offset.x),
      y: clampY(window.innerHeight * widget.coord.y + widget.offset.y),
      scale: widget.scale,
    };
  }

  function setEditMode(enabled) {
    editMode = !!enabled;
    return editMode;
  }

  // 通用布局：对单个 managed 组件完成显隐判定 + 定位。
  // - hidden && 非编辑态 → display:none 并返回
  // - 否则切换编辑预览类；若提供 onLayout 钩子则委托其自定义定位，
  //   否则套用统一定位（getWidgetAnchor + translate(-50%,-50%) scale）。
  function applyWidgetLayout(widget) {
    if (!widget || widget.managed === false) return;
    const el = widget.element;
    if (!el) return;
    if (widget.hidden && !editMode) {
      el.style.display = 'none';
      return;
    }
    el.classList.toggle('ui-widget-hidden-preview', !!(widget.hidden && editMode));
    const anchor = getWidgetAnchor(widget.id);
    if (typeof widget.onLayout === 'function') {
      widget.onLayout(el, anchor, widget, { editMode });
      return;
    }
    if (!anchor) return;
    el.style.display = '';
    // 直接写入锚点（与改造前一致）：getWidgetAnchor 已做 [0, vw-16] 钳制，
    // 用户自定义组件允许贴边半出屏（桌面宠物惯例），不做中心 clamp
    el.style.left = Math.round(anchor.x) + 'px';
    el.style.top = Math.round(anchor.y) + 'px';
    el.style.transform = 'translate(-50%, -50%) scale(' + (anchor.scale || 1) + ')';
  }

  function applyLayout(id) {
    if (id !== undefined && id !== null) {
      const widget = widgets.get(String(id));
      if (widget) applyWidgetLayout(widget);
      return;
    }
    widgets.forEach(applyWidgetLayout);
  }

  function startLayoutLoop() {
    if (layoutRafId !== null) return;
    const tick = () => {
      if (typeof onLayoutTick === 'function') {
        const shouldRedraw = onLayoutTick(); // 返回 true 才全量 applyLayout
        if (shouldRedraw) applyLayout();
      } else {
        applyLayout();
      }
      layoutRafId = requestAnimationFrame(tick);
    };
    layoutRafId = requestAnimationFrame(tick);
  }

  function stopLayoutLoop() {
    if (layoutRafId === null) return;
    cancelAnimationFrame(layoutRafId);
    layoutRafId = null;
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
    removeWidget,
    getWidget,
    listWidgets,
    getWidgetAnchor,
    getWidgetSize,
    invalidateWidgetSize,
    invalidateAllWidgetSizes,
    applyLayout,
    startLayoutLoop,
    stopLayoutLoop,
    setEditMode,
    isEditMode,
    getModelBounds: readModelBounds,
  };
}

// 纯函数：把锚点坐标 clamp 到视口内（考虑元素宽高与边距；元素超宽时退化到 min 边界）
export function clampToViewport(left, top, width, height, vw, vh, margin = 8) {
  const minLeft = width * 0.5 + margin;
  const maxLeft = Math.max(minLeft, vw - width * 0.5 - margin);
  const minTop = height * 0.5 + margin;
  const maxTop = Math.max(minTop, vh - height * 0.5 - margin);
  return {
    left: Math.max(minLeft, Math.min(maxLeft, left)),
    top: Math.max(minTop, Math.min(maxTop, top)),
  };
}