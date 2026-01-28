const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  setIgnoreMouseEvents: (v) => ipcRenderer.invoke('set-ignore-mouse-events', !!v),
  saveModelState: (state) => ipcRenderer.invoke('model-state-save', state),
  loadModelState: () => ipcRenderer.invoke('model-state-load')
});
