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

// --- Chat WebSocket in main process (optional, falls back to renderer if 'ws' not installed) --
//保持前台