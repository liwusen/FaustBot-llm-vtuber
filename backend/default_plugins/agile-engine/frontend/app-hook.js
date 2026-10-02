// LimitedUI 前端钩子（app 窗口）。
// register_frontend() 的资源会同时注入 app 窗口与配置窗口，所以第一行必须自守卫。
(function () {
  const api = window.faustAppUI;
  if (!api || typeof api.registerCommandHandler !== 'function') return;

  const PLUGIN_ID = 'agile-engine';
  const BASE = (window.api && window.api.backendBaseUrl) || api.backendBaseUrl || 'http://127.0.0.1:13900';

  function post(payload) {
    if (window.api && typeof window.api.configRequest === 'function') {
      return window.api.configRequest('POST', '/faust/plugins/' + PLUGIN_ID + '/communicate', payload)
        .catch(function () { return null; });
    }
    return fetch(BASE + '/faust/plugins/' + PLUGIN_ID + '/communicate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(function (r) { return r.json(); }).catch(function () { return null; });
  }

  // 物理像素（后端全程用物理像素；Electron 的 screenX/innerWidth 是 DIP，需乘 devicePixelRatio）
  function physicalBounds() {
    const dpr = window.devicePixelRatio || 1;
    return {
      x: Math.round(window.screenX * dpr),
      y: Math.round(window.screenY * dpr),
      width: Math.round(window.innerWidth * dpr),
      height: Math.round(window.innerHeight * dpr),
    };
  }

  // ── 左下角非模态提示条（只在状态切换时更新 DOM）──
  let hintEl = null;
  let hintState = null;
  function renderHint(payload) {
    const state = String((payload && payload.state) || 'ok');
    if (state === hintState) return;
    hintState = state;
    if (!hintEl) {
      hintEl = document.createElement('div');
      hintEl.className = 'agile-ui-hint';
      document.body.appendChild(hintEl);
    }
    if (state === 'lost') {
      const title = String((payload && payload.title) || '').trim();
      const reason = String((payload && payload.reason) || '').trim();
      hintEl.textContent = 'UI 操作暂停' + (title ? '：' + title : '') + (reason ? ' · ' + reason : '');
      hintEl.style.display = 'block';
    } else {
      hintEl.textContent = '';
      hintEl.style.display = 'none';
    }
  }

  api.registerCommandHandler(async function (cmd, arg) {
    let payload = null;
    if (arg) {
      try { payload = JSON.parse(arg); } catch (e) { console.warn('[limited-ui] bad payload', cmd, arg); payload = null; }
    }
    switch (cmd) {
      case 'UI_MODEL_BLINK': {
        const hide = !!(payload && payload.hide);
        let ok = false;
        try {
          ok = api.setModelVisible(!hide) !== false;
        } catch (e) {
          console.warn('[limited-ui] setModelVisible failed', e);
        }
        post({ action: 'ui_blink_ack', feedback_id: (payload && payload.feedback_id) || '', ok: ok });
        return true;
      }
      case 'UI_CONTROL_HINT':
        renderHint(payload || {});
        return true;
      case 'UI_WINDOW_BOUNDS': {
        const req = (payload && payload.req_id) || '';
        const b = physicalBounds();
        post(Object.assign({ action: 'ui_window_bounds', req_id: req }, b));
        return true;
      }
      case 'UI_SESSION_STATE': {
        const active = !!(payload && payload.active);
        const req = (payload && payload.req_id) || '';
        if (!active) renderHint({ state: 'ok' });
        post({ action: 'ui_session_state_ack', req_id: req, active: active });
        return true;
      }
      case 'UI_CONTROL_STOP':
        post({ action: 'ui_session_stop' });
        return true;
      default:
        return false;
    }
  });
})();
