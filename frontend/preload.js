const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  backendBaseUrl: 'http://127.0.0.1:13900',
  setIgnoreMouseEvents: (v) => ipcRenderer.invoke('set-ignore-mouse-events', !!v),
  focusMainWindow: () => ipcRenderer.invoke('focus-main-window'),
  hideToTray: () => ipcRenderer.invoke('hide-to-tray'),
  showFromTray: () => ipcRenderer.invoke('show-from-tray'),
  openConfigWindow: () => ipcRenderer.invoke('open-config-window'),
  openLiveWindow: () => ipcRenderer.invoke('open-live-window'),
  resolveFrontendAssetPath: (relativePath) => ipcRenderer.invoke('resolve-frontend-asset-path', String(relativePath || '')),
  getFaustbotRoot: () => ipcRenderer.invoke('get-faustbot-root'),
  restartFaust: () => ipcRenderer.invoke('restart-faust'),
  configRequest: (method, path, payload, query) => ipcRenderer.invoke('config-api', {
    method: String(method || 'GET'),
    path: String(path || ''),
    payload: payload ?? null,
    query: query ?? null,
  }),
  configOpenFile: (options) => ipcRenderer.invoke('config-dialog-open-file', options || {}),
  configOpenDirectory: (options) => ipcRenderer.invoke('config-dialog-open-directory', options || {}),
  configOpenPath: (targetPath) => ipcRenderer.invoke('config-open-path', String(targetPath || '')),
  configHttpRequest: (method, url, payload) => ipcRenderer.invoke('config-http-request', {
    method: String(method || 'GET'),
    url: String(url || ''),
    payload: payload ?? null,
  }),
  toggleLogPanel: () => ipcRenderer.invoke('toggle-log-panel'),
  toggleWidgetEditMode: () => ipcRenderer.invoke('toggle-widget-edit-mode'),
  recreateFrontendWindow: () => ipcRenderer.invoke('recreate-frontend-window'),
  ensureModelProfile: (modelDir, force) => ipcRenderer.invoke('soullink-ensure-profile', { modelDir: String(modelDir || ''), force: !!force }),
  saveModelProfile: (modelDir, json) => ipcRenderer.invoke('soullink-save-profile', { modelDir: String(modelDir || ''), json: json ?? null }),
});

// deeplink events
contextBridge.exposeInMainWorld('deeplink', {
  onConfigFaustCloud: (cb) => {
    if (typeof cb !== 'function') return;
    ipcRenderer.on('deeplink-config-faustcloud', (_evt, payload) => {
      try {
        cb(payload);
      } catch (e) {
        console.error('deeplink callback failed', e);
      }
    });
  }
});

// Listen for faust commands forwarded from the main process
contextBridge.exposeInMainWorld('faust', {
  onCommand: (cb) => {
    // cb will be called with the raw command string from the server
    ipcRenderer.on('faust-command', (evt, cmd) => {
      try {
        cb(cmd);
      } catch (e) {
        console.error('faust.onCommand callback failed', e);
      }
    });
  },
  onPluginInstallResult: (cb) => {
    ipcRenderer.on('plugin-install-result', (_evt, payload) => {
      try {
        cb(payload);
      } catch (e) {
        console.error('faust.onPluginInstallResult callback failed', e);
      }
    });
  },
  onToggleLogPanel: (cb) => {
    ipcRenderer.on('toggle-log-panel', () => {
      try { cb(); } catch (e) { console.error('faust.onToggleLogPanel callback failed', e); }
    });
  }
});

// allow renderer to send logs to main process console
contextBridge.exposeInMainWorld('logToMain', {
  info: (msg) => ipcRenderer.invoke('faust-log', String(msg)).catch(()=>{}),
});
