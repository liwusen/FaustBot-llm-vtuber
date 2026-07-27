(function(){
  const api = window.faustAppUI;
  if (!api) return;
  const overlay = document.createElement('div');
  overlay.className = 'desktop-overlay-v2';
  overlay.innerHTML = '<div class="desktop-widget-v2"><span class="desktop-weather-v2">--</span><span class="desktop-idle-v2">活动中</span></div>';
  document.body.appendChild(overlay);
  const weatherEl = overlay.querySelector('.desktop-weather-v2');
  const idleEl = overlay.querySelector('.desktop-idle-v2');

  const hasWidgetApi = typeof api.registerWidget === 'function' && typeof api.getWidget === 'function';
  if (hasWidgetApi) {
    api.registerWidget({
      id: 'desktop-weather',
      element: overlay,
      bindingType: 'screen',
      coord: { x: 0.9, y: 0.05 },
      offset: { x: 0, y: 0 },
      scale: 1,
      hidden: false,
      schema: {
        bindingType: 'screen',
        coord: 'point',
        scale: 'number',
        hidden: 'boolean',
      },
    });
  } else {
    overlay.classList.add('desktop-overlay-static');
  }

  function updatePosition(){
    if (!hasWidgetApi) return;
    const widget = api.getWidget('desktop-weather');
    if (!widget) return;
    const editMode = typeof api.isWidgetEditMode === 'function' && api.isWidgetEditMode();
    if (widget.hidden && !editMode) {
      overlay.classList.add('desktop-overlay-hidden');
      return;
    }
    overlay.classList.remove('desktop-overlay-hidden');
    overlay.classList.toggle('ui-widget-hidden-preview', !!(widget.hidden && editMode));
    const coord = widget.coord || { x: 0, y: 0 };
    const offset = widget.offset || { x: 0, y: 0 };
    const scale = widget.scale || 1;
    overlay.style.left = Math.round(window.innerWidth * coord.x + offset.x) + 'px';
    overlay.style.top = Math.round(window.innerHeight * coord.y + offset.y) + 'px';
    overlay.style.transform = 'translate(-50%, -50%) scale(' + scale + ')';
  }

  async function refresh(){
    try {
      const payload = await api.communicate('desktop-mood', { action: 'get_context' });
      const context = payload.context || {};
      const weather = context.weather || {};
      weatherEl.textContent = weather.text ? (weather.text + ' ' + (weather.temperature_c || '') + 'C') : '天气未启用';
      const idle = Number(context.idle_seconds || 0);
      idleEl.textContent = idle >= 600 ? '已离开' : idle >= 60 ? '短暂离开' : '活动中';
      overlay.setAttribute('data-mood', context.manual_mood || 'auto');
    } catch (error) {
      idleEl.textContent = '环境离线';
    }
  }

  function loop(){
    updatePosition();
    requestAnimationFrame(loop);
  }

  refresh();
  if (hasWidgetApi) loop();
  setInterval(refresh, 15000);
})();
