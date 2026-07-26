(function(){
  const api = window.pluginUI;
  if (!api) return;

  async function communicate(payload){
    return api.communicate('desktop-mood', payload || {});
  }

  async function fetchJson(path, options){
    const res = await fetch(api.backendBaseUrl + path, options || {});
    return res.json();
  }

  function boolText(value){
    return value ? '是' : '否';
  }

  function renderContextTable(context){
    const rows = [
      ['当前情绪模式', context.manual_mood || 'auto'],
      ['空闲时长', context.idle_seconds == null ? '未知' : String(context.idle_seconds) + ' 秒'],
      ['活动窗口', context.window_title || '未知'],
      ['天气', context.weather && context.weather.text ? context.weather.text : '未启用'],
      ['温度', context.weather && context.weather.temperature_c != null ? String(context.weather.temperature_c) + ' C' : '未知'],
    ];
    return '<table class="simple-table simple-table-compact"><tbody>' + rows.map(function(row){
      return '<tr><td class="cell-label">' + row[0] + '</td><td class="cell-value">' + row[1] + '</td></tr>';
    }).join('') + '</tbody></table>';
  }

  function render(container){
    container.innerHTML = '<article class="card full-span"><h3 class="card-title">Desktop Mood</h3><div class="toolbar"><select id="desktop-mood-select"><option value="auto">自动</option><option value="rainy">雨天</option><option value="warm">温暖</option><option value="dark">低沉</option></select><button id="desktop-mood-save" class="btn btn-primary">保存当前情绪</button></div><p class="card-help">规则文件位于 ~/.faustbot/desktop-mood.rules.json</p></article><article class="card full-span"><h3 class="card-title">桌面配置</h3><div class="toolbar"><label>全局冷却 <input id="desktop-global-cooldown" type="number" min="0" /></label><label>天气城市 <input id="desktop-weather-city" type="text" /></label><label><input id="desktop-window-watch" type="checkbox" /> 监控前台窗口</label><label><input id="desktop-idle-watch" type="checkbox" /> 检测空闲</label><label><input id="desktop-holiday-watch" type="checkbox" /> 节日彩蛋</label><label><input id="desktop-smtc-watch" type="checkbox" /> 监控媒体播放</label><button id="desktop-config-save" class="btn btn-primary">保存配置</button></div></article><article class="card full-span"><h3 class="card-title">当前上下文</h3><div id="desktop-context-pre" class="desktop-pre-v2">加载中...</div></article><article class="card full-span"><h3 class="card-title">规则</h3><table class="simple-table"><thead><tr><th>启用</th><th>规则名称</th><th>类型</th><th>冷却时间</th></tr></thead><tbody id="desktop-rule-list"></tbody></table><button id="desktop-rule-save" class="btn btn-secondary">保存规则</button></article>';

    async function refresh(){
      const results = await Promise.all([
        communicate({ action: 'get_context' }),
        communicate({ action: 'get_rules' }),
        communicate({ action: 'get_state' }),
        fetchJson('/faust/admin/plugins/desktop-mood/config')
      ]);
      const context = results[0].context || {};
      const rules = results[1].items || [];
      const state = results[2].state || {};
      const configValues = (((results[3] || {}).config || {}).values) || {};
      const pre = document.getElementById('desktop-context-pre');
      if (pre) pre.innerHTML = renderContextTable(context);
      const select = document.getElementById('desktop-mood-select');
      if (select) select.value = state.manual_mood || 'auto';
      const globalCooldown = document.getElementById('desktop-global-cooldown');
      const weatherCity = document.getElementById('desktop-weather-city');
      const windowWatch = document.getElementById('desktop-window-watch');
      const idleWatch = document.getElementById('desktop-idle-watch');
      const holidayWatch = document.getElementById('desktop-holiday-watch');
      const smtcWatch = document.getElementById('desktop-smtc-watch');
      if (globalCooldown) globalCooldown.value = String(configValues.GLOBAL_COOLDOWN_SEC ?? 180);
      if (weatherCity) weatherCity.value = String(configValues.WEATHER_CITY ?? 'auto');
      if (windowWatch) windowWatch.checked = !!configValues.ENABLE_WINDOW_WATCH;
      if (idleWatch) idleWatch.checked = !!configValues.ENABLE_IDLE_WATCH;
      if (holidayWatch) holidayWatch.checked = !!configValues.ENABLE_HOLIDAY_EGG;
      if (smtcWatch) smtcWatch.checked = !!configValues.ENABLE_SMTC_WATCH;
      const ruleList = document.getElementById('desktop-rule-list');
      if (ruleList) {
        ruleList.innerHTML = rules.map(function(rule, index){
          return '<tr><td><input type="checkbox" data-rule-index="' + index + '" ' + (rule.enabled ? 'checked' : '') + ' /></td><td>' + rule.label + '</td><td>' + (rule.kind || '-') + '</td><td>' + String(rule.cooldown_sec || 0) + ' 秒</td></tr>';
        }).join('') || '<tr><td colspan="4">暂无规则</td></tr>';
      }
      const saveRules = document.getElementById('desktop-rule-save');
      if (saveRules) {
        saveRules.onclick = async function(){
          const nextRules = rules.map(function(rule, index){
            const checkbox = document.querySelector('input[data-rule-index="' + index + '"]');
            return Object.assign({}, rule, { enabled: !!(checkbox && checkbox.checked) });
          });
          await communicate({ action: 'set_rules', items: nextRules });
          refresh();
        };
      }
      const saveMood = document.getElementById('desktop-mood-save');
      if (saveMood) {
        saveMood.onclick = async function(){
          await communicate({ action: 'set_mood', mood: document.getElementById('desktop-mood-select').value });
          refresh();
        };
      }
      const saveConfig = document.getElementById('desktop-config-save');
      if (saveConfig) {
        saveConfig.onclick = async function(){
          await fetchJson('/faust/admin/plugins/desktop-mood/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              values: {
                GLOBAL_COOLDOWN_SEC: Number(globalCooldown && globalCooldown.value || 180),
                WEATHER_CITY: weatherCity && weatherCity.value || 'auto',
                ENABLE_WINDOW_WATCH: !!(windowWatch && windowWatch.checked),
                ENABLE_IDLE_WATCH: !!(idleWatch && idleWatch.checked),
                ENABLE_HOLIDAY_EGG: !!(holidayWatch && holidayWatch.checked),
                ENABLE_SMTC_WATCH: !!(smtcWatch && smtcWatch.checked),
              },
              apply_runtime: true,
              no_initial_chat: true,
              reset_dialog: false
            })
          });
          refresh();
        };
      }
    }

    refresh().catch(function(){ container.innerHTML = '<article class="card full-span"><h3 class="card-title">Desktop Mood</h3><p class="card-help">桌面插件状态读取失败</p></article>'; });
  }

  api.addPage({ id: 'desktop-mood', label: '情绪化桌面', desc: '桌面采集、规则与 mood 控制', plugin: 'desktop-mood', render: render });
  api.addCard('plugins', {
    title: 'Desktop Mood',
    priority: 17,
    plugin: 'desktop-mood',
    render: function(container){
      communicate({ action: 'get_context' }).then(function(data){
        const context = data.context || {};
        container.innerHTML = '<div class="plugin-mini-card"><p>当前窗口：' + (context.window_title || '未知') + '</p><p class="plugin-mini-muted">空闲 ' + (context.idle_seconds == null ? '未知' : context.idle_seconds + ' 秒') + '，窗口监控 ' + boolText(!!context.window_title) + '</p></div>';
      }).catch(function(){ container.innerHTML = '<div class="plugin-mini-card">桌面状态不可用</div>'; });
    }
  });
})();
