// Nimble 窗口系统 — 浮动弹窗，支持拖拽/全屏/回调
// 用法: const nimble = initNimbleWindows({ callbackEndpoint, closeEndpoint });

export function initNimbleWindows({ callbackEndpoint, closeEndpoint }) {
  const nimbleWindows = new Map();
  let activeNimbleContext = null;
  let nimbleDragState = null;

  // DOM: ensure host container
  function ensureHost() {
    let host = document.getElementById('nimble-host');
    if (host) return host;
    host = document.createElement('div');
    host.id = 'nimble-host';
    host.style.position = 'fixed';
    host.style.right = '24px';
    host.style.top = '120px';
    host.style.zIndex = '1600';
    host.style.display = 'flex';
    host.style.flexDirection = 'column';
    host.style.gap = '12px';
    host.style.pointerEvents = 'auto';
    document.body.appendChild(host);
    return host;
  }

  // global drag listeners (set up once)
  document.addEventListener('mousemove', (e) => {
    if (!nimbleDragState) return;
    const { shell, offsetX, offsetY } = nimbleDragState;
    shell.style.left = (e.clientX - offsetX) + 'px';
    shell.style.top = (e.clientY - offsetY) + 'px';
    shell.style.right = 'auto';
  });

  document.addEventListener('mouseup', () => {
    if (!nimbleDragState) return;
    const { shell } = nimbleDragState;
    shell.style.cursor = '';
    shell.style.userSelect = '';
    nimbleDragState = null;
  });

  function installAPI(callbackId, shell, header) {
    activeNimbleContext = { callbackId };
    const getState = () => ({
      width: shell.style.width,
      height: shell.style.height,
      left: shell.style.left,
      top: shell.style.top,
      position: shell.style.position,
      draggable: header ? header.classList.contains('nimble-draggable') : false,
      fullscreen: shell.classList.contains('nimble-fullscreen'),
    });

    window.nimble = {
      submit: async (data, closeWindow = true) => {
        const r = await fetch(callbackEndpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ callback_id: callbackId, data, close: closeWindow }),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || j.error) throw new Error(j.error || `nimble submit failed: ${r.status}`);
        if (closeWindow) closeWindowFn(callbackId, false);
        return j;
      },
      close: async (reason = 'closed_by_user') => {
        const r = await fetch(closeEndpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ callback_id: callbackId, reason }),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || j.error) throw new Error(j.error || `nimble close failed: ${r.status}`);
        closeWindowFn(callbackId, false);
        return j;
      },
      resize(width, height) {
        shell.style.width = width;
        shell.style.height = height;
      },
      move(x, y) {
        shell.style.left = x;
        shell.style.top = y;
        shell.style.right = 'auto';
      },
      setDraggable(enabled) {
        if (!header) return;
        header.classList.toggle('nimble-draggable', enabled);
        header.style.cursor = enabled ? 'grab' : '';
      },
      setFullscreen(enabled) {
        if (enabled) {
          shell._nimblePrev = {
            width: shell.style.width,
            height: shell.style.height,
            left: shell.style.left,
            top: shell.style.top,
            right: shell.style.right,
            position: shell.style.position,
          };
          shell.style.position = 'fixed';
          shell.style.top = '0';
          shell.style.left = '0';
          shell.style.right = '0';
          shell.style.width = '100vw';
          shell.style.height = '100vh';
          shell.style.maxWidth = 'none';
          shell.style.maxHeight = 'none';
          shell.style.borderRadius = '0';
          shell.style.zIndex = '9999';
          shell.style.background = 'transparent';
          shell.style.backdropFilter = 'none';
          shell.classList.add('nimble-fullscreen');
        } else {
          const prev = shell._nimblePrev || {};
          shell.style.position = prev.position || 'relative';
          shell.style.top = prev.top || '';
          shell.style.left = prev.left || '';
          shell.style.right = prev.right || '';
          shell.style.width = prev.width || '360px';
          shell.style.height = prev.height || '';
          shell.style.maxWidth = '40vw';
          shell.style.maxHeight = '70vh';
          shell.style.borderRadius = '14px';
          shell.style.zIndex = '';
          shell.style.background = 'rgba(20,24,30,0.92)';
          shell.style.backdropFilter = 'blur(8px)';
          shell.classList.remove('nimble-fullscreen');
        }
      },
      getConfig() {
        const rect = shell.getBoundingClientRect();
        return {
          callbackId,
          width: rect.width,
          height: rect.height,
          x: rect.left,
          y: rect.top,
          zIndex: shell.style.zIndex,
          ...getState(),
        };
      },
    };
  }

  function closeWindowFn(callbackId, notifyBackend = true, reason = 'closed_locally') {
    const win = nimbleWindows.get(callbackId);
    if (win && win.parentNode) win.parentNode.removeChild(win);
    nimbleWindows.delete(callbackId);
    if (activeNimbleContext && activeNimbleContext.callbackId === callbackId) {
      activeNimbleContext = null;
    }
    if (!notifyBackend) return;
    fetch(closeEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ callback_id: callbackId, reason }),
    }).catch((e) => console.warn('nimble close notify failed', e));
  }

  function show(payload) {
    if (!payload || !payload.callback_id) return;
    const host = ensureHost();
    closeWindowFn(payload.callback_id, false);

    const shell = document.createElement('div');
    shell.className = 'nimble-window';
    shell.dataset.callbackId = payload.callback_id;
    shell.style.width = '360px';
    shell.style.maxWidth = '40vw';
    shell.style.maxHeight = '70vh';
    shell.style.overflow = 'hidden';
    shell.style.background = 'rgba(20,24,30,0.92)';
    shell.style.border = '1px solid rgba(255,255,255,0.12)';
    shell.style.borderRadius = '14px';
    shell.style.boxShadow = '0 10px 30px rgba(0,0,0,0.4)';
    shell.style.color = '#fff';
    shell.style.backdropFilter = 'blur(8px)';

    const header = document.createElement('div');
    header.style.display = 'flex';
    header.style.justifyContent = 'space-between';
    header.style.alignItems = 'center';
    header.style.padding = '10px 12px';
    header.style.background = 'rgba(255,255,255,0.06)';
    header.style.fontWeight = '700';
    header.textContent = payload.title || '灵动交互';

    const closeBtn = document.createElement('button');
    closeBtn.textContent = '\u00D7';
    closeBtn.style.marginLeft = '12px';
    closeBtn.style.background = 'transparent';
    closeBtn.style.color = '#fff';
    closeBtn.style.border = 'none';
    closeBtn.style.fontSize = '20px';
    closeBtn.style.cursor = 'pointer';
    closeBtn.onclick = () => closeWindowFn(payload.callback_id, true, 'closed_by_user');
    header.appendChild(closeBtn);

    const body = document.createElement('div');
    body.style.padding = '12px';
    body.style.overflow = 'auto';
    body.style.maxHeight = 'calc(70vh - 48px)';

    shell.appendChild(header);
    shell.appendChild(body);
    host.appendChild(shell);
    nimbleWindows.set(payload.callback_id, shell);

    installAPI(payload.callback_id, shell, header);
    try {
      body.innerHTML = payload.html || '<div>\u7A7A\u7A97\u53E3</div>';
    } catch (e) {
      body.textContent = '\u7075\u52A8\u7A97\u53E3 HTML \u6E32\u67D3\u5931\u8D25: ' + String(e);
    }
    try {
      body.querySelectorAll('script').forEach((oldScript) => {
        const newScript = document.createElement('script');
        if (oldScript.src) {
          newScript.src = oldScript.src;
        } else {
          newScript.textContent = oldScript.textContent;
        }
        oldScript.parentNode.replaceChild(newScript, oldScript);
      });
    } catch (e) {
      console.warn('nimble script exec error', e);
    }

    header.addEventListener('mousedown', (e) => {
      if (!header.classList.contains('nimble-draggable')) return;
      if (e.button !== 0) return;
      const rect = shell.getBoundingClientRect();
      const offsetX = e.clientX - rect.left;
      const offsetY = e.clientY - rect.top;
      nimbleDragState = { shell, offsetX, offsetY };
      shell.style.cursor = 'grabbing';
      shell.style.userSelect = 'none';
      e.preventDefault();
    });
  }

  function close(callbackId, notifyBackend = true, reason = 'closed_locally') {
    closeWindowFn(callbackId, notifyBackend, reason);
  }

  function isPointOverNimble(clientX, clientY) {
    const host = document.getElementById('nimble-host');
    if (!host) return false;
    const el = document.elementFromPoint(clientX, clientY);
    if (!el) return false;
    const win = el.closest('.nimble-window');
    if (!win) return false;
    if (el.closest('.nimble-pass-through')) return false;
    if (win.classList.contains('nimble-fullscreen')) {
      return isInteractiveElement(el);
    }
    return true;
  }

  function isPointOverWindow(clientX, clientY) {
    const el = document.elementFromPoint(clientX, clientY);
    if (!el) return false;
    return !!el.closest('.nimble-window');
  }

  function isInteractiveElement(el) {
    const tag = el.tagName;
    if (['BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'A'].includes(tag)) return true;
    if (el.hasAttribute('onclick') || el.hasAttribute('onmousedown') || el.hasAttribute('onmouseup')) return true;
    if (el.isContentEditable) return true;
    if (el.getAttribute('role') === 'button') return true;
    return false;
  }

  return { show, close, isPointOverNimble, isPointOverWindow, isInteractiveElement };
}
