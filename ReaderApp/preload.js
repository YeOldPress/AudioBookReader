const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  moveWindow: (dx, dy) => ipcRenderer.send('move-window', { dx, dy }),
  pickMediaRoot: (initialPath) => ipcRenderer.invoke('pick-media-root', initialPath || ''),
  getEpubCover: (mediaRoot) => ipcRenderer.invoke('get-epub-cover', mediaRoot || ''),
});
