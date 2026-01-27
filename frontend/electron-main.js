const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');

let mainWindow = null;

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
}

app.whenReady().then(()=>{
  createWindow();
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

app.on('window-all-closed', ()=>{ if (process.platform !== 'darwin') app.quit() });

// Try to load a WebSocket implementation for the main process.
let WSImpl = null;
try {
  WSImpl = require('ws');
} catch (e) {
  console.warn('Package "ws" not found in main process. To enable main-process WebSocket, run `npm install ws` in the frontend folder.');
  WSImpl = null;
}

// --- Chat WebSocket in main process (optional, falls back to renderer if 'ws' not installed) ---
class ChatWS {
  constructor() {
    this.ws = null;
    this.url = null;
    this.connected = false;
    this.queue = [];
    this.reconnectDelay = 1000;
    this.manualClose = false;
  }

  connect(url) {
    this.url = url;
    if (!WSImpl) {
      console.error('WebSocket implementation not available in main process (ws package missing)');
      return Promise.reject(new Error('ws-not-installed'));
    }

    return new Promise((resolve, reject) => {
      try {
        this.manualClose = false;
        // Prefer IPv4 loopback when 'localhost' is present because on some
        // Windows setups 'localhost' resolves to ::1 (IPv6) while the
        // backend may be listening only on IPv4. Replace 'localhost' with
        // explicit 127.0.0.1 to avoid ECONNREFUSED to ::1.
        let connectUrl = url.replace('ws://localhost', 'ws://127.0.0.1').replace('wss://localhost', 'wss://127.0.0.1');
        console.log('[main->ws] connecting to', connectUrl);
        this.ws = new WSImpl(connectUrl);
        this.ws.on('open', () => {
          this.connected = true;
          console.log('[main->ws] connected', url);
          if (mainWindow && mainWindow.webContents) mainWindow.webContents.send('chat-status', { connected: true });
          // flush queue
          while (this.queue.length > 0) {
            const msg = this.queue.shift();
            this.send(msg).catch(()=>{});
          }
          resolve();
        });

        this.ws.on('message', (data) => {
          try {
            const text = data.toString();
            if (mainWindow && mainWindow.webContents) mainWindow.webContents.send('chat-reply', text);
          } catch (e) { console.error(e); }
        });

        this.ws.on('close', (code, reason) => {
          this.connected = false;
          console.log('[main->ws] closed', code, reason && reason.toString());
          if (mainWindow && mainWindow.webContents) mainWindow.webContents.send('chat-status', { connected: false, code });
          if (!this.manualClose) {
            setTimeout(()=>{ this.connect(this.url).catch(()=>{}); }, this.reconnectDelay);
          }
        });

        this.ws.on('error', (err) => { console.error('[main->ws] error', err); });
      } catch (err) {
        reject(err);
      }
    });
  }

  disconnect() {
    this.manualClose = true;
    if (this.ws) {
      try { this.ws.close(); } catch (e) {}
    }
    this.ws = null;
    this.connected = false;
    if (mainWindow && mainWindow.webContents) mainWindow.webContents.send('chat-status', { connected: false });
  }

  async send(text) {
    if (!this.ws || !this.connected) {
      // queue until connected
      this.queue.push(text);
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      try {
        this.ws.send(text, (err) => {
          if (err) return reject(err);
          resolve();
        });
      } catch (e) { reject(e); }
    });
  }
}

const chatWS = new ChatWS();

ipcMain.handle('chat-connect', async (evt, { url }) => {
  if (!WSImpl) return { ok: false, error: 'ws-not-installed' };
  try {
    await chatWS.connect(url);
    return { ok: true };
  } catch (e) {
    console.error('chat-connect failed', e);
    return { ok: false, error: String(e) };
  }
});

ipcMain.handle('chat-disconnect', async () => {
  chatWS.disconnect();
  return { ok: true };
});

ipcMain.handle('chat-send', async (_evt, { text }) => {
  try {
    await chatWS.send(text);
    return { ok: true };
  } catch (e) {
    console.error('chat-send error', e);
    return { ok: false, error: String(e) };
  }
});

// If the renderer wants to know whether main has ws ability
ipcMain.handle('chat-capabilities', () => ({ wsImplemented: !!WSImpl }));

//保持前台