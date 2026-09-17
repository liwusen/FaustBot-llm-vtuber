(function () {
  const api = window.faustAppUI;
  if (!api) return;

  const WIDGET_ID = 'rss-banner';
  const BOTTOM_MARGIN = 18;   // 横幅距窗口底边
  const VIEWPORT_MARGIN = 8;  // 左右留白
  const SHOW_MS = 10000;      // 每条新条目显示时长
  const POLL_MS = 20000;      // 轮询间隔（后端 get_banner 是消费型队列，多条未读逐个弹）

  // 插件资源热重载时脚本会再次执行：先清掉上一份的节点与定时器，避免叠加横幅
  if (typeof window.__rssBannerTeardown === 'function') {
    try { window.__rssBannerTeardown(); } catch (e) { console.warn('[rss-watcher] teardown failed', e); }
  }

  const banner = document.createElement('div');
  banner.id = 'rssBanner';
  banner.className = 'rss-banner-v2';
  banner.innerHTML = '<span class="rss-banner-prefix">RSS</span><a class="rss-banner-link" href="#"></a>';
  document.body.appendChild(banner);

  let hideTimer = null;
  let pollTimer = null;

  function setBannerVisible(visible) {
    api.updateWidget(WIDGET_ID, { hidden: !visible });
  }

  // 底边对齐：uiWidget 通用布局是中心锚点（会有一半出屏），这里按自身高度贴住底边。
  // coord/offset 仍由布景台拖拽维护，见 registerWidget 的默认值。
  function layoutBanner(el, anchor, widget) {
    el.style.display = 'flex';
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const width = el.offsetWidth || 0;
    const height = el.offsetHeight || 0;
    const halfWidth = width / 2;
    const rawX = vw * widget.coord.x + widget.offset.x;
    const x = Math.min(Math.max(halfWidth + VIEWPORT_MARGIN, rawX), Math.max(halfWidth + VIEWPORT_MARGIN, vw - halfWidth - VIEWPORT_MARGIN));
    const y = vh * widget.coord.y + widget.offset.y - height / 2;
    el.style.left = Math.round(x) + 'px';
    el.style.top = Math.round(y) + 'px';
    el.style.transform = 'translate(-50%, -50%) scale(' + (widget.scale || 1) + ')';
  }

  api.registerWidget({
    id: WIDGET_ID,
    element: banner,
    bindingType: 'screen',
    coord: { x: 0.5, y: 1 },
    offset: { x: 0, y: -BOTTOM_MARGIN },
    scale: 1,
    hidden: true,
    // hidden 是运行时状态（有新条目显示、10s 后隐藏），不落盘；coord/offset/scale 可编辑并持久化
    transientHidden: true,
    schema: { bindingType: 'screen', coord: 'point', offset: 'point', scale: 'number' },
    onLayout: layoutBanner,
  });

  async function refresh() {
    try {
      const payload = await api.communicate('rss-watcher', { action: 'get_banner' });
      const item = payload && payload.item;
      if (!item) return;
      const link = banner.querySelector('.rss-banner-link');
      link.textContent = '新文章：《' + (item.title || '未命名条目') + '》';
      link.href = item.link || '#';
      setBannerVisible(true);
      clearTimeout(hideTimer);
      hideTimer = setTimeout(function () { setBannerVisible(false); }, SHOW_MS);
    } catch (error) {
      setBannerVisible(false);
    }
  }

  window.__rssBannerTeardown = function () {
    clearTimeout(hideTimer);
    clearInterval(pollTimer);
    hideTimer = null;
    pollTimer = null;
    banner.remove();
  };

  refresh();
  pollTimer = setInterval(refresh, POLL_MS);
})();
