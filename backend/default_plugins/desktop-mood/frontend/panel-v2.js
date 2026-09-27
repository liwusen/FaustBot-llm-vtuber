(function(){
  const api = window.pluginUI;
  if (!api) return;

  const TIER_LABELS = { green: '绿色', yellow: '黄色', red: '红色' };
  const TIER_HINTS = { green: '本机元数据', yellow: '文本与联网', red: '屏幕内容' };
  const ATTENTION_LABELS = { focused: '心流', fragmented: '碎片', chaotic: '狂乱', neutral: '一般' };
  const LEVEL_LABELS = { idle: '离开', calm: '平静', active: '活跃', intense: '激烈' };

  async function communicate(payload){
    return api.communicate('desktop-mood', payload || {});
  }

  async function fetchJson(path, options){
    const res = await fetch(api.backendBaseUrl + path, options || {});
    return res.json();
  }

  function esc(value){
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function agoText(ts){
    if (!ts) return '尚未采集';
    const delta = Math.max(0, Math.floor(Date.now() / 1000) - Number(ts));
    if (delta < 60) return delta + ' 秒前采集';
    if (delta < 3600) return Math.floor(delta / 60) + ' 分钟前采集';
    return Math.floor(delta / 3600) + ' 小时前采集';
  }

  function durationText(seconds){
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    if (total < 60) return total + ' 秒';
    if (total < 3600) return Math.floor(total / 60) + ' 分钟';
    return Math.floor(total / 3600) + ' 小时 ' + Math.floor((total % 3600) / 60) + ' 分钟';
  }

  function tierBadge(tier, short){
    const label = short ? (TIER_LABELS[tier] || tier) : ((TIER_LABELS[tier] || tier) + (TIER_HINTS[tier] ? ' · ' + TIER_HINTS[tier] : ''));
    return '<span class="desktop-tier-badge desktop-tier-' + esc(tier) + '">' + esc(label) + '</span>';
  }

  function renderTierGroups(perception){
    const tiers = perception.tiers || [];
    const sources = perception.sources || [];
    return tiers.map(function(tier){
      const sourcesOfTier = sources.filter(function(source){ return source.tier === tier.id; });
      const sourceRows = sourcesOfTier.map(function(source){
        const disabled = tier.enabled ? '' : ' disabled';
        return '<label class="desktop-source-row' + (tier.enabled ? '' : ' is-muted') + '">'
          + '<input type="checkbox" data-source-toggle="' + esc(source.id) + '"' + (source.enabled ? ' checked' : '') + disabled + ' />'
          + '<span class="desktop-source-name">' + esc(source.label) + '</span>'
          + '<span class="card-help">' + esc(source.note) + '（每 ' + String(source.cadence || 10) + 's）</span>'
          + '</label>';
      }).join('') || '<p class="card-help">该分级下暂无感知源</p>';
      return '<div class="desktop-tier-group" data-tier="' + esc(tier.id) + '">'
        + '<label class="desktop-tier-head">'
        + '<input type="checkbox" data-tier-toggle="' + esc(tier.id) + '"' + (tier.enabled ? ' checked' : '') + ' />'
        + tierBadge(tier.id)
        + '</label>'
        + '<p class="card-help">' + esc(tier.note) + '</p>'
        + '<div class="desktop-source-list">' + sourceRows + '</div>'
        + '</div>';
    }).join('');
  }

  function renderCollectionTable(perception){
    const sources = perception.sources || [];
    if (!sources.length) return '<tr><td colspan="5">暂无感知源</td></tr>';
    return sources.map(function(source){
      const status = source.collecting
        ? '<span class="desktop-status desktop-status-on">采集中</span>'
        : (source.enabled
          ? '<span class="desktop-status desktop-status-off">分级已关</span>'
          : '<span class="desktop-status desktop-status-off">已关闭</span>');
      const note = source.status ? '<div class="desktop-field-row"><span class="desktop-field-label">不可用</span><span class="desktop-field-value">' + esc(source.status) + '</span></div>' : '';
      const values = (source.fields || []).map(function(field){
        const text = field.value == null ? '<span class="desktop-value-missing">未采集</span>' : esc(field.value);
        return '<div class="desktop-field-row"><span class="desktop-field-label">' + esc(field.label) + '</span><span class="desktop-field-value">' + text + '</span></div>';
      }).join('') || '<span class="desktop-value-missing">未采集</span>';
      return '<tr>'
        + '<td>' + esc(source.label) + '</td>'
        + '<td>' + tierBadge(source.tier, true) + '</td>'
        + '<td>' + status + '</td>'
        + '<td>' + String(source.cadence || 10) + 's</td>'
        + '<td>' + values + note + '</td>'
        + '</tr>';
    }).join('');
  }

  function renderTimeline(perception){
    const events = perception.recent_events || [];
    const rows = events.slice(-12).reverse().map(function(event){
      const at = new Date(Number(event.at || 0) * 1000);
      const time = isNaN(at.getTime()) ? '--:--' : at.toTimeString().slice(0, 5);
      return '<li><span class="desktop-field-label">' + esc(time) + '</span>' + esc(event.text) + '</li>';
    }).join('');
    const digest = perception.away_digest;
    let awayText = '用户没有离开过（或还没回来）';
    if (digest && digest.items && digest.items.length){
      awayText = '离开 ' + durationText(digest.away_seconds) + '：' + digest.items.map(esc).join('；');
    } else if (digest){
      awayText = '离开 ' + durationText(digest.away_seconds) + '，期间没有特别事件';
    }
    return '<p class="desktop-narrative">' + esc(perception.narrative || '暂无场景摘要') + '</p>'
      + '<p class="card-help">离开期间：' + awayText + '</p>'
      + '<ul class="desktop-event-list">' + (rows || '<li class="desktop-value-missing">暂无事件</li>') + '</ul>';
  }

  function renderRhythm(perception){
    const today = perception.rhythm_today || {};
    const cards = [
      ['清醒', (today.awake_minutes || 0) + ' 分钟', (today.awake_first ? today.awake_first + '~' + (today.awake_last || '') : '')],
      ['专注', (today.focus_minutes || 0) + ' 分钟', ATTENTION_LABELS[perception.attention] || ''],
      ['游戏', (today.game_minutes || 0) + ' 分钟', ''],
      ['离开', (today.leaves || 0) + ' 次', '切换 ' + (today.switches || 0) + ' 次'],
      ['活动强度', LEVEL_LABELS[perception.activity_level] || '未知', ''],
    ];
    return cards.map(function(card){
      return '<div class="desktop-metric"><span class="desktop-metric-label">' + esc(card[0]) + '</span>'
        + '<span class="desktop-metric-value">' + esc(card[1]) + '</span>'
        + '<span class="card-help">' + esc(card[2]) + '</span></div>';
    }).join('');
  }

  function renderDisturb(perception){
    const reasons = perception.disturb_reasons || [];
    const quietLeft = Math.max(0, Number(perception.quiet_until || 0) - Math.floor(Date.now() / 1000));
    return '<p class="card-help">免打扰状态：'
      + (perception.disturbed ? '<span class="desktop-status desktop-status-off">不建议打扰</span> ' + esc(reasons.join('、')) : '<span class="desktop-status desktop-status-on">可以打扰</span>')
      + '</p>'
      + '<p class="card-help">静音阀：' + (quietLeft > 0 ? '剩余 ' + durationText(quietLeft) : '未开启') + '</p>'
      + '<div class="toolbar">'
      + '<button class="btn btn-secondary" data-quiet="1800">静音 30 分钟</button>'
      + '<button class="btn btn-secondary" data-quiet="3600">静音 1 小时</button>'
      + '<button class="btn btn-secondary" data-quiet="0">解除静音</button>'
      + '</div>';
  }

  function renderQuality(perception){
    const rows = (perception.quality || []).map(function(item){
      const rate = item.fired ? Math.round((item.followed / item.fired) * 100) + '%' : '-';
      return '<tr>'
        + '<td><input type="checkbox" data-rule-id="' + esc(item.id) + '" ' + (item.enabled ? 'checked' : '') + ' /></td>'
        + '<td>' + esc(item.label) + '<div class="card-help">' + esc(item.summary) + '</div></td>'
        + '<td>' + esc(item.kind) + '</td>'
        + '<td>' + Math.round(item.effective_cooldown_sec / 60) + ' 分' + (item.effective_cooldown_sec !== item.cooldown_sec ? '（自适应）' : '') + '</td>'
        + '<td>' + item.fired + '</td>'
        + '<td>' + item.followed + '</td>'
        + '<td>' + item.ignored + '</td>'
        + '<td>' + rate + '</td>'
        + '</tr>';
    }).join('') || '<tr><td colspan="8">暂无规则</td></tr>';
    return '<table class="simple-table"><thead><tr><th>启用</th><th>规则</th><th>类型</th><th>冷却</th><th>触发</th><th>有回应</th><th>被无视</th><th>回应率</th></tr></thead><tbody>'
      + rows + '</tbody></table>'
      + '<div class="toolbar"><button id="desktop-rule-save" class="btn btn-secondary">保存规则开关</button>'
      + '<span class="card-help">回应率 = 触发后 30 秒内用户有键鼠活动的比例；连续被无视的规则冷却会自动拉长。</span></div>';
  }

  function render(container){
    container.innerHTML = ''
      + '<article class="card full-span"><h3 class="card-title">感知引擎 · 桌宠能看什么</h3>'
      + '<p class="card-help">三级隐私开关决定允许采集的范围；每个感知源还能单独关闭。被关闭的源不会写进 desktop-context.json，依赖它的规则不会触发。保存后下一个心跳周期（≤10 秒）生效。</p>'
      + '<div id="desktop-tier-list" class="desktop-tier-list">加载中...</div>'
      + '<div class="toolbar"><button id="desktop-perception-save" class="btn btn-primary">保存感知设置</button></div>'
      + '</article>'
      + '<article class="card full-span"><h3 class="card-title">场景与时间线</h3><div id="desktop-narrative-box">加载中...</div></article>'
      + '<article class="card full-span"><h3 class="card-title">今日节律</h3><div id="desktop-rhythm-box" class="desktop-metric-row">加载中...</div></article>'
      + '<article class="card full-span"><h3 class="card-title">数据采集</h3>'
      + '<div class="toolbar"><span id="desktop-perception-stamp" class="card-help">尚未采集</span>'
      + '<button id="desktop-perception-refresh" class="btn btn-secondary">立即采集</button></div>'
      + '<table class="simple-table"><thead><tr><th>来源</th><th>分级</th><th>状态</th><th>间隔</th><th>采集到的数据</th></tr></thead>'
      + '<tbody id="desktop-perception-rows"></tbody></table>'
      + '</article>'
      + '<article class="card full-span"><h3 class="card-title">免打扰与静音阀</h3><div id="desktop-disturb-box">加载中...</div></article>'
      + '<article class="card full-span"><h3 class="card-title">规则质量</h3><div id="desktop-quality-box">加载中...</div></article>'
      + '<article class="card full-span"><h3 class="card-title">其他配置</h3>'
      + '<div class="toolbar"><label>当前情绪 <select id="desktop-mood-select"><option value="auto">自动</option><option value="rainy">雨天</option><option value="warm">温暖</option><option value="dark">低沉</option></select></label>'
      + '<button id="desktop-mood-save" class="btn btn-secondary">保存情绪</button>'
      + '<label>全局冷却 <input id="desktop-global-cooldown" type="number" min="0" style="width:80px" /></label>'
      + '<label>天气城市 <input id="desktop-weather-city" type="text" style="width:100px" /></label>'
      + '<label>编码监视目录 <input id="desktop-code-dir" type="text" style="width:220px" placeholder="D:\\some\\git\\repo" /></label>'
      + '<button id="desktop-other-save" class="btn btn-primary">保存</button></div>'
      + '<p class="card-help">规则文件位于 ~/.faustbot/desktop-mood.rules.json（由 faustbot://plugins/desktop-mood/reload 提交写入）。</p></article>';

    let latest = { perception: null, state: {}, config: {} };
    let tierDirty = false;

    function bindPerceptionPanel(perception){
      const tierList = document.getElementById('desktop-tier-list');
      if (tierList) tierList.innerHTML = renderTierGroups(perception);
      Array.prototype.forEach.call(container.querySelectorAll('input[data-tier-toggle]'), function(toggle){
        toggle.onchange = function(){
          const group = toggle.closest('.desktop-tier-group');
          const sourceToggles = group ? group.querySelectorAll('input[data-source-toggle]') : [];
          Array.prototype.forEach.call(sourceToggles, function(item){ item.disabled = !toggle.checked; });
          if (group) group.classList.toggle('is-muted', !toggle.checked);
          tierDirty = true;
        };
      });
      Array.prototype.forEach.call(container.querySelectorAll('input[data-source-toggle]'), function(toggle){
        toggle.onchange = function(){ tierDirty = true; };
      });
    }

    async function refresh(){
      const results = await Promise.all([
        communicate({ action: 'get_perception' }),
        communicate({ action: 'get_state' }),
        fetchJson('/faust/admin/plugins/desktop-mood/config')
      ]);
      const perception = results[0].perception || {};
      const state = results[1].state || {};
      const configValues = (((results[2] || {}).config || {}).values) || {};
      latest = { perception: perception, state: state, config: configValues };

      if (!tierDirty) bindPerceptionPanel(perception);
      const rows = document.getElementById('desktop-perception-rows');
      if (rows) rows.innerHTML = renderCollectionTable(perception);
      const stamp = document.getElementById('desktop-perception-stamp');
      if (stamp) stamp.textContent = agoText(perception.updated_at);
      const narrative = document.getElementById('desktop-narrative-box');
      if (narrative) narrative.innerHTML = renderTimeline(perception);
      const rhythm = document.getElementById('desktop-rhythm-box');
      if (rhythm) rhythm.innerHTML = renderRhythm(perception);
      const disturb = document.getElementById('desktop-disturb-box');
      if (disturb) disturb.innerHTML = renderDisturb(perception);
      const quality = document.getElementById('desktop-quality-box');
      if (quality) quality.innerHTML = renderQuality(perception);

      const select = document.getElementById('desktop-mood-select');
      if (select) select.value = state.manual_mood || 'auto';
      const globalCooldown = document.getElementById('desktop-global-cooldown');
      if (globalCooldown) globalCooldown.value = String(configValues.GLOBAL_COOLDOWN_SEC != null ? configValues.GLOBAL_COOLDOWN_SEC : 180);
      const weatherCity = document.getElementById('desktop-weather-city');
      if (weatherCity) weatherCity.value = String(configValues.WEATHER_CITY != null ? configValues.WEATHER_CITY : 'auto');
      const codeDir = document.getElementById('desktop-code-dir');
      if (codeDir) codeDir.value = String(configValues.CODE_WATCH_DIR || '');

      Array.prototype.forEach.call(container.querySelectorAll('button[data-quiet]'), function(button){
        button.onclick = async function(){
          await communicate({ action: 'quiet', seconds: Number(button.getAttribute('data-quiet')) });
          refresh();
        };
      });
      const saveMood = document.getElementById('desktop-mood-save');
      if (saveMood) saveMood.onclick = async function(){
        await communicate({ action: 'set_mood', mood: document.getElementById('desktop-mood-select').value });
        refresh();
      };
      const saveRules = document.getElementById('desktop-rule-save');
      if (saveRules) saveRules.onclick = async function(){
        const items = (perception.quality || []).map(function(item){
          const box = container.querySelector('input[data-rule-id="' + item.id + '"]');
          return Object.assign({}, item, { enabled: !!(box && box.checked) });
        });
        await communicate({ action: 'set_rules', items: items });
        refresh();
      };
      const savePerception = document.getElementById('desktop-perception-save');
      if (savePerception) savePerception.onclick = async function(){
        await saveConfig(collectPerceptionValues());
        tierDirty = false;
        refresh();
      };
      const saveOther = document.getElementById('desktop-other-save');
      if (saveOther) saveOther.onclick = async function(){
        await saveConfig({
          GLOBAL_COOLDOWN_SEC: Number((document.getElementById('desktop-global-cooldown') || {}).value || 180),
          WEATHER_CITY: (document.getElementById('desktop-weather-city') || {}).value || 'auto',
          CODE_WATCH_DIR: (document.getElementById('desktop-code-dir') || {}).value || ''
        });
        refresh();
      };
      const refreshPerception = document.getElementById('desktop-perception-refresh');
      if (refreshPerception) refreshPerception.onclick = async function(){
        await fetchJson('/faust/admin/plugins/heartbeat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        refresh();
      };
    }

    function collectPerceptionValues(){
      const values = {
        GLOBAL_COOLDOWN_SEC: Number((document.getElementById('desktop-global-cooldown') || {}).value || 180),
        WEATHER_CITY: (document.getElementById('desktop-weather-city') || {}).value || 'auto',
        CODE_WATCH_DIR: (document.getElementById('desktop-code-dir') || {}).value || ''
      };
      Array.prototype.forEach.call(container.querySelectorAll('input[data-tier-toggle]'), function(toggle){
        values['ENABLE_TIER_' + toggle.getAttribute('data-tier-toggle').toUpperCase()] = !!toggle.checked;
      });
      Array.prototype.forEach.call(container.querySelectorAll('input[data-source-toggle]'), function(toggle){
        const id = toggle.getAttribute('data-source-toggle');
        const source = ((latest.perception || {}).sources || []).filter(function(item){ return item.id === id; })[0];
        if (source && source.key) values[source.key] = !!toggle.checked;
      });
      return values;
    }

    async function saveConfig(values){
      // apply_runtime=false：感知开关只改采集范围，没必要重建 agent 运行时（也不会重置对话）
      return fetchJson('/faust/admin/plugins/desktop-mood/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ values: values, apply_runtime: false, no_initial_chat: true, reset_dialog: false })
      });
    }

    refresh().catch(function(){
      container.innerHTML = '<article class="card full-span"><h3 class="card-title">感知引擎</h3><p class="card-help">桌面插件状态读取失败</p></article>';
    });
    const timer = setInterval(function(){
      if (!document.body.contains(container)) { clearInterval(timer); return; }
      refresh().catch(function(){});
    }, 5000);
  }

  api.addPage({ id: 'desktop-mood', label: '感知引擎', desc: '感知分级、场景时间线与规则质量', plugin: 'desktop-mood', render: render });
  api.addCard('plugins', {
    title: '感知引擎',
    priority: 17,
    plugin: 'desktop-mood',
    render: function(container){
      communicate({ action: 'get_perception' }).then(function(data){
        const perception = data.perception || {};
        const tiers = perception.tiers || [];
        const sources = perception.sources || [];
        const tierText = tiers.map(function(tier){ return TIER_LABELS[tier.id] + (tier.enabled ? '开' : '关'); }).join(' / ');
        const live = sources.filter(function(source){ return source.collecting; });
        container.innerHTML = '<div class="plugin-mini-card">'
          + '<p>' + esc(perception.narrative || '暂无场景摘要') + '</p>'
          + '<p class="plugin-mini-muted">感知分级 ' + esc(tierText || '未知') + '，采集中 ' + live.length + '/' + sources.length + ' 个来源'
          + '，' + esc(agoText(perception.updated_at))
          + (perception.disturbed ? '，免打扰中' : '') + '</p></div>';
      }).catch(function(){ container.innerHTML = '<div class="plugin-mini-card">桌面状态不可用</div>'; });
    }
  });
})();
