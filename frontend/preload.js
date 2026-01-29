const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  setIgnoreMouseEvents: (v) => ipcRenderer.invoke('set-ignore-mouse-events', !!v),
  saveModelState: (state) => ipcRenderer.invoke('model-state-save', state),
  loadModelState: () => ipcRenderer.invoke('model-state-load')
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
