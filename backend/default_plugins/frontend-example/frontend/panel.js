(function(){
  // Frontend Example Plugin — injected UI
  const api = window.pluginUI;
  if (!api) return;

  // Add a plugin page
  api.addPage({
    id: 'frontend-example',
    label: '前端示例',
    desc: '演示插件前端注入功能的示例页面',
    plugin: 'frontend-example',
    render: function(container) {
      container.innerHTML = '<div class="fe-container"><h3>Frontend Example Plugin</h3><p id="fe-status">加载中...</p></div>';
      fetch('/faust/plugins/frontend-example/hello')
        .then(r => r.json())
        .then(data => {
          document.getElementById('fe-status').textContent = JSON.stringify(data);
        })
        .catch(() => {
          document.getElementById('fe-status').textContent = '无法获取插件状态';
        });
    }
  });

  // Add a card to the plugins module
  api.addCard('plugins', {
    title: '前端示例',
    priority: 10,
    plugin: 'frontend-example',
    render: function(container) {
      container.innerHTML = '<div style="padding:8px 0"><p style="color:var(--accent);font-weight:600">✅ 前端注入成功</p><p style="font-size:12px;color:var(--muted)">此卡片由 frontend-example 插件通过 pluginUI.addCard() 注入</p></div>';
    }
  });
})();
