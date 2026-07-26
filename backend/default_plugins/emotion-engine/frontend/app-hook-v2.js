(function(){
  const api = window.faustAppUI;
  if (!api) return;
  const overlay = document.createElement('div');
  overlay.className = 'emotion-overlay-v2';
  overlay.innerHTML = '<div class="emotion-badge-v2 emotion-badge-hidden" title="情绪监控中"><span class="emotion-badge-icon">🙂</span></div>';
  document.body.appendChild(overlay);
  const badge = overlay.querySelector('.emotion-badge-v2');
  const icon = overlay.querySelector('.emotion-badge-icon');

  function updatePosition(){
    if (!badge || typeof api.getModelBounds !== 'function') return;
    const bounds = api.getModelBounds();
    if (!bounds || !Number.isFinite(bounds.left) || !Number.isFinite(bounds.top)) {
      badge.classList.add('emotion-badge-hidden');
      return;
    }
    const anchorX = bounds.left + Math.min(24, bounds.width * 0.08);
    const anchorY = bounds.top + Math.min(20, bounds.height * 0.1);
    badge.style.left = Math.round(anchorX) + 'px';
    badge.style.top = Math.round(anchorY) + 'px';
  }

  async function refresh(){
    try {
      const payload = await api.communicate('emotion-engine', { action: 'get_state' });
      const top = Array.isArray(payload.top_emotions) ? payload.top_emotions : [];
      const dominant = top[0] || { key: 'curiosity', label: '好奇', value: 0 };
      const iconMap = { joy: '😊', irritation: '😤', pride: '😏', curiosity: '🧐', sharpness: '😼', boredom: '😶' };
      icon.textContent = iconMap[dominant.key] || '🙂';
      badge.title = dominant.label + ' ' + Number(dominant.value || 0).toFixed(1);
      badge.classList.remove('emotion-badge-hidden');
      badge.setAttribute('data-mode', dominant.key || 'curiosity');
      updatePosition();
    } catch (error) {
      badge.classList.add('emotion-badge-hidden');
    }
  }

  function loop(){
    updatePosition();
    requestAnimationFrame(loop);
  }

  refresh();
  loop();
  setInterval(refresh, 15000);
})();
