(function () {
  const api = window.faustAppUI;
  if (!api) return;
  const baseUrl = (window.faustAppUI && window.faustAppUI.backendBaseUrl) || 'http://127.0.0.1:13900';
  const banner = document.createElement('div');
  banner.className = 'rss-banner-v2 rss-banner-hidden';
  banner.innerHTML = '<span class="rss-banner-prefix">RSS</span><a class="rss-banner-link" href="#"></a>';
  document.body.appendChild(banner);
  let hideTimer = null;

  async function refresh() {
    try {
      const res = await fetch(baseUrl + '/faust/plugins/rss-watcher/banner');
      const payload = await res.json();
      const item = payload.item;
      if (!item) return;
      const link = banner.querySelector('.rss-banner-link');
      link.textContent = '新文章：《' + (item.title || '未命名条目') + '》';
      link.href = item.link || '#';
      banner.classList.remove('rss-banner-hidden');
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = setTimeout(function () { banner.classList.add('rss-banner-hidden'); }, 10000);
    } catch (error) {
      banner.classList.add('rss-banner-hidden');
    }
  }

  refresh();
  setInterval(refresh, 20000);
})();
