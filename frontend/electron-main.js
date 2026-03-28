const { app, BrowserWindow, ipcMain, globalShortcut, Tray, Menu } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

let mainWindow = null;
let tray = null;

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
  { accelerator: 'CommandOrControl+Alt+Up', command: 'SCALE_UP' },
  { accelerator: 'CommandOrControl+Alt+Down', command: 'SCALE_DOWN' },
  { accelerator: 'CommandOrControl+Alt+M', command: 'RANDOM_MOTION' },
];

function sendFaustCommand(command) {
  if (!mainWindow || !mainWindow.webContents) return false;
  try {
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
  mainWindow = new BrowserWindow({
    width: 900,
    height: 700,
    fullscreen: true,
    fullscreenable: true,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    hasShadow: false,
    resizable: false,
    alwaysOnTop: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    }
  });

  const index = path.join(__dirname, 'index.html');
  mainWindow.loadFile(index);
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

function launchPySideConfiger(){
  const scriptPath = path.join(__dirname, 'configer_pyside6.py');
  if (!fs.existsSync(scriptPath)) {
    return { ok: false, error: `Configer 脚本不存在: ${scriptPath}` };
  }

  const candidates = [
    process.env.PYTHON ? { cmd: process.env.PYTHON, args: [scriptPath] } : null,
    { cmd: 'python', args: [scriptPath] },
    { cmd: 'py', args: ['-3', scriptPath] },
  ].filter(Boolean);

  let lastError = null;
  for (const c of candidates) {
    try {
      const child = spawn(c.cmd, c.args, {
        cwd: __dirname,
        detached: true,
        stdio: 'ignore',
        windowsHide: true,
      });
      child.unref();
      return { ok: true, launcher: c.cmd };
    } catch (e) {
      lastError = e;
    }
  }

  return { ok: false, error: String(lastError || '未找到可用 Python 解释器') };
}

function getTrayIconPath(){
  const candidates = [
    path.join(__dirname, '..', '..', 'live-2d', 'fake_neuro.ico'),
    path.join(__dirname, '..', '..', 'image', 'dmx1.png'),
  ];
  return candidates.find((candidate)=> fs.existsSync(candidate)) || null;
}

function showMainWindow(){
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
  tray.setToolTip('Faust Live2D');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '显示前端', click: ()=> showMainWindow() },
    { label: '打开配置中心(PySide6)', click: ()=> launchPySideConfiger() },
    { label: '隐藏到托盘', click: ()=> hideMainWindowToTray() },
    { type: 'separator' },
    { label: '退出', click: ()=> app.quit() },
  ]));
  tray.on('double-click', ()=>{ showMainWindow(); });
  return tray;
}

app.whenReady().then(()=>{
  createWindow();
  createTray();
  registerGlobalShortcuts();
  // Start WebSocket command client (main process)
  startCommandWS();
  app.on('activate', ()=>{ if (BrowserWindow.getAllWindows().length === 0) createWindow(); })
});

// model state file location in userData
const MODEL_STATE_FILE = path.join(app.getPath('userData'), 'faust_model_state.json');

ipcMain.handle('model-state-save', async (evt, state) => {
  try{
    await fs.promises.writeFile(MODEL_STATE_FILE, JSON.stringify(state, null, 2), 'utf8');
    return { ok: true };
  }catch(e){
    console.error('保存 model state 失败', e);
    return { ok: false, error: String(e) };
  }
});

ipcMain.handle('model-state-load', async () => {
  try{
    if (!fs.existsSync(MODEL_STATE_FILE)) return null;
    const raw = await fs.promises.readFile(MODEL_STATE_FILE, 'utf8');
    console.log('Loaded model state:', raw);
    return JSON.parse(raw);
  }catch(e){
    console.error('读取 model state 失败', e);
    return null;
  }
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

ipcMain.handle('open-config-window', () => {
  const result = launchPySideConfiger();
  if (!result.ok) {
    throw new Error(result.error || '打开 PySide6 Configer 失败');
  }
  return result;
});

// allow renderer to send log messages to main process console
ipcMain.handle('faust-log', async (evt, msg) => {
  try{
    console.log('[renderer]', String(msg));
  }catch(e){ console.error('faust-log failed', e); }
  return { ok: true };
});

app.on('window-all-closed', ()=>{ if (process.platform !== 'darwin') app.quit() });

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
      try{
        // forward raw text to renderer
        if (mainWindow && mainWindow.webContents) mainWindow.webContents.send('faust-command', text);
      }catch(e){
        console.error('Failed to forward faust command to renderer', e);
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
