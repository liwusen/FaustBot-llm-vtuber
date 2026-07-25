(function(){
  const api = window.faustAppUI;
  if (!api) return;
  const overlay = document.createElement('div');
  overlay.className = 'desktop-overlay-v2';
  overlay.innerHTML = '<div class="desktop-widget-v2"><span class="desktop-weather-v2">--</span><span class="desktop-idle-v2">活动中</span></div>';
  document.body.appendChild(overlay);
  const weatherEl = overlay.querySelector('.desktop-weather-v2');
  const idleEl = overlay.querySelector('.desktop-idle-v2');

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

  refresh();
  setInterval(refresh, 15000);
})();
