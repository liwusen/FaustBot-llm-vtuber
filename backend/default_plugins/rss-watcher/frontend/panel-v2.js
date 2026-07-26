(function(){
  const api = window.pluginUI;
  if (!api) return;

  async function communicate(payload){
    return api.communicate('rss-watcher', payload || {});
  }

  async function fetchJson(path, options){
    const res = await fetch(api.backendBaseUrl + path, options || {});
    return res.json();
  }

  async function saveConfig(values){
    return fetchJson('/faust/admin/plugins/rss-watcher/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values: values, apply_runtime: true, no_initial_chat: true, reset_dialog: false })
    });
  }

  function feedTableRows(feeds){
    if (!feeds.length) return '<tr><td colspan="4">暂无订阅源</td></tr>';
    return feeds.map(function(feed){
      return '<tr>'
        + '<td>' + (feed.name || '未命名') + '</td>'
        + '<td>' + (feed.category || '未分类') + '</td>'
        + '<td>' + (feed.url || '-') + '</td>'
        + '<td><div class="toolbar compact"><button class="btn btn-ghost" data-feed-edit="' + feed.id + '">编辑</button><button class="btn btn-ghost" data-feed-id="' + feed.id + '">删除</button></div></td>'
        + '</tr>';
    }).join('');
  }

  function itemTableRows(items){
    if (!items.length) return '<tr><td colspan="4">暂无条目</td></tr>';
    return items.map(function(item){
      return '<tr>'
        + '<td>' + (item.title || '未命名条目') + '</td>'
        + '<td>' + (item.feed_name || 'RSS') + '</td>'
        + '<td>' + (item.link || '-') + '</td>'
        + '<td><button class="btn btn-ghost" data-save-id="' + item.id + '">' + (item.is_saved ? '已收藏' : '收藏') + '</button></td>'
        + '</tr>';
    }).join('');
  }

  function renderPage(container){
    container.innerHTML = `
<article class="card full-span">
  <h3 class="card-title">RSS Watcher</h3>
  <p class="card-help">添加和管理 RSS 订阅源，手动触发抓取，查看近期摘要。</p>

  <form id="rss-feed-form" class="toolbar rss-form">
    <input class="input" id="rss-feed-name" placeholder="名称" />
    <input class="input" id="rss-feed-url" placeholder="https://example.com/feed.xml" />
    <input class="input" id="rss-feed-category" placeholder="分类（如 tech、news）" />
    <button type="submit" class="btn btn-primary">添加</button>
    <button type="button" id="rss-fetch-now" class="btn btn-secondary">立即抓取</button>
  </form>
</article>

<article class="card full-span">
  <h3 class="card-title">推送配置</h3>

  <div class="rss-config-vertical">
    <label class="rss-config-item">
      <span class="rss-config-label">推送阈值</span>
      <span class="rss-config-desc">累计多少条新条目时触发播报。推荐 3–5 条。</span>
      <input class="input" id="rss-threshold" type="number" min="1" max="20" />
    </label>

    <label class="rss-config-item">
      <span class="rss-config-label">抓取间隔（分钟）</span>
      <span class="rss-config-desc">多久检查一次订阅源更新。建议 15–60 分钟。</span>
      <input class="input" id="rss-interval" type="number" min="5" max="360" />
    </label>

    <label class="rss-config-item">
      <span class="rss-config-label">静默开始</span>
      <span class="rss-config-desc">此时间之后不推送新消息，避免夜间打扰。</span>
      <input class="input" id="rss-quiet-start" placeholder="如 23:00" />
    </label>

    <label class="rss-config-item">
      <span class="rss-config-label">静默结束</span>
      <span class="rss-config-desc">静默时段结束时间。</span>
      <input class="input" id="rss-quiet-end" placeholder="如 08:00" />
    </label>

    <label class="rss-config-item">
      <span class="rss-config-label">分类过滤</span>
      <span class="rss-config-desc">只推送指定分类的条目。填写 "all" 不过滤。</span>
      <input class="input" id="rss-category-filter" placeholder="all / tech / news" />
    </label>

    <button type="button" id="rss-save-config" class="btn btn-primary" style="align-self:flex-start;margin-top:4px">保存配置</button>
  </div>
</article>

<article class="card full-span">
  <h3 class="card-title">订阅源</h3>
  <table class="simple-table"><thead><tr><th>名称</th><th>分类</th><th>地址</th><th>操作</th></tr></thead><tbody id="rss-feed-list"></tbody></table>
</article>

<article class="card full-span">
  <h3 class="card-title">摘要</h3>
  <pre id="rss-digest" class="rss-digest-v2">加载中...</pre>
</article>

<article class="card full-span">
  <h3 class="card-title">最近条目</h3>
  <table class="simple-table"><thead><tr><th>标题</th><th>来源</th><th>链接</th><th>操作</th></tr></thead><tbody id="rss-item-list"></tbody></table>
</article>`;

    async function refresh(){
      const results = await Promise.all([
        communicate({ action: 'get_feeds' }),
        communicate({ action: 'get_items', limit: 12 }),
        communicate({ action: 'get_digest' }),
        fetchJson('/faust/admin/plugins/rss-watcher/config')
      ]);
      const feeds = results[0].items || [];
      const items = results[1].items || [];
      const digest = results[2].summary || '暂无摘要';
      const configValues = (((results[3] || {}).config || {}).values) || {};
      const feedList = document.getElementById('rss-feed-list');
      const itemList = document.getElementById('rss-item-list');
      const digestEl = document.getElementById('rss-digest');
      if (digestEl) digestEl.textContent = digest;
      const threshold = document.getElementById('rss-threshold');
      const interval = document.getElementById('rss-interval');
      const quietStart = document.getElementById('rss-quiet-start');
      const quietEnd = document.getElementById('rss-quiet-end');
      const categoryFilter = document.getElementById('rss-category-filter');
      if (threshold) threshold.value = String(configValues.PUSH_THRESHOLD ?? 3);
      if (interval) interval.value = String(configValues.FETCH_INTERVAL_MIN ?? 15);
      if (quietStart) quietStart.value = String(configValues.QUIET_START ?? '23:00');
      if (quietEnd) quietEnd.value = String(configValues.QUIET_END ?? '08:00');
      if (categoryFilter) categoryFilter.value = String(configValues.CATEGORY_FILTER ?? 'all');
      const saveConfigBtn = document.getElementById('rss-save-config');
      if (saveConfigBtn) {
        saveConfigBtn.onclick = async function(){
          await saveConfig({
            PUSH_THRESHOLD: Number(threshold && threshold.value || 3),
            FETCH_INTERVAL_MIN: Number(interval && interval.value || 15),
            QUIET_START: quietStart && quietStart.value || '23:00',
            QUIET_END: quietEnd && quietEnd.value || '08:00',
            CATEGORY_FILTER: categoryFilter && categoryFilter.value || 'all'
          });
          refresh();
        };
      }
      if (feedList) {
        feedList.innerHTML = feedTableRows(feeds);
        Array.from(feedList.querySelectorAll('button[data-feed-edit]')).forEach(function(button){
          button.addEventListener('click', async function(){
            const feedId = button.getAttribute('data-feed-edit');
            const feed = feeds.find(function(item){ return String(item.id) === String(feedId); });
            if (!feed) return;
            const name = window.prompt('RSS 名称', feed.name || '');
            if (name === null) return;
            const url = window.prompt('RSS URL', feed.url || '');
            if (url === null) return;
            const category = window.prompt('RSS 分类', feed.category || '') || '';
            await communicate({ action: 'update_feed', feed_id: Number(feedId), name: name, url: url, category: category });
            refresh();
          });
        });
        Array.from(feedList.querySelectorAll('button[data-feed-id]')).forEach(function(button){
          button.addEventListener('click', async function(){
            await communicate({ action: 'delete_feed', feed_id: Number(button.getAttribute('data-feed-id')) });
            refresh();
          });
        });
      }
      if (itemList) {
        itemList.innerHTML = itemTableRows(items);
        Array.from(itemList.querySelectorAll('button[data-save-id]')).forEach(function(button){
          if (button.textContent === '已收藏') return;
          button.addEventListener('click', async function(){
            await communicate({ action: 'save_item', item_id: Number(button.getAttribute('data-save-id')) });
            refresh();
          });
        });
      }
    }

    const form = document.getElementById('rss-feed-form');
    if (form) {
      form.addEventListener('submit', async function(event){
        event.preventDefault();
        await communicate({
          action: 'create_feed',
          name: document.getElementById('rss-feed-name').value,
          url: document.getElementById('rss-feed-url').value,
          category: document.getElementById('rss-feed-category').value
        });
        form.reset();
        refresh();
      });
    }
    const fetchBtn = document.getElementById('rss-fetch-now');
    if (fetchBtn) {
      fetchBtn.addEventListener('click', async function(){
        await communicate({ action: 'fetch_now' });
        refresh();
      });
    }
    refresh().catch(function(){ container.innerHTML = '<article class="card full-span"><h3 class="card-title">RSS Watcher</h3><p class="card-help">RSS 插件状态读取失败</p></article>'; });
  }

  api.addPage({ id: 'rss-watcher', label: 'RSS 感知', desc: 'RSS 源、条目与推送设置', plugin: 'rss-watcher', render: renderPage });
  api.addCard('plugins', {
    title: 'RSS Watcher',
    priority: 16,
    plugin: 'rss-watcher',
    render: function(container){
      communicate({ action: 'get_digest' }).then(function(data){
        container.innerHTML = '<div class="plugin-mini-card"><p>摘要条目：' + (data.count || 0) + '</p><p class="plugin-mini-muted">可以在管理页新增订阅源，并手动触发抓取。</p></div>';
      }).catch(function(){ container.innerHTML = '<div class="plugin-mini-card">RSS 状态不可用</div>'; });
    }
  });
})();
