(function(){
  const api = window.faustAppUI;
  if (!api) return;
  const overlay = document.createElement('div');
  overlay.className = 'emotion-overlay-v2';
  overlay.innerHTML = '<div class="emotion-badge-v2 emotion-badge-hidden" title="情绪监控中"><span class="emotion-badge-icon">🙂</span></div>';
  document.body.appendChild(overlay);
  const badge = overlay.querySelector('.emotion-badge-v2');
  const icon = overlay.querySelector('.emotion-badge-icon');

  if (typeof api.registerWidget === 'function') {
    api.registerWidget({
      id: 'emotion-badge',
      element: badge,
      bindingType: 'model',
      coord: { x: 0.08, y: 0.1 },
      offset: { x: 0, y: 0 },
      scale: 1,
      hidden: false,
      managed: true,
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
    } catch (error) {
      if (!loaded) badge.classList.add('emotion-badge-hidden');
    }
  }

  async function bootstrap(){
    while (!loaded) {
      await refresh();
      if (loaded) break;
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }

  bootstrap();
  setInterval(refresh, 15000);
})();
