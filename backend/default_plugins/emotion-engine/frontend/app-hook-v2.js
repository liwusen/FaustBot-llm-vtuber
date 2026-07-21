(function(){
  const api = window.faustAppUI;
  if (!api) return;
  const baseUrl = (window.faustAppUI && window.faustAppUI.backendBaseUrl) || 'http://127.0.0.1:13900';
  const overlay = document.createElement('div');
  overlay.className = 'emotion-overlay-v2';
  overlay.innerHTML = '<div class="emotion-badge-v2 emotion-badge-hidden"><span class="emotion-badge-icon">...</span><span class="emotion-badge-text">情绪监控中</span></div>';
  document.body.appendChild(overlay);
  const badge = overlay.querySelector('.emotion-badge-v2');
  const icon = overlay.querySelector('.emotion-badge-icon');
  const text = overlay.querySelector('.emotion-badge-text');

  async function refresh(){
    try {
      const res = await fetch(baseUrl + '/faust/plugins/emotion-engine/state');
      const payload = await res.json();
      const top = Array.isArray(payload.top_emotions) ? payload.top_emotions : [];
      const dominant = top[0] || { key: 'curiosity', label: '好奇', value: 0 };
      const iconMap = { joy: 'blush', irritation: 'anger', pride: 'smirk', curiosity: 'spark', sharpness: 'fang', boredom: 'zzz' };
      icon.textContent = iconMap[dominant.key] || 'spark';
      text.textContent = dominant.label + ' ' + Number(dominant.value || 0).toFixed(1);
      badge.classList.remove('emotion-badge-hidden');
      badge.setAttribute('data-mode', dominant.key || 'curiosity');
    } catch (error) {
      badge.classList.add('emotion-badge-hidden');
    }
  }

  refresh();
  setInterval(refresh, 15000);
})();
