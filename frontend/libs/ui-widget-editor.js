export function initUiWidgetEditor({ manager, saveSettings, refreshLayout, onEditModeChange }) {
  let selectedId = '';
  let dragState = null;
  let scaleState = null;

  const overlay = document.createElement('div');
  overlay.className = 'ui-widget-editor-overlay';
  overlay.style.display = 'none';
  overlay.innerHTML = '<div class="ui-widget-selection-box"><span class="ui-widget-handle tl"></span><span class="ui-widget-handle tr"></span><span class="ui-widget-handle bl"></span><span class="ui-widget-handle br" data-scale-handle="1"></span></div>';
  document.body.appendChild(overlay);

  const propertyPanel = document.createElement('div');
  propertyPanel.className = 'ui-widget-property-panel floating-panel';
  propertyPanel.style.display = 'none';
  document.body.appendChild(propertyPanel);

  function persist() {
    if (typeof saveSettings === 'function') {
      Promise.resolve(saveSettings()).catch(() => {});
    }
  }

  function widgetElements() {
    return manager.listWidgets().filter((widget) => widget && widget.element);
  }

  function applyGhostState() {
    const editMode = manager.isEditMode();
    widgetElements().forEach((widget) => {
      const el = widget.element;
      if (!el) return;
      el.classList.toggle('ui-widget-editing-target', editMode);
      el.classList.toggle('ui-widget-hidden-preview', editMode && !!widget.hidden);
      // 进入编辑态时，让被隐藏的组件恢复显示以便选中/拖动（清除内联 display:none，交回 CSS 默认）
      if (editMode && widget.hidden) {
        el.style.display = '';
      }
    });
  }

  function updateSelectionBox() {
    if (!manager.isEditMode() || !selectedId) {
      overlay.style.display = 'none';
      return;
    }
    const widget = manager.getWidget(selectedId);
    const el = widget && widget.element;
    if (!el) {
      overlay.style.display = 'none';
      return;
    }
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height) {
      overlay.style.display = 'none';
      return;
    }
    overlay.style.display = 'block';
    overlay.style.left = `${rect.left - 6}px`;
    overlay.style.top = `${rect.top - 6}px`;
    overlay.style.width = `${rect.width + 12}px`;
    overlay.style.height = `${rect.height + 12}px`;
  }

  function selectWidget(id) {
    selectedId = String(id || '');
    updateSelectionBox();
  }

  function openPropertyPanel(id, clientX, clientY) {
    const widget = manager.getWidget(id);
    if (!widget) return;
    propertyPanel.style.display = 'flex';
    propertyPanel.style.position = 'fixed';
    propertyPanel.style.left = `${Math.max(12, clientX)}px`;
    propertyPanel.style.top = `${Math.max(12, clientY)}px`;
    propertyPanel.style.width = '280px';
    propertyPanel.style.flexDirection = 'column';
    propertyPanel.style.gap = '10px';
    propertyPanel.style.padding = '14px';
    propertyPanel.innerHTML = '';

    const title = document.createElement('div');
    title.className = 'card-title';
    title.textContent = `组件: ${widget.id}`;

    const hiddenRow = document.createElement('label');
    hiddenRow.className = 'switch-row';
    hiddenRow.innerHTML = '<span class="switch-text">隐藏</span>';
    const hiddenInput = document.createElement('input');
    hiddenInput.type = 'checkbox';
    hiddenInput.checked = !!widget.hidden;
    hiddenInput.addEventListener('change', () => {
      manager.updateWidget(widget.id, { hidden: hiddenInput.checked });
      if (typeof refreshLayout === 'function') refreshLayout();
      applyGhostState();
      updateSelectionBox();
      persist();
    });
    hiddenRow.append(hiddenInput);

    const makeNumberField = (label, value, onChange) => {
      const wrap = document.createElement('label');
      wrap.className = 'field-wrap';
      const text = document.createElement('span');
      text.className = 'card-key';
      text.textContent = label;
      const input = document.createElement('input');
      input.className = 'input';
      input.type = 'number';
      input.step = '0.01';
      input.value = String(value);
      input.addEventListener('change', () => onChange(Number(input.value)));
      wrap.append(text, input);
      return wrap;
    };

    const scaleField = makeNumberField('缩放', widget.scale || 1, (value) => {
      manager.updateWidget(widget.id, { scale: Math.max(0.2, value || 1) });
      if (typeof refreshLayout === 'function') refreshLayout();
      updateSelectionBox();
      persist();
    });
    const xField = makeNumberField(widget.bindingType === 'model' ? '相对 X' : '屏幕 X', widget.coord.x || 0, (value) => {
      manager.updateWidget(widget.id, { coord: { ...widget.coord, x: value } });
      if (typeof refreshLayout === 'function') refreshLayout();
      updateSelectionBox();
      persist();
    });
    const yField = makeNumberField(widget.bindingType === 'model' ? '相对 Y' : '屏幕 Y', widget.coord.y || 0, (value) => {
      manager.updateWidget(widget.id, { coord: { ...widget.coord, y: value } });
      if (typeof refreshLayout === 'function') refreshLayout();
      updateSelectionBox();
      persist();
    });

    const propFields = [];
    const propSchema = widget.schema && widget.schema.props ? widget.schema.props : {};
    Object.entries(propSchema).forEach(([propKey, propType]) => {
      if (propType === 'boolean') {
        const row = document.createElement('label');
        row.className = 'switch-row';
        row.innerHTML = `<span class="switch-text">${propKey}</span>`;
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = !!(widget.props && widget.props[propKey]);
        input.addEventListener('change', () => {
          manager.updateWidget(widget.id, { props: { ...(widget.props || {}), [propKey]: input.checked } });
          if (typeof refreshLayout === 'function') refreshLayout();
          persist();
        });
        row.append(input);
        propFields.push(row);
      } else {
        const field = makeNumberField(propKey, widget.props && widget.props[propKey] != null ? widget.props[propKey] : 0, (value) => {
          manager.updateWidget(widget.id, { props: { ...(widget.props || {}), [propKey]: value } });
          if (typeof refreshLayout === 'function') refreshLayout();
          persist();
        });
        propFields.push(field);
      }
    });

    const closeBtn = document.createElement('button');
    closeBtn.className = 'btn btn-ghost';
    closeBtn.textContent = '关闭';
    closeBtn.addEventListener('click', () => {
      propertyPanel.style.display = 'none';
    });

    propertyPanel.append(title, hiddenRow, scaleField, xField, yField, ...propFields, closeBtn);
  }

  function beginDrag(widget, event) {
    dragState = {
      id: widget.id,
      startX: event.clientX,
      startY: event.clientY,
      coord: { ...widget.coord },
    };
  }

  function beginScale(event) {
    if (!selectedId) return;
    const widget = manager.getWidget(selectedId);
    if (!widget) return;
    scaleState = {
      id: widget.id,
      startX: event.clientX,
      startY: event.clientY,
      scale: widget.scale || 1,
    };
    event.preventDefault();
    event.stopPropagation();
  }

  function widgetFromEvent(event) {
    if (!manager.isEditMode()) return null;
    const target = event.target;
    if (!(target instanceof Node)) return null;
    if (overlay.contains(target) || propertyPanel.contains(target)) return null;
    for (const widget of widgetElements()) {
      if (widget.element.contains(target)) return widget;
    }
    return null;
  }

  document.addEventListener('mousedown', (event) => {
    if (event.button !== 0) return;
    const widget = widgetFromEvent(event);
    if (!widget) return;
    event.preventDefault();
    event.stopPropagation();
    const current = manager.getWidget(widget.id);
    if (!current) return;
    selectWidget(widget.id);
    beginDrag(current, event);
  }, true);

  document.addEventListener('contextmenu', (event) => {
    const widget = widgetFromEvent(event);
    if (!widget) return;
    event.preventDefault();
    event.stopPropagation();
    selectWidget(widget.id);
    openPropertyPanel(widget.id, event.clientX, event.clientY);
  }, true);

  document.addEventListener('mousemove', (event) => {
    if (dragState) {
      const widget = manager.getWidget(dragState.id);
      if (!widget) return;
      const dx = event.clientX - dragState.startX;
      const dy = event.clientY - dragState.startY;
      const clamp01 = (v) => Math.min(1, Math.max(0, v));
      if (widget.bindingType === 'model') {
        const bounds = manager.getModelBounds();
        if (!bounds || !bounds.width || !bounds.height) return;
        manager.updateWidget(widget.id, {
          coord: {
            x: clamp01(dragState.coord.x + dx / bounds.width),
            y: clamp01(dragState.coord.y + dy / bounds.height),
          },
        });
      } else {
        manager.updateWidget(widget.id, {
          coord: {
            x: clamp01(dragState.coord.x + dx / Math.max(1, window.innerWidth)),
            y: clamp01(dragState.coord.y + dy / Math.max(1, window.innerHeight)),
          },
        });
      }
      if (typeof refreshLayout === 'function') refreshLayout();
      updateSelectionBox();
    }
    if (scaleState) {
      const widget = manager.getWidget(scaleState.id);
      if (!widget) return;
      const delta = Math.max(event.clientX - scaleState.startX, event.clientY - scaleState.startY);
      const nextScale = Math.max(0.2, scaleState.scale * (1 + delta / 180));
      manager.updateWidget(widget.id, { scale: nextScale });
      if (typeof refreshLayout === 'function') refreshLayout();
      updateSelectionBox();
    }
  });

  document.addEventListener('mouseup', () => {
    if (dragState || scaleState) persist();
    dragState = null;
    scaleState = null;
  });

  overlay.addEventListener('mousedown', (event) => {
    const handle = event.target && event.target.dataset && event.target.dataset.scaleHandle;
    if (!handle) return;
    beginScale(event);
  });

  function setEditMode(enabled) {
    manager.setEditMode(enabled);
    if (typeof onEditModeChange === 'function') onEditModeChange(manager.isEditMode());
    overlay.style.display = enabled ? 'block' : 'none';
    if (!enabled) {
      selectedId = '';
      propertyPanel.style.display = 'none';
    }
    applyGhostState();
    if (typeof refreshLayout === 'function') refreshLayout();
    updateSelectionBox();
  }

  document.addEventListener('keydown', (event) => {
    if (event.ctrlKey && event.shiftKey && (event.key === 'E' || event.key === 'e')) {
      event.preventDefault();
      setEditMode(!manager.isEditMode());
    } else if (event.key === 'Escape' && manager.isEditMode()) {
      setEditMode(false);
    }
  });

  return {
    toggle() {
      setEditMode(!manager.isEditMode());
    },
    setEditMode,
    isEditMode() {
      return manager.isEditMode();
    },
    refreshSelection: updateSelectionBox,
    refreshGhostState: applyGhostState,
  };
}