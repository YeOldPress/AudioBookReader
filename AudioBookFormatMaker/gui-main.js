const { app, BrowserWindow, ipcMain, dialog } = require("electron");
const { spawn, spawnSync } = require("child_process");
const path = require("path");
const fs = require("fs");

let win;
let activeTask = null;
let cachedPython = undefined;
const lastPaths = new Map();

const APP_DIR = __dirname;
const ROOT_DIR = path.resolve(APP_DIR, "..");

// Reduce Chromium GPU timing noise in terminal logs on some Linux setups.
app.commandLine.appendSwitch("disable-gpu-vsync");

function createWindow() {
  win = new BrowserWindow({
    width: 1240,
    height: 840,
    minWidth: 980,
    minHeight: 700,
    titleBarStyle: "hiddenInset",
    backgroundColor: "#0a1215",
    title: "AudioBookFormat Maker",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(APP_DIR, "gui-preload.js"),
    },
  });

  win.loadFile(path.join(APP_DIR, "gui.html"));
}

function findPython() {
  if (cachedPython !== undefined) {
    return cachedPython;
  }

  if (process.env.PYTHON && process.env.PYTHON.trim()) {
    cachedPython = process.env.PYTHON.trim();
    return cachedPython;
  }

  for (const candidate of ["python3", "python"]) {
    const result = spawnSync(candidate, ["--version"], { stdio: "ignore" });
    if (result.status === 0) {
      cachedPython = candidate;
      return cachedPython;
    }
  }

  cachedPython = null;
  return cachedPython;
}

function runCommandCapture(command, args, options = {}) {
  return new Promise((resolve) => {
    const proc = spawn(command, args, {
      ...options,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";

    proc.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });

    proc.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });

    proc.on("error", (error) => {
      resolve({ status: 1, stdout, stderr: error.message || String(error) });
    });

    proc.on("close", (code) => {
      resolve({ status: code ?? 1, stdout, stderr });
    });
  });
}

async function getGpuDiagnostics() {
  const ffmpeg = await runCommandCapture("ffmpeg", ["-hide_banner", "-hwaccels"]);

  let ffmpegHwaccels = [];
  if (ffmpeg.status === 0) {
    const lines = (ffmpeg.stdout || "").split(/\r?\n/).map((s) => s.trim());
    let start = false;
    for (const ln of lines) {
      if (ln.includes("Hardware acceleration methods")) {
        start = true;
        continue;
      }
      if (start && ln) {
        ffmpegHwaccels.push(ln);
      }
    }
  }

  const python = findPython();
  const pyDiag = {
    pythonFound: Boolean(python),
    torchInstalled: false,
    torchVersion: null,
    cudaAvailable: false,
    cudaVersion: null,
    hipVersion: null,
    mpsAvailable: false,
    mlxWhisperInstalled: false,
    error: null,
  };

  if (python) {
    const code = [
      "import json",
      "d={'torchInstalled':False,'torchVersion':None,'cudaAvailable':False,'cudaVersion':None,'hipVersion':None,'mpsAvailable':False,'mlxWhisperInstalled':False,'error':None}",
      "try:",
      " import torch",
      " d['torchInstalled']=True",
      " d['torchVersion']=getattr(torch,'__version__',None)",
      " d['cudaAvailable']=bool(torch.cuda.is_available())",
      " d['cudaVersion']=getattr(getattr(torch,'version',None),'cuda',None)",
      " d['hipVersion']=getattr(getattr(torch,'version',None),'hip',None)",
      " d['mpsAvailable']=bool(hasattr(torch,'backends') and hasattr(torch.backends,'mps') and torch.backends.mps.is_available())",
      "except Exception as e:",
      " d['error']=str(e)",
      "try:",
      " import mlx_whisper",
      " d['mlxWhisperInstalled']=True",
      "except Exception:",
      " pass",
      "print(json.dumps(d))",
    ].join("\n");

    const probe = await runCommandCapture(python, ["-c", code]);
    if (probe.status === 0) {
      try {
        const parsed = JSON.parse((probe.stdout || "{}").trim());
        Object.assign(pyDiag, parsed);
      } catch (_err) {
        pyDiag.error = "Could not parse Python diagnostics output.";
      }
    } else {
      pyDiag.error = (probe.stderr || "Python diagnostics failed").trim();
    }
  }

  return {
    ffmpegFound: ffmpeg.status === 0,
    ffmpegHwaccels,
    python: pyDiag,
  };
}

function parseProgress(mode, line, state) {
  if (mode === "convert") {
    const totalMatch = line.match(/Chapters:\s*(\d+)/);
    if (totalMatch) {
      state.total = parseInt(totalMatch[1], 10);
    }
    if (/^\s*\[\d+\]/.test(line)) {
      state.current += 1;
    }
  }

  if (mode === "sync") {
    const withEpub = line.match(/Chapters with EPUB text\s*:\s*(\d+)/);
    if (withEpub) {
      state.total = parseInt(withEpub[1], 10);
    }

    const trackFilter = line.match(/After --tracks filter\s*:\s*(\d+)/);
    if (trackFilter) {
      state.total = parseInt(trackFilter[1], 10);
      state.current = 0;
    }

    const resumeFilter = line.match(/After --resume filter\s*:\s*(\d+)/);
    if (resumeFilter) {
      state.total = parseInt(resumeFilter[1], 10);
      state.current = 0;
    }

    if (/^\s*Track\s+\d+\s*:/.test(line)) {
      state.current += 1;
    }

    const tqdm = line.match(/(\d+)\/(\d+)/);
    if (tqdm) {
      state.current = parseInt(tqdm[1], 10);
      state.total = parseInt(tqdm[2], 10);
    }
  }

  if (mode === "sync" && win && !win.isDestroyed()) {
    let backend = null;
    if (/Using mlx-whisper/i.test(line)) {
      backend = { label: "MLX  (Apple Silicon GPU)", level: "good" };
    } else if (/Apple Silicon MPS detected/i.test(line)) {
      backend = { label: "MPS  (Apple Silicon GPU)", level: "good" };
    } else if (/AMD ROCm\/HIP detected/i.test(line)) {
      const m = line.match(/AMD ROCm\/HIP detected:\s*(.+)/);
      backend = { label: `ROCm GPU  — ${m ? m[1].trim() : "AMD"}`, level: "good" };
    } else if (/NVIDIA CUDA detected/i.test(line)) {
      const m = line.match(/NVIDIA CUDA detected:\s*(.+)/);
      backend = { label: `CUDA GPU  — ${m ? m[1].trim() : "NVIDIA"}`, level: "good" };
    } else if (/GPU detected through torch\.cuda/i.test(line)) {
      backend = { label: "CUDA GPU  (torch)", level: "good" };
    } else if (/Loading openai-whisper.*on (cuda|mps)/i.test(line)) {
      const m = line.match(/on (\S+?)…?$/);
      const dev = m ? m[1] : "GPU";
      backend = { label: `Whisper on ${dev}`, level: "good" };
    } else if (/Loading openai-whisper.*on cpu/i.test(line)) {
      backend = { label: "Whisper on CPU  (no GPU)", level: "warn" };
    } else if (/No GPU detected by PyTorch/i.test(line) || /Whisper will run on CPU/i.test(line)) {
      backend = { label: "CPU only  — no GPU found", level: "bad" };
    }
    if (backend) {
      win.webContents.send("afm:backend", backend);
    }
  }

  if (state.total > 0 && win && !win.isDestroyed()) {
    win.webContents.send("afm:progress", {
      current: Math.min(state.current, state.total),
      total: state.total,
      percent: Math.round((Math.min(state.current, state.total) / state.total) * 100),
    });
  }
}

function buildTaskCommand(payload) {
  const python = findPython();
  if (!python) {
    throw new Error("Could not find python3/python in PATH. Set PYTHON environment variable.");
  }

  const { taskType, options } = payload;
  let scriptName = "";
  const args = [];
  const env = { ...process.env };
  let mode = "sync";

  if (taskType === "convert") {
    mode = "convert";
    scriptName = "convert_m4b.py";
    args.push(options.inputFile, "--out", options.outputDir);
    if (options.author) {
      args.push("--author", options.author);
    }
    args.push("--start-track", String(options.startTrack || 1));
    args.push("--quality", String(options.quality || 2));
    if (options.bitrateKbps && Number(options.bitrateKbps) > 0) {
      args.push("--bitrate", String(options.bitrateKbps));
    }
    if (options.targetSizeMb && Number(options.targetSizeMb) > 0) {
      args.push("--target-size-mb", String(options.targetSizeMb));
    }
    args.push("--hwaccel", options.hwaccel === "off" ? "off" : "auto");
    if (options.listOnly) {
      args.push("--list");
    }
  } else if (taskType === "sync") {
    mode = "sync";
    const backend = options.backend || "auto";

    if (backend === "amd") {
      scriptName = "sync_audiobook_amd_rocm_v2.py";
      if (options.rocmCompat) {
        env.TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL = "1";
      }
      args.push("--device", "cuda");
    } else {
      scriptName = "sync_audiobook.py";
      if (backend === "nvidia") {
        args.push("--device", "cuda");
      } else if (backend === "apple") {
        args.push("--device", "mps");
      } else if (backend === "cpu") {
        args.push("--device", "cpu");
      }
    }

    args.push("--audio", options.audioDir);
    args.push("--epub", options.epubPath);
    args.push("--out", options.outputDir);
    args.push("--model", options.model);

    if (options.tracks && options.tracks.length > 0) {
      args.push("--tracks", ...options.tracks.map(String));
    }
    if (options.resume) {
      args.push("--resume");
    }
  } else {
    throw new Error(`Unknown task type: ${taskType}`);
  }

  const scriptPath = path.join(APP_DIR, scriptName);
  return { python, scriptPath, args, env, mode };
}

function terminateProcessTree(proc) {
  if (!proc || proc.exitCode !== null) {
    return false;
  }

  if (process.platform === "win32") {
    spawnSync("taskkill", ["/PID", String(proc.pid), "/T", "/F"], { stdio: "ignore" });
    return true;
  }

  try {
    // If process was spawned as detached, negative pid kills the full group.
    process.kill(-proc.pid, "SIGTERM");
  } catch (_err) {
    try {
      proc.kill("SIGTERM");
    } catch (_err2) {
      return false;
    }
  }

  setTimeout(() => {
    if (proc.exitCode === null) {
      try {
        process.kill(-proc.pid, "SIGKILL");
      } catch (_err) {
        try {
          proc.kill("SIGKILL");
        } catch (_err2) {
          // no-op
        }
      }
    }
  }, 1500);

  return true;
}

function normalizeDefaultPath(initialPath, kind) {
  const remembered = lastPaths.get(kind);
  const candidate = initialPath || remembered || ROOT_DIR;
  if (!candidate) {
    return ROOT_DIR;
  }

  try {
    const stat = fs.existsSync(candidate) ? fs.statSync(candidate) : null;
    if (stat && stat.isDirectory()) {
      return candidate;
    }
    return path.dirname(candidate);
  } catch (_err) {
    return ROOT_DIR;
  }
}

ipcMain.handle("afm:pick-path", async (_event, payload) => {
  const kind = typeof payload === "string" ? payload : payload?.kind;
  const initialPath = typeof payload === "string" ? undefined : payload?.initialPath;
  const common = {
    defaultPath: normalizeDefaultPath(initialPath, kind),
    properties: ["createDirectory"],
  };

  if (kind === "file") {
    const res = await dialog.showOpenDialog(win, {
      ...common,
      properties: ["openFile"],
      filters: [
        { name: "Audiobook / EPUB", extensions: ["m4b", "m4a", "aac", "mp4", "epub", "zip"] },
        { name: "All Files", extensions: ["*"] },
      ],
    });
    if (res.canceled) {
      return null;
    }
    lastPaths.set(kind, res.filePaths[0]);
    return res.filePaths[0];
  }

  if (kind === "epub-file") {
    const fileRes = await dialog.showOpenDialog(win, {
      ...common,
      properties: ["openFile"],
      filters: [
        { name: "EPUB", extensions: ["epub", "zip"] },
        { name: "All Files", extensions: ["*"] },
      ],
    });
    if (fileRes.canceled) {
      return null;
    }
    lastPaths.set(kind, fileRes.filePaths[0]);
    return fileRes.filePaths[0];
  }

  const dirRes = await dialog.showOpenDialog(win, {
    ...common,
    properties: ["openDirectory", "createDirectory"],
  });
  if (dirRes.canceled) {
    return null;
  }
  lastPaths.set(kind, dirRes.filePaths[0]);
  return dirRes.filePaths[0];
});

ipcMain.handle("afm:estimate-convert", async (_event, payload) => {
  const inputFile = payload?.inputFile;
  const bitrateKbps = Number(payload?.bitrateKbps || 0);
  const targetSizeMb = Number(payload?.targetSizeMb || 0);

  if (!inputFile) {
    return { ok: false, error: "No input file selected." };
  }

  const ffprobe = spawnSync(
    "ffprobe",
    [
      "-v",
      "quiet",
      "-print_format",
      "json",
      "-show_format",
      inputFile,
    ],
    { encoding: "utf8" }
  );

  if (ffprobe.status !== 0) {
    return { ok: false, error: "ffprobe not available or failed to read the file." };
  }

  let duration = 0;
  try {
    const parsed = JSON.parse(ffprobe.stdout || "{}");
    duration = Number(parsed?.format?.duration || 0);
  } catch (_err) {
    return { ok: false, error: "Could not parse ffprobe output." };
  }

  if (!duration || duration <= 0) {
    return { ok: false, error: "Unable to determine duration for this file." };
  }

  const chosenBitrate = bitrateKbps > 0 ? bitrateKbps : 64;
  const estimatedMb = (duration * chosenBitrate / 8.0) / 1024.0;

  let fitInfo = null;
  if (targetSizeMb > 0) {
    const fits = estimatedMb <= targetSizeMb;
    fitInfo = { targetSizeMb, fits };
  }

  return {
    ok: true,
    durationSeconds: duration,
    bitrateKbps: chosenBitrate,
    estimatedMb,
    fitInfo,
  };
});

ipcMain.handle("afm:get-diagnostics", async () => {
  return await getGpuDiagnostics();
});

ipcMain.handle("afm:start-task", async (_event, payload) => {
  if (activeTask && activeTask.proc && activeTask.proc.exitCode === null) {
    throw new Error("A task is already running.");
  }

  const { python, scriptPath, args, env, mode } = buildTaskCommand(payload);
  const proc = spawn(python, ["-u", scriptPath, ...args], {
    cwd: ROOT_DIR,
    env,
    stdio: ["ignore", "pipe", "pipe"],
    detached: process.platform !== "win32",
  });

  const task = { proc, stopRequested: false };
  const progressState = { current: 0, total: 0 };

  const emitLine = (line) => {
    if (win && !win.isDestroyed()) {
      win.webContents.send("afm:log", line);
    }
    parseProgress(mode, line, progressState);
  };

  proc.stdout.on("data", (chunk) => {
    const text = chunk.toString("utf8");
    for (const line of text.split(/\r?\n/)) {
      if (line.trim().length > 0) {
        emitLine(line);
      }
    }
  });

  proc.stderr.on("data", (chunk) => {
    const text = chunk.toString("utf8");
    for (const line of text.split(/\r?\n/)) {
      if (line.trim().length > 0) {
        emitLine(line);
      }
    }
  });

  proc.on("close", (code) => {
    if (win && !win.isDestroyed()) {
      const stopped = task.stopRequested;
      win.webContents.send("afm:done", {
        code: stopped ? 130 : (code ?? 1),
        stopped,
      });
    }
    activeTask = null;
  });

  activeTask = task;
  return { ok: true };
});

ipcMain.handle("afm:stop-task", async () => {
  if (activeTask && activeTask.proc && activeTask.proc.exitCode === null) {
    activeTask.stopRequested = true;
    const stopped = terminateProcessTree(activeTask.proc);
    return { stopped };
  }
  return { stopped: false };
});

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  if (activeTask && activeTask.proc && activeTask.proc.exitCode === null) {
    terminateProcessTree(activeTask.proc);
  }
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
