/**
 * main.js — Electron main process for Heaven's River Audiobook Reader
 */
const { app, BrowserWindow, Menu, ipcMain } = require('electron');
const path = require('path');

let win;

function createWindow() {
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    titleBarStyle: 'hiddenInset',   // macOS: traffic lights overlaid on app chrome
    vibrancy: 'under-window',       // macOS frosted-glass effect
    visualEffectState: 'active',
    backgroundColor: '#0d1117',
    title: "Heaven's River",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
      // Allow loading local audio files referenced relative to the HTML
      webSecurity: false,
    },
  });

  win.loadFile('reader.html');

  // Build a minimal app menu (keeps Cmd+Q, Cmd+C/V/X, fullscreen, etc.)
  const isMac = process.platform === 'darwin';
  const template = [
    ...(isMac ? [{ role: 'appMenu' }] : []),
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    { role: 'windowMenu' },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.whenReady().then(() => {
  createWindow();

  ipcMain.on('move-window', (_event, { dx, dy }) => {
    if (!win) return;
    const [x, y] = win.getPosition();
    win.setPosition(x + dx, y + dy);
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
