const { app, BrowserWindow, ipcMain, globalShortcut, Tray, Menu, dialog, protocol, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const http = require('http');
const https = require('https');
const { URL } = require('url');
const { spawn, exec } = require('child_process');

const net = require('net');

let mainWindow = null;
let configWindow = null;
let tray = null;
let pendingDeepLinks = [];
let _backendReady = false;

const FAUST_PROTOCOL = 'faustbot';
const STATIC_PROTOCOL = 'static';
const FAUST_BACKEND_BASE = 'http://127.0.0.1:13900';
const FAUST_BACKEND_SYNC_API = 'http://127.0.0.1:13900/faust/admin/plugin-market/sync';

protocol.registerSchemesAsPrivileged([
  {
    scheme: STATIC_PROTOCOL,
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      stream: true,
      corsEnabled: true,
    },
  },
]);

function registerFaustProtocolClient() {
  try {
    let ok = false;
    if (process.defaultApp && process.argv.length >= 2) {
      // Dev mode: electron .
      ok = app.setAsDefaultProtocolClient(FAUST_PROTOCOL, process.execPath, [path.resolve(process.argv[1])]);
    } else {
      // Packaged app
      ok = app.setAsDefaultProtocolClient(FAUST_PROTOCOL);
    }
    console.log(`[deeplink] register protocol ${FAUST_PROTOCOL}:`, ok);
    return ok;
  } catch (e) {
    console.warn('[deeplink] register protocol failed', e);
    return false;
  }
}

function getFrontendAppDir() {
  return __dirname;
}

function findFrontendProjectDir() {
  if (!app.isPackaged) return __dirname;

  const exeDir = path.dirname(process.execPath);
  const candidates = [
    exeDir,
    path.resolve(exeDir, '..'),
    path.resolve(exeDir, '..', 'frontend'),
    path.resolve(process.resourcesPath, '..', '..'),
    path.resolve(process.resourcesPath, '..', '..', 'frontend'),
  ];

  for (const candidate of candidates) {
    if (!candidate) continue;
    if (fs.existsSync(path.join(candidate, '2D')) && fs.existsSync(path.join(candidate, 'index.html'))) {
      return candidate;
    }
  }

  return exeDir;
}

function getFrontendResourceDir() {
  return app.isPackaged ? process.resourcesPath : __dirname;
}

function getRepoRootDir() {
  return path.resolve(findFrontendProjectDir(), '..');
}

function getFaustHomeDir() {
  return path.join(os.homedir(), '.faustbot');
}

function getImageModelDir() {
  return path.join(getFaustHomeDir(), 'models', 'image');
}

function getLive2DModelDir() {
  return path.join(getFaustHomeDir(), 'models', '2D');
}

function getVrmModelDir() {
  return path.join(getFaustHomeDir(), 'models', 'VRM');
}

function getStaticBases() {
  const frontendProjectDir = findFrontendProjectDir();
  const repoRootDir = getRepoRootDir();
  return {
    frontend: frontendProjectDir,
    repo: repoRootDir,
    live2d_models: getLive2DModelDir(),
    vrm_models: getVrmModelDir(),
    image_models: getImageModelDir(),
  };
}

function toStaticUrl(baseKey, absolutePath) {
  const bases = getStaticBases();
  const baseDir = bases[baseKey];
  if (!baseDir) return '';
  const resolvedBase = path.resolve(baseDir);
  const resolvedPath = path.resolve(absolutePath);
  const relativePath = path.relative(resolvedBase, resolvedPath);
  if (!relativePath || relativePath.startsWith('..') || path.isAbsolute(relativePath)) {
    return '';
  }
  const encodedRelativePath = relativePath.split(path.sep).map(encodeURIComponent).join('/');
  return `${STATIC_PROTOCOL}://${baseKey}/${encodedRelativePath}`;
}

function resolveStaticRequestToPath(requestUrl) {
  const parsed = new URL(requestUrl);
  const baseKey = String(parsed.hostname || '').trim();
  const bases = getStaticBases();
  const baseDir = bases[baseKey];
  if (!baseDir) return null;

  const relativePath = decodeURIComponent(parsed.pathname || '')
    .replace(/^\/+/,'')
    .replace(/\//g, path.sep);
  const resolvedBase = path.resolve(baseDir);
  const resolvedPath = path.resolve(baseDir, relativePath);
  if (!resolvedPath.startsWith(resolvedBase + path.sep) && resolvedPath !== resolvedBase) {
    return null;
  }
  return resolvedPath;
}

function registerStaticProtocol() {
  protocol.handle(STATIC_PROTOCOL, async (request) => {
    try {
      const resolvedPath = resolveStaticRequestToPath(request.url);
      if (!resolvedPath) {
        return new Response('Forbidden', { status: 403 });
      }
      if (!fs.existsSync(resolvedPath) || !fs.statSync(resolvedPath).isFile()) {
        return new Response('Not Found', { status: 404 });
      }
      const data = fs.readFileSync(resolvedPath);
      return new Response(data, {
        status: 200,
        headers: {
          'Content-Type': getMimeType(resolvedPath),
          'Cache-Control': 'no-cache',
        },
      });
    } catch (error) {
      console.error('[static] request failed', request.url, error);
      return new Response('Internal Server Error', { status: 500 });
    }
  });
}

function getMimeType(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === '.json') return 'application/json; charset=utf-8';
  if (ext === '.png') return 'image/png';
  if (ext === '.jpg' || ext === '.jpeg') return 'image/jpeg';
  if (ext === '.moc3') return 'application/octet-stream';
  if (ext === '.physics3.json') return 'application/json; charset=utf-8';
  if (ext === '.motion3.json') return 'application/json; charset=utf-8';
  if (ext === '.exp3.json') return 'application/json; charset=utf-8';
  if (ext === '.wav') return 'audio/wav';
  if (ext === '.vrm') return 'model/gltf-binary';
  return 'application/octet-stream';
}

function resolveFrontendAssetPath(relativePath) {
  const raw = String(relativePath || '').trim();
  if (!raw) return '';
  if (/^https?:/i.test(raw) || new RegExp(`^${STATIC_PROTOCOL}:`, 'i').test(raw)) return raw;

  const normalized = raw.replace(/\\/g, '/');
  const repoRootDir = getRepoRootDir();
  const frontendProjectDir = findFrontendProjectDir();
  const candidatePaths = [];

  if (/^[a-zA-Z]:\//.test(normalized) || normalized.startsWith('/')) {
    candidatePaths.push({ baseKey: null, path: normalized });
  } else {
    const trimmed = normalized.replace(/^[/\\]+/, '');
    const withoutFrontendPrefix = trimmed.replace(/^frontend\//i, '');
    const withoutModel2DPrefix = withoutFrontendPrefix.replace(/^2D\//i, '');
    const withoutVrmPrefix = withoutFrontendPrefix.replace(/^VRM\//i, '');

    candidatePaths.push({ baseKey: 'frontend', path: path.join(frontendProjectDir, trimmed) });
    candidatePaths.push({ baseKey: 'frontend', path: path.join(frontendProjectDir, withoutFrontendPrefix) });
    candidatePaths.push({ baseKey: 'repo', path: path.join(repoRootDir, trimmed) });
    candidatePaths.push({ baseKey: 'repo', path: path.join(repoRootDir, withoutFrontendPrefix) });
    candidatePaths.push({ baseKey: 'live2d_models', path: path.join(getLive2DModelDir(), withoutModel2DPrefix) });
    candidatePaths.push({ baseKey: 'vrm_models', path: path.join(getVrmModelDir(), withoutVrmPrefix) });
    candidatePaths.push({ baseKey: 'image_models', path: path.join(getImageModelDir(), trimmed) });
    candidatePaths.push({ baseKey: 'image_models', path: path.join(getImageModelDir(), withoutFrontendPrefix) });

    if (/^2D\//i.test(withoutFrontendPrefix)) {
      candidatePaths.push({ baseKey: 'live2d_models', path: path.join(getLive2DModelDir(), withoutModel2DPrefix) });
      candidatePaths.push({ baseKey: 'frontend', path: path.join(frontendProjectDir, withoutFrontendPrefix) });
      candidatePaths.push({ baseKey: 'repo', path: path.join(repoRootDir, 'frontend', withoutFrontendPrefix) });
    }
    if (/^VRM\//i.test(withoutFrontendPrefix)) {
      candidatePaths.push({ baseKey: 'vrm_models', path: path.join(getVrmModelDir(), withoutVrmPrefix) });
    }
  }

  for (const candidate of candidatePaths) {
    const resolved = path.resolve(candidate.path);
    if (fs.existsSync(resolved)) {
      if (candidate.baseKey) {
        const staticUrl = toStaticUrl(candidate.baseKey, resolved);
        if (staticUrl) return staticUrl;
      }

      const bases = getStaticBases();
      for (const [baseKey, baseDir] of Object.entries(bases)) {
        const resolvedBase = path.resolve(baseDir);
        if (resolved === resolvedBase || resolved.startsWith(resolvedBase + path.sep)) {
          const staticUrl = toStaticUrl(baseKey, resolved);
          if (staticUrl) return staticUrl;
        }
      }
    }
  }

  // Absolute paths that didn't match any static base → use file:// directly
  if (/^[a-zA-Z]:\//.test(normalized) || normalized.startsWith('/')) {
    const absPath = path.resolve(normalized);
    if (fs.existsSync(absPath)) {
      return 'file:///' + absPath.replace(/\\/g, '/').replace(/^\//, '');
    }
  }

  const fallbackRelative = normalized.replace(/^[/\\]+/, '').replace(/^frontend\//i, '');
  return `${STATIC_PROTOCOL}://frontend/${fallbackRelative.split('/').map(encodeURIComponent).join('/')}`;
}

function postJson(url, payload, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    try {
      const u = new URL(url);
      const data = Buffer.from(JSON.stringify(payload || {}), 'utf8');
      const lib = u.protocol === 'https:' ? https : http;
      const req = lib.request({
        method: 'POST',
        hostname: u.hostname,
        port: u.port || (u.protocol === 'https:' ? 443 : 80),
        path: `${u.pathname}${u.search || ''}`,
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': data.length,
        },
      }, (res) => {
        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          let parsed = null;
          try {
            parsed = text ? JSON.parse(text) : null;
          } catch (e) {
            parsed = { raw: text };
          }
          if ((res.statusCode || 500) >= 400) {
            const err = new Error(`HTTP ${res.statusCode}: ${text}`);
            err.statusCode = res.statusCode || 500;
            err.response = parsed;
            return reject(err);
          }
          resolve(parsed);
        });
      });

      req.setTimeout(timeoutMs, () => {
        req.destroy(new Error(`请求超时(${timeoutMs}ms)`));
      });
      req.on('error', reject);
      req.write(data);
      req.end();
    } catch (e) {
      reject(e);
    }
  });
}

function requestJson(method, url, payload = null, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    try {
      const u = new URL(url);
      const hasBody = payload !== null && payload !== undefined && method !== 'GET';
      const data = hasBody ? Buffer.from(JSON.stringify(payload), 'utf8') : null;
      const lib = u.protocol === 'https:' ? https : http;
      const req = lib.request({
        method,
        hostname: u.hostname,
        port: u.port || (u.protocol === 'https:' ? 443 : 80),
        path: `${u.pathname}${u.search || ''}`,
        headers: hasBody ? {
          'Content-Type': 'application/json',
          'Content-Length': data.length,
        } : {},
      }, (res) => {
        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          let parsed = null;
          try {
            parsed = text ? JSON.parse(text) : null;
          } catch (e) {
            parsed = { raw: text };
          }
          if ((res.statusCode || 500) >= 400) {
            const err = new Error(`HTTP ${res.statusCode}: ${text}`);
            err.statusCode = res.statusCode || 500;
            err.response = parsed;
            return reject(err);
          }
          resolve(parsed);
        });
      });

      req.setTimeout(timeoutMs, () => {
        req.destroy(new Error(`请求超时(${timeoutMs}ms)`));
      });
      req.on('error', reject);
      if (data) {
        req.write(data);
      }
      req.end();
    } catch (e) {
      reject(e);
    }
  });
}

function buildBackendUrl(apiPath, query) {
  const normalizedPath = String(apiPath || '').trim();
  const url = new URL(`${FAUST_BACKEND_BASE}${normalizedPath}`);
  if (query && typeof query === 'object') {
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null) continue;
      url.searchParams.set(String(k), String(v));
    }
  }
  return url.toString();
}

function parseFaustDeepLink(rawUrl) {
  try {
    if (!rawUrl || typeof rawUrl !== 'string') return null;
    const parsed = new URL(rawUrl);
    if (parsed.protocol !== 'faustbot:') return null;
    const action = (parsed.hostname || parsed.pathname.replace(/^\//, '') || '').trim();

    if (action.toLowerCase() === 'syncplugin') {
      const pluginId = (parsed.searchParams.get('id') || '').trim();
      if (!pluginId) {
        throw new Error('缺少插件 id 参数');
      }
      return {
        type: 'sync_plugin',
        pluginId,
        rawUrl,
      };
    }

    // config FaustBot Cloud via deeplink
    if (action === 'config_faustcloud') {
      const key = (parsed.searchParams.get('key') || '').trim();
      let host = (parsed.searchParams.get('host') || '').trim();
      if (!key || !host) {
        throw new Error('缺少 key 或 host 参数');
      }
      // decode URL-safe base64 host, restore padding if necessary
      try {
        const decodeBase64Url = (s) => {
          if (!s) return '';
          // replace URL-safe chars
          let t = String(s).replace(/-/g, '+').replace(/_/g, '/');
          const padLen = (4 - (t.length % 4)) % 4;
          if (padLen > 0) t += '='.repeat(padLen);
          return Buffer.from(t, 'base64').toString('utf8');
        };
        const decoded = decodeBase64Url(host);
        if (decoded) host = decoded;
      } catch (e) {
        console.warn('[deeplink] host base64 decode failed, using raw host', e);
      }
      return {
        type: 'config_faustcloud',
        key,
        host,
        rawUrl,
      };
    }

    return null;
  } catch (e) {
    console.error('[deeplink] parse failed:', rawUrl, e);
    return null;
  }
}

async function runSyncPluginByDeepLink(task) {
  if (!task) return;

  // handle FaustBot Cloud config deeplink
  if (task.type === 'config_faustcloud') {
    try {
      const cfgWin = createConfigWindow();
      if (cfgWin && cfgWin.webContents) {
        cfgWin.webContents.once('did-finish-load', () => {
          try {
            cfgWin.webContents.send('deeplink-config-faustcloud', { host: task.host, key: task.key });
          } catch (e) {
            console.error('[deeplink] send config payload failed', e);
          }
        });
        // if already ready, send immediately
        if (cfgWin.webContents.isLoading() === false) {
          try { cfgWin.webContents.send('deeplink-config-faustcloud', { host: task.host, key: task.key }); } catch (e) {}
        }
      }
      return;
    } catch (e) {
      console.error('[deeplink] config_faustcloud handling failed', e);
      return;
    }
  }

  if (task.type !== 'sync_plugin') return;

  const targetWindow = mainWindow || BrowserWindow.getFocusedWindow() || undefined;
  const firstConfirm = await dialog.showMessageBox(targetWindow, {
    type: 'warning',
    title: '确认安装第三方插件',
    message: `即将安装/更新插件：${task.pluginId}`,
    detail: '该插件由第三方创建，可能包含安全风险。请仅安装你信任来源的插件。若插件已存在将被覆盖更新。是否继续？',
    buttons: ['继续', '取消'],
    defaultId: 0,
    cancelId: 1,
    noLink: true,
  });
  if (firstConfirm.response !== 0) {
    if (mainWindow && mainWindow.webContents) {
      mainWindow.webContents.send('plugin-install-result', {
        ok: false,
        pluginId: task.pluginId,
        canceled: true,
        error: '用户取消了插件安装',
      });
    }
    return;
  }

  const payload = {
    plugin_id: task.pluginId,
    apply_runtime: true,
    reset_dialog: false,
    no_initial_chat: true,
  };

  try {
    const result = await postJson(FAUST_BACKEND_SYNC_API, payload);
    console.log('[deeplink] plugin sync success:', task.pluginId, result);
    if (mainWindow && mainWindow.webContents) {
      mainWindow.webContents.send('plugin-install-result', {
        ok: true,
        pluginId: task.pluginId,
        result,
      });
    }
  } catch (e) {
    console.error('[deeplink] plugin sync failed:', task.pluginId, e);
    if (mainWindow && mainWindow.webContents) {
      mainWindow.webContents.send('plugin-install-result', {
        ok: false,
        pluginId: task.pluginId,
        error: String(e),
      });
    }
  }
}

async function flushPendingDeepLinks() {
  if (!pendingDeepLinks.length) return;
  const tasks = pendingDeepLinks.slice();
  pendingDeepLinks = [];
  for (const task of tasks) {
    await runSyncPluginByDeepLink(task);
  }
}

function queueDeepLinkUrl(rawUrl) {
  const task = parseFaustDeepLink(rawUrl);
  if (!task) return false;
  pendingDeepLinks.push(task);
  if (mainWindow) {
    showMainWindow();
    flushPendingDeepLinks();
  }
  return true;
}

function decodeWsTextMessage(data, isBinary = false) {
  if (typeof data === 'string') return data;

  try {
    if (Buffer.isBuffer(data)) {
      return data.toString('utf8');
    }

    if (data instanceof ArrayBuffer) {
      return Buffer.from(data).toString('utf8');
    }

    if (ArrayBuffer.isView(data)) {
      return Buffer.from(data.buffer, data.byteOffset, data.byteLength).toString('utf8');
    }

    if (isBinary && data && typeof data.toString === 'function') {
      return data.toString('utf8');
    }
  } catch (e) {
    console.error('[faust-ws] utf8 decode failed, fallback to String(data)', e);
  }

  return String(data ?? '');
}

const GLOBAL_SHORTCUTS = [
  { accelerator: 'CommandOrControl+Alt+A', command: 'TOGGLE_ASR' },
  { accelerator: 'CommandOrControl+Alt+S', command: 'STOP_AUDIO' },
  { accelerator: 'CommandOrControl+Alt+X', command: 'INTERRUPT_CHAT' },
  { accelerator: 'CommandOrControl+Alt+Up', command: 'SCALE_UP' },
  { accelerator: 'CommandOrControl+Alt+Down', command: 'SCALE_DOWN' },
  { accelerator: 'CommandOrControl+Alt+M', command: 'RANDOM_MOTION' },
  { accelerator: 'CommandOrControl+Shift+T', command: 'FOCUS_TEXT_CHAT' },
];

function sendFaustCommand(command) {
  if (!mainWindow || !mainWindow.webContents) return false;
  try {
    if (command === 'FOCUS_TEXT_CHAT') {
      showMainWindow();
    }
    mainWindow.webContents.send('faust-command', command);
    return true;
  } catch (e) {
    console.error('Failed to send faust command from shortcut', command, e);
    return false;
  }
}

function registerGlobalShortcuts() {
  for (const item of GLOBAL_SHORTCUTS) {
    try {
      const ok = globalShortcut.register(item.accelerator, () => {
        sendFaustCommand(item.command);
      });
      if (!ok) {
        console.warn('[shortcut] register failed:', item.accelerator, '->', item.command);
      }
    } catch (e) {
      console.error('[shortcut] register error:', item.accelerator, item.command, e);
    }
  }
}

function createWindow(){
  const windowIconPath = path.join(getFrontendAppDir(), 'FaustBot.icon.tiny.png');
  mainWindow = new BrowserWindow({
    width: 900,
    height: 700,
    title: 'FaustBot',
    fullscreen: true,
    fullscreenable: true,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    hasShadow: false,
    resizable: false,
    alwaysOnTop: true,
    icon: fs.existsSync(windowIconPath) ? windowIconPath : undefined,
    webPreferences: {
      preload: path.join(getFrontendAppDir(), 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    }
  });

  const index = path.join(getFrontendAppDir(), 'index.html');
  mainWindow.loadFile(index);

  mainWindow.webContents.on('did-start-loading', () => {
    rendererReady = false;
  });

  mainWindow.webContents.on('did-finish-load', () => {
    flushCommandBuffer();
  });
  // start fullscreen. mouse-ignore (click-through) is controlled from renderer via IPC

  // Ensure the window remains fullscreen: if it ever leaves fullscreen or is resized,
  // re-enter fullscreen shortly after. This keeps the app visually always-fullscreen.
  mainWindow.on('leave-full-screen', () => {
    try{
      // small delay to avoid races
      setTimeout(()=>{ if (mainWindow && !mainWindow.isDestroyed()) mainWindow.setFullScreen(true); }, 120);
    }catch(e){ console.error('Re-enter fullscreen failed', e); }
  });


  // If window is resized or maximized/unmaximized, force fullscreen again
  mainWindow.on('resize', () => {
    try{ if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isFullScreen()) mainWindow.setFullScreen(true); }catch(e){}
  });

  mainWindow.on('closed', ()=>{ mainWindow = null });

  mainWindow.setAlwaysOnTop(true, 'screen-saver');
}

function createConfigWindow() {
  if (!_backendReady) {
    dialog.showMessageBox({
      type: 'info',
      title: '后端启动中',
      message: '后端服务正在启动，请稍后再试。',
      buttons: ['确定'],
    });
    return null;
  }

  if (configWindow && !configWindow.isDestroyed()) {
    configWindow.show();
    if (configWindow.isMinimized()) configWindow.restore();
    configWindow.focus();
    return configWindow;
  }

  const windowIconPath = path.join(getFrontendAppDir(), 'FaustBot.icon.tiny.png');
  configWindow = new BrowserWindow({
    width: 1240,
    height: 860,
    minWidth: 1080,
    minHeight: 700,
    title: 'FaustBot 配置中心',
    backgroundColor: '#edf1f6',
    frame: true,
    show: false,
    autoHideMenuBar: true,
    icon: fs.existsSync(windowIconPath) ? windowIconPath : undefined,
    webPreferences: {
      preload: path.join(getFrontendAppDir(), 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  configWindow.loadFile(path.join(getFrontendAppDir(), 'config-window.html'));
  configWindow.once('ready-to-show', () => {
    if (!configWindow || configWindow.isDestroyed()) return;
    configWindow.show();
    configWindow.focus();
  });

  configWindow.on('closed', () => {
    configWindow = null;
  });

  return configWindow;
}

function recreateFrontendMainWindow() {
  _isRecreating = true;
  setTimeout(() => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.close();
    }
    //wait a bit for the window to close before creating a new one
    setTimeout(() => {
      _isRecreating = false;
      createWindow();
    }, 300); // wait 300ms before creating a new window
  }, 300); // wait 300ms before closing the old window
}

let liveWindow = null;

function createLiveWindow() {
  if (liveWindow && !liveWindow.isDestroyed()) {
    liveWindow.show();
    if (liveWindow.isMinimized()) liveWindow.restore();
    liveWindow.focus();
    return liveWindow;
  }

  const windowIconPath = path.join(getFrontendAppDir(), 'FaustBot.icon.tiny.png');
  liveWindow = new BrowserWindow({
    width: 900,
    height: 700,
    minWidth: 700,
    minHeight: 500,
    title: 'FaustBot 直播控制台',
    backgroundColor: '#edf1f6',
    frame: true,
    show: false,
    autoHideMenuBar: true,
    icon: fs.existsSync(windowIconPath) ? windowIconPath : undefined,
    webPreferences: {
      preload: path.join(getFrontendAppDir(), 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  liveWindow.loadFile(path.join(getFrontendAppDir(), 'live-window.html'));
  liveWindow.once('ready-to-show', () => {
    if (!liveWindow || liveWindow.isDestroyed()) return;
    liveWindow.show();
    liveWindow.focus();
  });

  liveWindow.on('closed', () => {
    liveWindow = null;
  });

  return liveWindow;
}

function spawnDetachedWithCheck(cmd, args, options = {}) {
  return new Promise((resolve) => {
    let settled = false;
    try {
      const child = spawn(cmd, args, {
        cwd: __dirname,
        detached: true,
        stdio: 'ignore',
        windowsHide: true,
        shell: false,
        ...options,
      });

      const finish = (result) => {
        if (settled) return;
        settled = true;
        try { child.removeAllListeners('error'); } catch (e) {}
        try { child.removeAllListeners('spawn'); } catch (e) {}
        resolve(result);
      };

      child.once('error', (error) => {
        finish({ ok: false, error: String(error), launcher: cmd });
      });

      child.once('spawn', () => {
        try { child.unref(); } catch (e) {}
        finish({ ok: true, launcher: cmd });
      });
    } catch (error) {
      resolve({ ok: false, error: String(error), launcher: cmd });
    }
  });
}

async function launchPySideConfiger(){
  const resourceDir = getFrontendResourceDir();
  const repoRootDir = getRepoRootDir();
  const scriptPath = path.join(resourceDir, 'configer_pyside6.py');
  const startBatPath = path.join(resourceDir, 'start-configer.bat');
  const runtimePythonPath = path.join(repoRootDir, '.runtime', 'python.exe');
  const bootstrapPath = app.isPackaged
    ? path.join(resourceDir, 'embedded_python_bootstrap.py')
    : path.join(repoRootDir, 'embedded_python_bootstrap.py');
  if (!fs.existsSync(scriptPath)) {
    return { ok: false, error: `Configer 脚本不存在: ${scriptPath}` };
  }

  const candidates = [];
  if (fs.existsSync(runtimePythonPath)) {
    candidates.push({ cmd: runtimePythonPath, args: [bootstrapPath, scriptPath] });
  }
  if (fs.existsSync(startBatPath)) {
    candidates.push({
      cmd: 'cmd.exe',
      args: ['/c', 'start', '', startBatPath],
      options: { shell: false, windowsHide: true },
    });
  }
  if (process.env.PYTHON) {
    candidates.push({ cmd: process.env.PYTHON, args: [scriptPath] });
  }
  candidates.push(
    { cmd: 'python', args: [scriptPath] },
    { cmd: 'py', args: ['-3', scriptPath] },
  );

  let lastResult = null;
  for (const candidate of candidates) {
    const result = await spawnDetachedWithCheck(candidate.cmd, candidate.args, candidate.options || {});
    if (result.ok) {
      return result;
    }
    lastResult = result;
  }

  return { ok: false, error: String((lastResult && lastResult.error) || '未找到可用 Python 解释器或启动脚本') };
}

function getTrayIconPath(){
  const candidates = [
    path.join(__dirname, 'FaustBot.icon.tiny.png'),
    path.join(getFrontendResourceDir(), 'FaustBot.icon.tiny.png'),
    path.join(getFrontendResourceDir(), 'fake_neuro.ico'),
    path.join(getFrontendResourceDir(), 'dmx1.png'),
    path.join(__dirname, '..', '..', 'live-2d', 'fake_neuro.ico'),
    path.join(__dirname, '..', '..', 'image', 'dmx1.png'),
  ];
  return candidates.find((candidate)=> fs.existsSync(candidate)) || null;
}

ipcMain.handle('resolve-frontend-asset-path', (_event, relativePath) => {
  return resolveFrontendAssetPath(relativePath);
});

function showMainWindow(){
  if (!mainWindow || mainWindow.isDestroyed()) {
    createWindow();
  }
  if (!mainWindow) return false;
  try{
    mainWindow.show();
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.setSkipTaskbar(false);
    mainWindow.setAlwaysOnTop(true, 'screen-saver');
    mainWindow.focus();
    return true;
  }catch(e){
    console.error('showMainWindow failed', e);
    return false;
  }
}

function hideMainWindowToTray(){
  if (!mainWindow) return false;
  try{
    mainWindow.hide();
    mainWindow.setSkipTaskbar(true);
    return true;
  }catch(e){
    console.error('hideMainWindowToTray failed', e);
    return false;
  }
}

function createTray(){
  if (tray) return tray;
  const trayIconPath = getTrayIconPath();
  if (!trayIconPath) {
    console.warn('Tray icon not found, tray feature disabled.');
    return null;
  }

  tray = new Tray(trayIconPath);
  tray.setToolTip('FaustBot');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '显示前端', click: ()=> showMainWindow() },
    { label: '打开配置中心', click: ()=> createConfigWindow() },
    { label: '隐藏到托盘', click: ()=> hideMainWindowToTray() },
    { type: 'separator' },
    { label: '退出', click: ()=> app.quit() },
  ]));
  tray.on('double-click', ()=>{ showMainWindow(); });
  return tray;
}

const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', (_event, argv) => {
    showMainWindow();
    for (const arg of (argv || [])) {
      if (typeof arg === 'string' && arg.startsWith('faustbot://')) {
        queueDeepLinkUrl(arg);
      }
    }
  });
}

app.on('open-url', (event, url) => {
  event.preventDefault();
  queueDeepLinkUrl(url);
});

const BACKEND_PORT_TO_CHECK = 13900;

function findBackendDir() {
  const exeDir = path.dirname(process.execPath);
  const frontendDir = findFrontendProjectDir();
  const candidates = [
    path.join(frontendDir, '..', 'backend'),
    path.join(__dirname, '..', 'backend'),
    path.join(exeDir, 'backend'),
    path.join(exeDir, '..', 'backend'),
    path.join(process.resourcesPath, 'backend'),
    path.join(process.resourcesPath, '..', 'backend'),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, 'MAIN.bat'))) {
      return path.resolve(candidate);
    }
  }
  return path.resolve(candidates[0]);
}

const BACKEND_DIR = findBackendDir();
const BACKEND_MAIN = path.join(BACKEND_DIR, 'main.py');

function checkPort(port){
  return new Promise((resolve) => {
    const socket = new net.Socket();
    socket.setTimeout(1500);
    socket.on('connect', () => { socket.destroy(); resolve(true); });
    socket.on('error', () => { socket.destroy(); resolve(false); });
    socket.on('timeout', () => { socket.destroy(); resolve(false); });
    socket.connect(port, '127.0.0.1');
  });
}

function startBackendInPowerShell(){
  const mainBat = path.join(BACKEND_DIR, 'MAIN.bat');
  const psCmd = "chcp 65001 | Out-Null; cd '" + BACKEND_DIR.replace(/'/g, "''") + "'; & '" + mainBat.replace(/'/g, "''") + "'";
  // Use cmd.exe /c start to force a new console window (spawn from GUI Electron doesn't create one)
  // Must pass the whole command as a single string so cmd.exe parses "start" args correctly
  const child = spawn(
    process.env.comspec || 'cmd.exe',
    ['/c', 'start', '"FaustBot"', 'powershell.exe', '-NoExit', '-Command', psCmd],
    { cwd: BACKEND_DIR }
  );
  child.on('error', (e) => console.error('启动后端 PowerShell 失败', e));
  console.log('后端已在启动，等待就绪…');
}

function waitForBackend(maxAttempts = 40, interval = 3000) {
  return new Promise((resolve) => {
    let attempts = 0;
    const poll = () => {
      checkPort(BACKEND_PORT_TO_CHECK).then((open) => {
        if (open) { resolve(true); return; }
        attempts++;
        if (attempts >= maxAttempts) { resolve(false); return; }
        setTimeout(poll, interval);
      });
    };
    poll();
  });
}

app.whenReady().then(async () => {
  registerStaticProtocol();
  registerFaustProtocolClient();

  let open = await checkPort(BACKEND_PORT_TO_CHECK);
  if (!open) {
    startBackendInPowerShell();
    dialog.showMessageBox({
      type: 'info',
      title: '后端启动中',
      message: '后端服务正在启动，请稍候…\n\n后端将在新的 PowerShell 窗口中启动。待后端就绪后，主窗口将自动打开。',
      buttons: ['确定'],
    });
    open = await waitForBackend();
    if (!open) {
      dialog.showErrorBox('启动超时', 'CRITICAL:后端服务启动超时 (120s)');
      app.quit();
      return;
    }
  }

  _backendReady = true;
  createWindow();
  createTray();
  registerGlobalShortcuts();
  for (const arg of process.argv) {
    if (typeof arg === 'string' && arg.startsWith(`${FAUST_PROTOCOL}://`)) {
      queueDeepLinkUrl(arg);
    }
  }
  flushPendingDeepLinks();
  startCommandWS();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

ipcMain.handle('set-ignore-mouse-events', (evt, ignore) => {
  if (!mainWindow) return false;
  try{
    // forward: true allows mouse events to still be received by the window's webContents if needed
    mainWindow.setIgnoreMouseEvents(!!ignore, { forward: true });
    return true;
  }catch(e){
    console.error(e);
    return false;
  }
});

ipcMain.handle('focus-main-window', () => {
  if (!mainWindow) return false;
  try{
    mainWindow.setAlwaysOnTop(true, 'screen-saver');
    if (typeof mainWindow.focus === 'function') mainWindow.focus();
    return true;
  }catch(e){
    console.error('focus-main-window failed', e);
    return false;
  }
});

ipcMain.handle('hide-to-tray', () => {
  createTray();
  return hideMainWindowToTray();
});

ipcMain.handle('show-from-tray', () => {
  return showMainWindow();
});

ipcMain.handle('open-config-window', async () => {
  createConfigWindow();
  return { ok: true, mode: 'electron-window' };
});

ipcMain.handle('open-live-window', async () => {
  createLiveWindow();
  return { ok: true, mode: 'electron-window' };
});

ipcMain.handle('recreate-frontend-window', () => {
  recreateFrontendMainWindow();
  return { ok: true };
});

ipcMain.handle('config-api', async (event, req) => {
  const senderWindow = BrowserWindow.fromWebContents(event.sender);
  if (!senderWindow) {
    throw new Error('非法请求来源');
  }

  const method = String((req && req.method) || 'GET').toUpperCase();
  const pathValue = String((req && req.path) || '').trim();
  const payload = req ? req.payload : null;
  const query = req ? req.query : null;

  const allowMethods = new Set(['GET', 'POST', 'PUT', 'DELETE']);
  if (!allowMethods.has(method)) {
    throw new Error(`不支持的请求方法: ${method}`);
  }

  const fullUrl = buildBackendUrl(pathValue, query);
  return requestJson(method, fullUrl, payload);
});

ipcMain.handle('config-dialog-open-file', async (_event, options) => {
  const result = await dialog.showOpenDialog({
    title: String((options && options.title) || '选择文件'),
    filters: Array.isArray(options && options.filters) ? options.filters : undefined,
    properties: ['openFile'],
  });
  if (result.canceled || !result.filePaths || !result.filePaths.length) {
    return null;
  }
  return result.filePaths[0];
});

ipcMain.handle('config-dialog-open-directory', async (_event, options) => {
  const result = await dialog.showOpenDialog({
    title: String((options && options.title) || '选择目录'),
    properties: ['openDirectory'],
  });
  if (result.canceled || !result.filePaths || !result.filePaths.length) {
    return null;
  }
  return result.filePaths[0];
});

ipcMain.handle('config-open-path', async (_event, targetPath) => {
  const p = String(targetPath || '').trim();
  if (!p) {
    throw new Error('路径不能为空');
  }
  const openErr = await shell.openPath(p);
  return { ok: !openErr, error: openErr || null };
});

ipcMain.handle('get-faustbot-root', () => {
  return path.join(app.getPath('home'), '.faustbot');
});

ipcMain.handle('restart-faust', () => {
  const appPath = process.argv[0];
  const args = process.argv.slice(1);
  spawn(appPath, args, { detached: true, stdio: 'ignore' });
  app.quit();
  return { status: 'restarting' };
});

ipcMain.handle('toggle-log-panel', () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('toggle-log-panel');
    return true;
  }
  return false;
});

ipcMain.handle('config-http-request', async (_event, req) => {
  const method = String((req && req.method) || 'GET').toUpperCase();
  const rawUrl = String((req && req.url) || '').trim();
  const payload = req ? req.payload : null;
  const allowMethods = new Set(['GET', 'POST', 'PUT', 'DELETE']);
  if (!allowMethods.has(method)) {
    throw new Error(`不支持的请求方法: ${method}`);
  }
  if (!rawUrl) {
    throw new Error('URL 不能为空');
  }
  const parsed = new URL(rawUrl);
  if (!(parsed.hostname === '127.0.0.1' || parsed.hostname === 'localhost')) {
    throw new Error('仅允许请求本机服务');
  }
  return requestJson(method, parsed.toString(), payload);
});

// allow renderer to send log messages to main process console
ipcMain.handle('faust-log', async (evt, msg) => {
  try{
    console.log('[renderer]', String(msg));
  }catch(e){ console.error('faust-log failed', e); }
  return { ok: true };
});

let _isRecreating = false;
app.on('window-all-closed', ()=>{
  if (_isRecreating) return;
  if (process.platform !== 'darwin') app.quit()
});

app.on('will-quit', ()=>{
  try{ globalShortcut.unregisterAll(); }catch(e){ console.error('unregisterAll failed', e); }
  try{ if (tray) { tray.destroy(); tray = null; } }catch(e){ console.error('tray destroy failed', e); }
});

// Try to load a WebSocket implementation for the main process.
let WSImpl = null;
try {
  WSImpl = require('ws');
} catch (e) {
  console.warn('Package "ws" not found in main process. To enable main-process WebSocket, run `npm install ws` in the frontend folder.');
  WSImpl = null;
}

// WS client to receive commands from backend and forward to renderer
const commandBuffer = [];
let rendererReady = false;

function flushCommandBuffer(){
  rendererReady = true;
  while (commandBuffer.length) {
    const text = commandBuffer.shift();
    try{
      if (mainWindow && mainWindow.webContents) mainWindow.webContents.send('faust-command', text);
    }catch(e){
      console.error('Failed to forward faust command to renderer', e);
    }
  }
}

function startCommandWS(){
  if (!WSImpl){
    console.warn('WebSocket client not available in main process; faust commands will not be received. Install "ws" in frontend.');
    return;
  }
  const url = 'ws://127.0.0.1:13900/faust/command';
  let ws = null;
  let reconnectTimer = null;

  function doConnect(){
    try{
      //ws = new WSImpl(url, { headers: { Origin: 'http://127.0.0.1:13900' } });
      ws = new WSImpl(url);
    }catch(e){
      console.error('Failed to create WS client', e);
      scheduleReconnect();
      return;
    }

    ws.on('open', ()=>{
      console.log('[faust-ws] connected to', url);
    });

    ws.on('message', (data, isBinary) => {
      const text = decodeWsTextMessage(data, isBinary);
      console.log('[faust-ws] message:', text);
      if (rendererReady) {
        try{
          if (mainWindow && mainWindow.webContents) mainWindow.webContents.send('faust-command', text);
        }catch(e){
          console.error('Failed to forward faust command to renderer', e);
        }
      } else {
        commandBuffer.push(text);
      }
    });

    ws.on('close', (code, reason) => {
      console.warn('[faust-ws] closed', code, reason && reason.toString ? reason.toString() : reason);
      scheduleReconnect();
    });

    ws.on('error', (err) => {
      console.error('[faust-ws] error', err);
      // let close handler schedule reconnect
    });
  }

  function scheduleReconnect(){
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(()=>{
      reconnectTimer = null;
      doConnect();
    }, 2000);
  }

  doConnect();
}
