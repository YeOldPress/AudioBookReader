const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("afm", {
  pickPath: (payload) => ipcRenderer.invoke("afm:pick-path", payload),
  estimateConvert: (payload) => ipcRenderer.invoke("afm:estimate-convert", payload),
  getDiagnostics: () => ipcRenderer.invoke("afm:get-diagnostics"),
  startTask: (payload) => ipcRenderer.invoke("afm:start-task", payload),
  stopTask: () => ipcRenderer.invoke("afm:stop-task"),
  onLog: (cb) => ipcRenderer.on("afm:log", (_event, line) => cb(line)),
  onProgress: (cb) => ipcRenderer.on("afm:progress", (_event, progress) => cb(progress)),
  onDone: (cb) => ipcRenderer.on("afm:done", (_event, result) => cb(result)),
  onBackend: (cb) => ipcRenderer.on("afm:backend", (_event, info) => cb(info)),
});
