(function(){
  const api = window.faustAppUI;
  if (!api) return;
  const badge = document.createElement('div');
  badge.className = 'emotion-badge-v2 emotion-badge-hidden';
  badge.title = '情绪监控中';
  badge.innerHTML = '<span class="emotion-badge-icon">🙂</span>';
  document.body.appendChild(badge);
  const icon = badge.querySelector('.emotion-badge-icon');

  if (typeof api.registerWidget === 'function') {
    api.registerWidget({
      id: 'emotion-badge',
      element: badge,
      bindingType: 'model',
      coord: { x: 0.08, y: 0.1 },
      offset: { x: 0, y: 0 },
      scale: 1,
      hidden: false,
      schema: {
        bindingType: 'model',
        coord: 'point',
        scale: 'number',
        hidden: 'boolean',
        props: { dynamicBackground: 'boolean' },
      },
      props: { dynamicBackground: true },
    });
  }

  function updatePosition(){
    if (!badge || typeof api.getWidget !== 'function' || typeof api.getModelBounds !== 'function') return;
    const widget = api.getWidget('emotion-badge');
    const bounds = api.getModelBounds();
    const editMode = typeof api.isWidgetEditMode === 'function' && api.isWidgetEditMode();
    if (!bounds || !Number.isFinite(bounds.left) || !Number.isFinite(bounds.top)) {
      badge.classList.add('emotion-badge-hidden');
      return;
    }
    if (widget && widget.hidden && !editMode) {
      badge.classList.add('emotion-badge-hidden');
      return;
    }
    if (editMode || loaded) badge.classList.remove('emotion-badge-hidden');
    const coord = widget && widget.coord ? widget.coord : { x: 0.08, y: 0.1 };
    const offset = widget && widget.offset ? widget.offset : { x: 0, y: 0 };
    const scale = widget && widget.scale ? widget.scale : 1;
    badge.classList.toggle('ui-widget-hidden-preview', !!(widget && widget.hidden && editMode));
    const anchorX = bounds.left + bounds.width * coord.x + offset.x;
    const anchorY = bounds.top + bounds.height * coord.y + offset.y;
    badge.style.left = Math.round(anchorX) + 'px';
    badge.style.top = Math.round(anchorY) + 'px';
    badge.style.transform = 'translate(-50%, -50%) scale(' + scale + ')';
  }

  let loaded = false;

  async function refresh(){
    try {
      const payload = await api.communicate('emotion-engine', { action: 'get_state' });
      const top = Array.isArray(payload.top_emotions) ? payload.top_emotions : [];
      const dominant = top[0] || { key: 'curiosity', label: '好奇', value: 0 };
      const iconMap = { joy: '😊', irritation: '😤', pride: '😏', curiosity: '🧐', sharpness: '😼', boredom: '😶' };
      const widget = typeof api.getWidget === 'function' ? api.getWidget('emotion-badge') : null;
      const dynamicBackground = !widget || !widget.props ? true : widget.props.dynamicBackground !== false;
      icon.textContent = iconMap[dominant.key] || '🙂';
      badge.title = dominant.label + ' ' + Number(dominant.value || 0).toFixed(1);
      badge.classList.remove('emotion-badge-hidden');
      badge.setAttribute('data-mode', dynamicBackground ? (dominant.key || 'curiosity') : 'static');
      loaded = true;
      updatePosition();
    } catch (error) {
      if (!loaded) badge.classList.add('emotion-badge-hidden');
    }
  }

  function loop(){
    updatePosition();
    requestAnimationFrame(loop);
  }

  async function bootstrap(){
    while (!loaded) {
      await refresh();
      if (loaded) break;
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }

  bootstrap();
  loop();
  setInterval(refresh, 15000);
})();
