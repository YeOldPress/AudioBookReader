/**
 * main.js — Electron main process for Heaven's River Audiobook Reader
 */
const { app, BrowserWindow, Menu, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { execFileSync } = require('child_process');

let win;

function isDevicePath(p) {
  return typeof p === 'string' && p.startsWith('/dev/');
}

function listEpubCandidates(rootDir) {
  const candidates = [];
  const dirs = [rootDir, path.join(rootDir, 'ebook')];
  for (const dir of dirs) {
    if (!fs.existsSync(dir)) continue;
    let items = [];
    try {
      items = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const item of items) {
      if (item.isFile() && /\.epub$/i.test(item.name)) {
        candidates.push(path.join(dir, item.name));
      }
    }
  }
  return candidates;
}

function scoreCoverEntry(entry) {
  const name = entry.toLowerCase();
  if (!/\.(jpe?g|png|webp)$/i.test(name)) return -1;
  let score = 0;
  if (/cover/.test(name)) score += 80;
  if (/folder/.test(name)) score += 40;
  if (/front/.test(name)) score += 35;
  if (/images?\//.test(name)) score += 12;
  if (/thumb|icon|small/.test(name)) score -= 30;
  if (name.includes('svg')) score -= 40;
  return score;
}

function pickCoverEntry(entries) {
  let best = null;
  let bestScore = -1;
  for (const entry of entries) {
    const score = scoreCoverEntry(entry);
    if (score > bestScore) {
      best = entry;
      bestScore = score;
    }
  }
  return best;
}

function extractEpubCover(epubPath) {
  const listingRaw = execFileSync('unzip', ['-Z1', epubPath], {
    encoding: 'utf8',
    maxBuffer: 8 * 1024 * 1024,
  });
  const entries = listingRaw.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  const coverEntry = pickCoverEntry(entries);
  if (!coverEntry) {
    return null;
  }

  const coverBuffer = execFileSync('unzip', ['-p', epubPath, coverEntry], {
    encoding: 'buffer',
    maxBuffer: 24 * 1024 * 1024,
  });
  if (!coverBuffer || !coverBuffer.length) {
    return null;
  }

  const ext = (path.extname(coverEntry).toLowerCase() || '.jpg').replace(/[^.a-z0-9]/g, '');
  const stat = fs.statSync(epubPath);
  const key = `${epubPath}|${stat.mtimeMs}|${stat.size}|${coverEntry}`;
  const hash = crypto.createHash('sha1').update(key).digest('hex');
  const cacheDir = path.join(app.getPath('userData'), 'epub-covers');
  fs.mkdirSync(cacheDir, { recursive: true });
  const outPath = path.join(cacheDir, `${hash}${ext}`);
  if (!fs.existsSync(outPath)) {
    fs.writeFileSync(outPath, coverBuffer);
  }
  return outPath;
}

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

  win.loadFile(path.join(__dirname, 'reader.html'));

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

  ipcMain.handle('pick-media-root', async (_event, initialPath) => {
    const result = await dialog.showOpenDialog(win, {
      title: 'Select CD / media folder',
      defaultPath: (typeof initialPath === 'string' && initialPath.trim()) ? initialPath : app.getPath('home'),
      properties: ['openDirectory'],
    });

    if (result.canceled || !result.filePaths.length) {
      return { canceled: true };
    }

    let selected = result.filePaths[0];
    const base = path.basename(selected).toLowerCase();
    if (base === 'audio' || base === 'sync') {
      selected = path.dirname(selected);
    }

    if (isDevicePath(selected)) {
      return {
        canceled: false,
        error: 'Please choose the mounted CD directory, not a /dev device path.',
      };
    }

    const syncIndex = path.join(selected, 'sync', '_index.json');
    const audioDir = path.join(selected, 'audio');
    const valid = fs.existsSync(syncIndex) && fs.existsSync(audioDir) && fs.statSync(audioDir).isDirectory();
    if (!valid) {
      return {
        canceled: false,
        error: 'Selected folder must contain sync/_index.json and an audio folder.',
      };
    }

    return { canceled: false, path: selected };
  });

  ipcMain.handle('get-epub-cover', async (_event, mediaRoot) => {
    try {
      const root = typeof mediaRoot === 'string' ? mediaRoot.trim() : '';
      if (!root || isDevicePath(root) || !path.isAbsolute(root) || !fs.existsSync(root)) {
        return { ok: false, error: 'Invalid media root' };
      }

      const epubs = listEpubCandidates(root);
      if (!epubs.length) {
        return { ok: false, error: 'No EPUB found' };
      }

      const coverPath = extractEpubCover(epubs[0]);
      if (!coverPath) {
        return { ok: false, error: 'No cover image in EPUB' };
      }

      return { ok: true, path: coverPath };
    } catch (err) {
      return { ok: false, error: String(err?.message || err) };
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
