// Nimble 窗口系统 — 以小组件形式注册的浮动弹窗，console 双向通信
// 用法: const nimble = initNimbleWindows({ messageEndpoint, closeEndpoint, widgetManager, saveSettings, getPersistedWidgetSettings });

export function initNimbleWindows({ messageEndpoint, closeEndpoint, widgetManager, saveSettings, getPersistedWidgetSettings }) {
  const nimbleWindows = new Map(); // callback_id -> { shell, body, header, api, messageHandler, fullscreen }
  let nimbleDragState = null;

  const DEFAULT_COORD = { x: 0.7, y: 0.5 };

  function widgetId(callbackId) {
    return `nimble::${callbackId}`;
  }

  function ensureHost() {
    let host = document.getElementById('nimble-host');
    if (host) return host;
    host = document.createElement('div');
    host.id = 'nimble-host';
    host.style.position = 'fixed';
    host.style.left = '0';
    host.style.top = '0';
    host.style.width = '0';
    host.style.height = '0';
    host.style.zIndex = '1600';
    host.style.pointerEvents = 'none';
    document.body.appendChild(host);
    return host;
  }

  function persist() {
    if (typeof saveSettings === 'function') {
      Promise.resolve(saveSettings()).catch(() => {});
    }
  }

  function layoutWindow(callbackId) {
    const win = nimbleWindows.get(callbackId);
    if (!win) return;
    if (win.fullscreen) return;
    const widget = widgetManager.getWidget(widgetId(callbackId));
    if (!widget) return;
    const shell = win.shell;
    if (widget.hidden) {
      shell.style.display = 'none';
      return;
    }
    shell.style.display = '';
    const anchor = widgetManager.getWidgetAnchor(widgetId(callbackId));
    if (!anchor) return;
    shell.style.left = `${anchor.x}px`;
    shell.style.top = `${anchor.y}px`;
    shell.style.transformOrigin = 'top left';
    shell.style.transform = anchor.scale && anchor.scale !== 1 ? `scale(${anchor.scale})` : '';
  }

  function layoutWindows() {
    for (const callbackId of nimbleWindows.keys()) layoutWindow(callbackId);
  }

  // 非编辑模式下的标题栏拖拽：直接更新 widget coord
  document.addEventListener('mousemove', (e) => {
    if (!nimbleDragState) return;
    const { callbackId, startX, startY, coord } = nimbleDragState;
    const clamp01 = (v) => Math.min(1, Math.max(0, v));
    try {
      widgetManager.updateWidget(widgetId(callbackId), {
        coord: {
          x: clamp01(coord.x + (e.clientX - startX) / Math.max(1, window.innerWidth)),
          y: clamp01(coord.y + (e.clientY - startY) / Math.max(1, window.innerHeight)),
        },
      });
    } catch (_e) { nimbleDragState = null; return; }
    layoutWindow(callbackId);
  });

  document.addEventListener('mouseup', () => {
    if (!nimbleDragState) return;
    nimbleDragState = null;
    persist();
  });

  function buildAPI(callbackId, shell) {
    const api = {
      callbackId,
      sendMessage: async (createEventTrigger, payload) => {
        const r = await fetch(messageEndpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            callback_id: callbackId,
            create_event_trigger: !!createEventTrigger,
            payload,
          }),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || j.error) throw new Error(j.error || `nimble sendMessage failed: ${r.status}`);
        return j;
      },
      setMessageHandler(func) {
        const win = nimbleWindows.get(callbackId);
        if (win) win.messageHandler = typeof func === 'function' ? func : null;
      },
      resize(width, height) {
        shell.style.width = width;
        shell.style.height = height;
        // 显式指定尺寸时解除 40vw 上限，避免棋盘等宽内容被 overflow:hidden 裁剪
        shell.style.maxWidth = 'none';
      },
      setFullscreen(enabled) {
        const win = nimbleWindows.get(callbackId);
        if (!win) return;
        if (enabled) {
          shell._nimblePrev = {
            width: shell.style.width,
            height: shell.style.height,
            transform: shell.style.transform,
            maxWidth: shell.style.maxWidth,
          };
          shell.style.top = '0';
          shell.style.left = '0';
          shell.style.width = '100vw';
          shell.style.height = '100vh';
          shell.style.maxWidth = 'none';
          shell.style.maxHeight = 'none';
          shell.style.borderRadius = '0';
          shell.style.zIndex = '9999';
          shell.style.background = 'transparent';
          shell.style.backdropFilter = 'none';
          shell.style.transform = '';
          shell.classList.add('nimble-fullscreen');
          win.fullscreen = true;
        } else {
          const prev = shell._nimblePrev || {};
          shell.style.width = prev.width || '360px';
          shell.style.height = prev.height || '';
          shell.style.maxWidth = prev.maxWidth || '40vw';
          shell.style.maxHeight = '70vh';
          shell.style.borderRadius = '14px';
          shell.style.zIndex = '';
          shell.style.background = 'rgba(255,255,255,0.94)';
          shell.style.backdropFilter = 'blur(8px)';
          shell.classList.remove('nimble-fullscreen');
          win.fullscreen = false;
          layoutWindow(callbackId);
        }
      },
      getConfig() {
        const rect = shell.getBoundingClientRect();
        const widget = widgetManager.getWidget(widgetId(callbackId)) || {};
        const win = nimbleWindows.get(callbackId);
        return {
          callbackId,
          width: rect.width,
          height: rect.height,
          x: rect.left,
          y: rect.top,
          coord: widget.coord || null,
          scale: widget.scale || 1,
          fullscreen: !!(win && win.fullscreen),
        };
      },
    };
    return api;
  }

  function closeWindowFn(callbackId, notifyBackend = true, reason = 'closed_locally') {
    const win = nimbleWindows.get(callbackId);
    if (win && win.shell.parentNode) win.shell.parentNode.removeChild(win.shell);
    nimbleWindows.delete(callbackId);
    try { widgetManager.removeWidget(widgetId(callbackId)); } catch (_e) {}
    if (window.nimble && window.nimble.callbackId === callbackId) window.nimble = null;
    if (!notifyBackend) return;
    fetch(closeEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ callback_id: callbackId, reason }),
    }).catch((e) => console.warn('nimble close notify failed', e));
  }

  function handleReservedCommand(callbackId, message) {
    if (!message || typeof message !== 'object') return false;
    if (message.type !== 'command') return false;
    const args = message.args || {};
    switch (message.command) {
      case 'close-window':
        closeWindowFn(callbackId, true, 'closed_by_agent');
        return true;
      case 'set-scale': {
        const scale = Number(args.scale);
        if (Number.isFinite(scale) && scale > 0) {
          try { widgetManager.updateWidget(widgetId(callbackId), { scale: Math.max(0.2, scale) }); } catch (_e) {}
          layoutWindow(callbackId);
          persist();
        }
        return true;
      }
      case 'set-coord': {
        const x = Number(args.x);
        const y = Number(args.y);
        if (Number.isFinite(x) && Number.isFinite(y)) {
          try { widgetManager.updateWidget(widgetId(callbackId), { coord: { x, y } }); } catch (_e) {}
          layoutWindow(callbackId);
          persist();
        }
        return true;
      }
      default:
        console.warn('nimble unknown reserved command', message);
        return true;
    }
  }

  // 后端 NIMBLE_MESSAGE 入口：{callback_id, payload}
  function handleMessage(payload) {
    if (!payload || !payload.callback_id) return;
    const callbackId = payload.callback_id;
    const win = nimbleWindows.get(callbackId);
    if (!win) return;
    const message = payload.payload;
    if (handleReservedCommand(callbackId, message)) return;
    if (typeof win.messageHandler === 'function') {
      try { win.messageHandler(message); } catch (e) { console.warn('nimble messageHandler error', e); }
    }
  }

  function show(payload) {
    if (!payload || !payload.callback_id) return;
    const callbackId = payload.callback_id;
    const host = ensureHost();
    closeWindowFn(callbackId, false);

    const shell = document.createElement('div');
    shell.className = 'nimble-window';
    shell.dataset.callbackId = callbackId;
    shell.style.position = 'fixed';
    shell.style.pointerEvents = 'auto';
    shell.style.width = '360px';
    shell.style.maxWidth = '40vw';
    shell.style.maxHeight = '70vh';
    shell.style.overflow = 'hidden';
    shell.style.background = 'rgba(255,255,255,0.94)';
    shell.style.border = '1px solid rgba(190,201,217,0.78)';
    shell.style.borderRadius = '14px';
    shell.style.boxShadow = '0 16px 38px rgba(32,55,91,0.16)';
    shell.style.color = '#1a2433';
    shell.style.backdropFilter = 'blur(8px)';

    const header = document.createElement('div');
    header.style.display = 'flex';
    header.style.justifyContent = 'space-between';
    header.style.alignItems = 'center';
    header.style.padding = '10px 12px';
    header.style.background = 'rgba(63,107,232,0.08)';
    header.style.fontWeight = '700';
    header.style.cursor = 'grab';
    header.textContent = payload.title || '灵动交互';

    const closeBtn = document.createElement('button');
    closeBtn.textContent = '\u00D7';
    closeBtn.style.marginLeft = '12px';
    closeBtn.style.background = 'transparent';
    closeBtn.style.color = '#647086';
    closeBtn.style.border = 'none';
    closeBtn.style.fontSize = '20px';
    closeBtn.style.cursor = 'pointer';
    closeBtn.onclick = () => closeWindowFn(callbackId, true, 'closed_by_user');
    header.appendChild(closeBtn);

    const body = document.createElement('div');
    body.style.padding = '12px';
    body.style.overflow = 'auto';
    body.style.maxHeight = 'calc(70vh - 48px)';

    shell.appendChild(header);
    shell.appendChild(body);
    host.appendChild(shell);

    const win = { shell, body, header, api: null, messageHandler: null, fullscreen: false };
    nimbleWindows.set(callbackId, win);

    // 注册为小组件（screen 绑定）；持久化窗口沿用已保存的布局
    widgetManager.registerWidget({
      id: widgetId(callbackId),
      element: shell,
      bindingType: 'screen',
      coord: { ...DEFAULT_COORD },
      offset: { x: 0, y: 0 },
      scale: 1,
      hidden: false,
      managed: true,
      onLayout: () => layoutWindow(callbackId),
      schema: { bindingType: 'screen', coord: 'point', scale: 'number', hidden: 'boolean' },
    });
    const persisted = typeof getPersistedWidgetSettings === 'function' ? getPersistedWidgetSettings(widgetId(callbackId)) : null;
    if (persisted) {
      try { widgetManager.updateWidget(widgetId(callbackId), persisted); } catch (_e) {}
    }

    const api = buildAPI(callbackId, shell);
    win.api = api;
    window.nimble = api; // 兼容 HTML 内联事件属性（指向最近显示的窗口）

    try {
      body.innerHTML = payload.html || '<div>\u7A7A\u7A97\u53E3</div>';
    } catch (e) {
      body.textContent = '\u7075\u52A8\u7A97\u53E3 HTML \u6E32\u67D3\u5931\u8D25: ' + String(e);
    }
    try {
      body.querySelectorAll('script').forEach((oldScript) => {
        if (oldScript.src) {
          const newScript = document.createElement('script');
          newScript.src = oldScript.src;
          oldScript.parentNode.replaceChild(newScript, oldScript);
          return;
        }
        const code = oldScript.textContent || '';
        oldScript.remove();
        // 每窗口独立作用域注入 nimble 对象，避免全局单例被后开窗口覆盖
        new Function('nimble', code)(api);
      });
    } catch (e) {
      console.warn('nimble script exec error', e);
    }

    header.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      if (e.target === closeBtn) return;
      if (widgetManager.isEditMode()) return; // 编辑模式交给 widget editor
      if (win.fullscreen) return;
      const widget = widgetManager.getWidget(widgetId(callbackId));
      if (!widget) return;
      nimbleDragState = { callbackId, startX: e.clientX, startY: e.clientY, coord: { ...widget.coord } };
      e.preventDefault();
    });

    layoutWindow(callbackId);
  }

  function close(callbackId, notifyBackend = true, reason = 'closed_locally') {
    closeWindowFn(callbackId, notifyBackend, reason);
  }

  function isPointOverNimble(clientX, clientY) {
    const host = document.getElementById('nimble-host');
    if (!host || host.style.display === 'none') return false;
    const el = document.elementFromPoint(clientX, clientY);
    if (!el) return false;
    if (!host.contains(el) && el !== host) return false;
    if (el.closest('.nimble-pass-through')) return false;
    const win = el.closest('.nimble-window');
    if (!win) return true;
    if (win.classList.contains('nimble-fullscreen')) {
      return isInteractiveElement(el);
    }
    return true;
  }

  function isPointOverWindow(clientX, clientY) {
    const host = document.getElementById('nimble-host');
    if (!host) return false;
    const el = document.elementFromPoint(clientX, clientY);
    if (!el) return false;
    if (el === host) return true;
    if (!host.contains(el)) return false;
    // 非全屏窗口整体算窗口区域；全屏窗口只有交互元素算，避免全屏透明层挡住桌面点击穿透
    const win = el.closest('.nimble-window');
    if (win && win.classList.contains('nimble-fullscreen')) {
      return !el.closest('.nimble-pass-through') && isInteractiveElement(el);
    }
    return true;
  }

  function isInteractiveElement(el) {
    const tag = el.tagName;
    if (['BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'A'].includes(tag)) return true;
    if (el.hasAttribute('onclick') || el.hasAttribute('onmousedown') || el.hasAttribute('onmouseup')) return true;
    if (el.isContentEditable) return true;
    if (el.getAttribute('role') === 'button') return true;
    return false;
  }

  return { show, close, handleMessage, layoutWindows, isPointOverNimble, isPointOverWindow, isInteractiveElement };
}
