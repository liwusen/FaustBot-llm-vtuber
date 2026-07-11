// 日志面板模块 — 自包含 WebSocket 日志查看器
// 用法: const logPanel = initLogPanel(); logPanel.init();

export function initLogPanel() {
  const LOG_WS_URL = (window.BACKEND_BASE || 'ws://127.0.0.1:13900') + '/faust/logger/ws';
  const logPanel = document.getElementById('logPanel');
  const logContent = document.getElementById('logContent');
  const logLevelFilter = document.getElementById('logLevelFilter');
  const logClearBtn = document.getElementById('logClearBtn');
  const logCloseBtn = document.getElementById('logCloseBtn');
  const openLogBtn = document.getElementById('openLogPanelBtn');
  let logWs = null;
  let logReconnectTimer = null;

  function connectLogWs() {
    if (logWs && (logWs.readyState === WebSocket.OPEN || logWs.readyState === WebSocket.CONNECTING)) return;
    try {
      logWs = new WebSocket(LOG_WS_URL);
      logWs.onmessage = (ev) => {
        try { addLogEntry(JSON.parse(ev.data)); } catch (e) {}
      };
      logWs.onclose = () => {
        logWs = null;
        clearTimeout(logReconnectTimer);
        logReconnectTimer = setTimeout(connectLogWs, 3000);
      };
      logWs.onerror = () => { if (logWs) logWs.close(); };
    } catch (e) {
      clearTimeout(logReconnectTimer);
      logReconnectTimer = setTimeout(connectLogWs, 5000);
    }
  }

  function addLogEntry(entry) {
    if (!logContent) return;
    const levelno = entry.levelno || 20;
    const filterLevel = parseInt((logLevelFilter && logLevelFilter.value) || '0', 10);
    if (filterLevel > 0 && levelno < filterLevel) return;

    const placeholder = logContent.querySelector('.log-placeholder');
    if (placeholder) placeholder.remove();

    const line = document.createElement('div');
    line.className = 'log-line LEVEL_' + (entry.level || 'INFO');
    line.textContent = '[' + (entry.timestamp || '') + '] [' + (entry.level || '') + '] ' + (entry.name || '') + ': ' + (entry.message || '');
    logContent.appendChild(line);
    logContent.scrollTop = logContent.scrollHeight;
    while (logContent.children.length > 500) logContent.removeChild(logContent.firstChild);
  }

  function togglePanel() {
    const isHidden = logPanel && logPanel.style.display === 'none';
    if (logPanel) {
      logPanel.style.display = isHidden ? 'flex' : 'none';
      try{ logPanel.style.pointerEvents = 'auto'; }catch(e){}
      try{ logPanel.style.zIndex = '99999'; }catch(e){}
    }
    if (logWs) { logWs.close(); logWs = null; }
    if (isHidden) connectLogWs();
  }

  function open() {
    if (logPanel) {
      logPanel.style.display = 'flex';
      try{ logPanel.style.pointerEvents = 'auto'; }catch(e){}
      try{ logPanel.style.zIndex = '99999'; }catch(e){}
    }
    connectLogWs();
  }

  function close() {
    if (logPanel) logPanel.style.display = 'none';
    if (logWs) { logWs.close(); logWs = null; }
  }

  function init() {
    if (openLogBtn) {
      openLogBtn.addEventListener('click', () => {
        const isHidden = logPanel && logPanel.style.display === 'none';
        if (logPanel) logPanel.style.display = isHidden ? 'flex' : 'none';
        if (logWs) { logWs.close(); logWs = null; }
        if (isHidden) connectLogWs();
      });
    }
    if (logCloseBtn) {
      logCloseBtn.addEventListener('click', close);
    }
    if (logClearBtn) {
      logClearBtn.addEventListener('click', () => {
        if (logContent) logContent.innerHTML = '<div class="log-placeholder">日志已清除</div>';
      });
    }
    if (logPanel && logPanel.style.display !== 'none') connectLogWs();

    // Listen for toggle-log-panel event from config window
    if (window.faust && typeof window.faust.onToggleLogPanel === 'function') {
      window.faust.onToggleLogPanel(togglePanel);
    }
  }

  return { init, open, close, toggle: togglePanel };
}
