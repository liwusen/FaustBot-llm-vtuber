const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  setIgnoreMouseEvents: (v) => ipcRenderer.invoke('set-ignore-mouse-events', !!v),
  saveModelState: (state) => ipcRenderer.invoke('model-state-save', state),
  loadModelState: () => ipcRenderer.invoke('model-state-load')
  ,
  // Chat IPC wrappers: connect/disconnect/send and capability check
  chatConnect: (opts) => ipcRenderer.invoke('chat-connect', opts),
  chatDisconnect: () => ipcRenderer.invoke('chat-disconnect'),
  chatSend: (msg) => ipcRenderer.invoke('chat-send', msg),
  chatCapabilities: () => ipcRenderer.invoke('chat-capabilities'),
  // Event subscriptions from main: reply and status
  onChatReply: (cb) => {
    const listener = (_ev, payload) => cb(payload);
    ipcRenderer.on('chat-reply', listener);
    return () => ipcRenderer.removeListener('chat-reply', listener);
  },
  onChatStatus: (cb) => {
    const listener = (_ev, payload) => cb(payload);
    ipcRenderer.on('chat-status', listener);
    return () => ipcRenderer.removeListener('chat-status', listener);
  }
});
