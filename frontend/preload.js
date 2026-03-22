const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  setIgnoreMouseEvents: (v) => ipcRenderer.invoke('set-ignore-mouse-events', !!v),
  saveModelState: (state) => ipcRenderer.invoke('model-state-save', state),
  loadModelState: () => ipcRenderer.invoke('model-state-load'),
  focusMainWindow: () => ipcRenderer.invoke('focus-main-window')
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
  }
});

// allow renderer to send logs to main process console
contextBridge.exposeInMainWorld('logToMain', {
  info: (msg) => ipcRenderer.invoke('faust-log', String(msg)).catch(()=>{}),
});
