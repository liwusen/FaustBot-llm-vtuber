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

  function render(container){
    container.innerHTML = '<article class="card full-span"><h3 class="card-title">Desktop Mood</h3><div class="toolbar"><select id="desktop-mood-select"><option value="auto">auto</option><option value="rainy">rainy</option><option value="warm">warm</option><option value="dark">dark</option></select><button id="desktop-mood-save" class="btn btn-primary">保存 mood</button></div><p class="card-help">规则文件位于 ~/.faustbot/desktop-mood.rules.json</p></article><article class="card full-span"><h3 class="card-title">桌面配置</h3><div class="toolbar"><label>全局冷却 <input id="desktop-global-cooldown" type="number" min="0" /></label><label>天气城市 <input id="desktop-weather-city" type="text" /></label><label><input id="desktop-window-watch" type="checkbox" /> 窗口监控</label><label><input id="desktop-idle-watch" type="checkbox" /> 空闲检测</label><label><input id="desktop-holiday-watch" type="checkbox" /> 节日彩蛋</label><label><input id="desktop-smtc-watch" type="checkbox" /> 媒体监控</label><button id="desktop-config-save" class="btn btn-primary">保存配置</button></div></article><article class="card full-span"><h3 class="card-title">当前上下文</h3><pre id="desktop-context-pre" class="desktop-pre-v2">加载中...</pre></article><article class="card full-span"><h3 class="card-title">规则</h3><div id="desktop-rule-list" class="desktop-rules-v2"></div><button id="desktop-rule-save" class="btn btn-secondary">保存规则</button></article>';

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
      if (pre) pre.textContent = JSON.stringify(context, null, 2);
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
          return '<label class="desktop-rule-row"><input type="checkbox" data-rule-index="' + index + '" ' + (rule.enabled ? 'checked' : '') + ' /><span>' + rule.label + '</span><small>' + rule.kind + ' / cooldown ' + rule.cooldown_sec + 's</small></label>';
        }).join('') || '<p>暂无规则</p>';
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
        container.innerHTML = '<div class="plugin-mini-card"><p>空闲：' + (context.idle_seconds == null ? '未知' : context.idle_seconds + 's') + '</p><p class="plugin-mini-muted">窗口：' + (context.window_title || '未知') + '</p></div>';
      }).catch(function(){ container.innerHTML = '<div class="plugin-mini-card">桌面状态不可用</div>'; });
    }
  });
})();
