const els = {
  tabConvert: document.getElementById("tab-convert"),
  tabSync: document.getElementById("tab-sync"),
  viewConvert: document.getElementById("view-convert"),
  viewSync: document.getElementById("view-sync"),

  convertInput: document.getElementById("convert-input"),
  convertOutput: document.getElementById("convert-output"),
  convertAuthor: document.getElementById("convert-author"),
  convertTrack: document.getElementById("convert-track"),
  convertQuality: document.getElementById("convert-quality"),
  convertBitrate: document.getElementById("convert-bitrate"),
  convertFitCdr: document.getElementById("convert-fit-cdr"),
  convertHwaccel: document.getElementById("convert-hwaccel"),
  convertListOnly: document.getElementById("convert-list-only"),
  convertEstimate: document.getElementById("convert-estimate"),

  syncAudio: document.getElementById("sync-audio"),
  syncEpub: document.getElementById("sync-epub"),
  syncOutput: document.getElementById("sync-output"),
  syncModel: document.getElementById("sync-model"),
  syncBackend: document.getElementById("sync-backend"),
  syncTracks: document.getElementById("sync-tracks"),
  syncResume: document.getElementById("sync-resume"),
  syncRocm: document.getElementById("sync-rocm"),

  browseConvertInput: document.getElementById("browse-convert-input"),
  browseConvertOutput: document.getElementById("browse-convert-output"),
  browseSyncAudio: document.getElementById("browse-sync-audio"),
  browseSyncEpubFile: document.getElementById("browse-sync-epub-file"),
  browseSyncEpubDir: document.getElementById("browse-sync-epub-dir"),
  browseSyncOutput: document.getElementById("browse-sync-output"),

  runConvert: document.getElementById("run-convert"),
  runSync: document.getElementById("run-sync"),
  stopTask: document.getElementById("stop-task"),

  statusText: document.getElementById("status-text"),
  progressCount: document.getElementById("progress-count"),
  progressPercent: document.getElementById("progress-percent"),
  progressFill: document.getElementById("progress-fill"),
  refreshDiag: document.getElementById("refresh-diag"),
  diagContent: document.getElementById("diag-content"),
  log: document.getElementById("log"),
  backendBadge: document.getElementById("backend-badge"),
};

let running = false;
let stopRequested = false;
let estimateTimer = null;

function setActiveTab(which) {
  const convertActive = which === "convert";
  els.tabConvert.classList.toggle("active", convertActive);
  els.tabSync.classList.toggle("active", !convertActive);
  els.viewConvert.classList.toggle("active", convertActive);
  els.viewSync.classList.toggle("active", !convertActive);
}

function setRunning(isRunning) {
  running = isRunning;
  els.runConvert.disabled = isRunning;
  els.runSync.disabled = isRunning;
  els.stopTask.disabled = !isRunning;
}

function setStatus(text) {
  els.statusText.textContent = text;
}

function setProgress(current, total) {
  const safeTotal = total > 0 ? total : 0;
  const safeCurrent = Math.max(0, Math.min(current, safeTotal || current));
  const percent = safeTotal > 0 ? Math.round((safeCurrent / safeTotal) * 100) : 0;
  els.progressCount.textContent = `${safeCurrent} / ${safeTotal}`;
  els.progressPercent.textContent = `${percent}%`;
  els.progressFill.style.width = `${percent}%`;
}

function resetProgress() {
  setProgress(0, 0);
}

function appendLog(line) {
  els.log.textContent += `${line}\n`;
  els.log.scrollTop = els.log.scrollHeight;
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function diagRow(key, value, level) {
  return `
    <div class="diag-row">
      <span class="diag-key">${escapeHtml(key)}</span>
      <span class="diag-value ${level}">${escapeHtml(value)}</span>
    </div>
  `;
}

function renderDiagnostics(diag) {
  const rows = [];

  rows.push(diagRow("ffmpeg", diag.ffmpegFound ? "found" : "not found", diag.ffmpegFound ? "diag-good" : "diag-bad"));
  if (diag.ffmpegHwaccels && diag.ffmpegHwaccels.length > 0) {
    rows.push(diagRow("ffmpeg hwaccels", diag.ffmpegHwaccels.join(", "), "diag-good"));
  } else {
    rows.push(diagRow("ffmpeg hwaccels", "none detected", "diag-warn"));
  }

  const py = diag.python || {};
  rows.push(diagRow("python", py.pythonFound ? "found" : "not found", py.pythonFound ? "diag-good" : "diag-bad"));
  rows.push(diagRow("torch", py.torchInstalled ? `yes (${py.torchVersion || "unknown"})` : "no", py.torchInstalled ? "diag-good" : "diag-warn"));
  rows.push(diagRow("cuda available", py.cudaAvailable ? "yes" : "no", py.cudaAvailable ? "diag-good" : "diag-muted"));
  rows.push(diagRow("cuda version", py.cudaVersion || "n/a", py.cudaVersion ? "diag-good" : "diag-muted"));
  rows.push(diagRow("rocm/hip version", py.hipVersion || "n/a", py.hipVersion ? "diag-good" : "diag-muted"));
  rows.push(diagRow("mps available", py.mpsAvailable ? "yes" : "no", py.mpsAvailable ? "diag-good" : "diag-muted"));
  rows.push(diagRow("mlx-whisper", py.mlxWhisperInstalled ? "yes" : "no", py.mlxWhisperInstalled ? "diag-good" : "diag-warn"));
  if (py.error) {
    rows.push(diagRow("python probe note", py.error, "diag-warn"));
  }

  els.diagContent.innerHTML = rows.join("");
}

async function refreshDiagnostics() {
  els.diagContent.textContent = "Refreshing diagnostics...";
  try {
    const diag = await window.afm.getDiagnostics();
    renderDiagnostics(diag);
  } catch (err) {
    els.diagContent.textContent = `Diagnostics failed: ${err.message || String(err)}`;
  }
}

async function refreshConvertEstimate() {
  const inputFile = els.convertInput.value.trim();
  if (!inputFile) {
    els.convertEstimate.textContent = "Estimate: select input file.";
    return;
  }

  let bitrateKbps = Number.parseInt(els.convertBitrate.value, 10);
  if (!Number.isFinite(bitrateKbps) || bitrateKbps <= 0) {
    // Derive expected bitrate from VBR quality setting (mirrors Python quality_to_estimated_kbps)
    const qualityTable = { 0:245, 1:225, 2:190, 3:175, 4:165, 5:130, 6:115, 7:100, 8:85, 9:65 };
    const q = Number.parseInt(els.convertQuality.value, 10);
    bitrateKbps = qualityTable[Math.max(0, Math.min(9, Number.isFinite(q) ? q : 2))] ?? 190;
  }

  const targetSizeMb = els.convertFitCdr.checked ? 650 : 0;

  try {
    const res = await window.afm.estimateConvert({ inputFile, bitrateKbps, targetSizeMb });
    if (!res.ok) {
      els.convertEstimate.textContent = `Estimate unavailable: ${res.error}`;
      return;
    }

    const mins = Math.round((res.durationSeconds || 0) / 60);
    let text = `Estimate: ~${res.estimatedMb.toFixed(1)} MB at ${res.bitrateKbps} kbps mono (${mins} min).`;
    if (res.fitInfo) {
      text += ` CD-R fit: ${res.fitInfo.fits ? "yes" : "no"}.`;
    }
    els.convertEstimate.textContent = text;
  } catch (err) {
    els.convertEstimate.textContent = `Estimate unavailable: ${err.message || String(err)}`;
  }
}

function parseTracks(trackText) {
  if (!trackText.trim()) {
    return [];
  }
  const tokens = trackText.trim().split(/\s+/);
  if (!tokens.every((t) => /^\d+$/.test(t))) {
    throw new Error("Tracks must be space-separated integers, e.g. 3 4 5");
  }
  return tokens.map((x) => parseInt(x, 10));
}

async function browse(kind, targetEl) {
  setStatus("Opening picker...");
  const picked = await window.afm.pickPath({
    kind,
    initialPath: targetEl.value.trim(),
  });
  if (picked) {
    targetEl.value = picked;
  }
  setStatus(running ? "Running" : "Ready");
}

function scheduleConvertEstimate() {
  if (estimateTimer) {
    window.clearTimeout(estimateTimer);
  }
  estimateTimer = window.setTimeout(() => {
    refreshConvertEstimate();
  }, 180);
}

async function startConvert() {
  const inputFile = els.convertInput.value.trim();
  const outputDir = els.convertOutput.value.trim();
  const author = els.convertAuthor.value.trim();
  const startTrack = Number.parseInt(els.convertTrack.value, 10);
  const quality = Number.parseInt(els.convertQuality.value, 10);
  const bitrate = Number.parseInt(els.convertBitrate.value, 10);

  if (!inputFile || !outputDir) {
    window.alert("Convert requires input file and output folder.");
    return;
  }
  if (!Number.isFinite(startTrack) || startTrack < 1) {
    window.alert("Start track must be an integer >= 1.");
    return;
  }
  if (!Number.isFinite(quality) || quality < 0 || quality > 9) {
    window.alert("Quality must be 0-9.");
    return;
  }
  if (!Number.isFinite(bitrate) || bitrate < 32 || bitrate > 320) {
    window.alert("Bitrate must be 32-320 kbps.");
    return;
  }

  const targetSizeMb = els.convertFitCdr.checked ? 650 : 0;

  await startTask({
    taskType: "convert",
    options: {
      inputFile,
      outputDir,
      author,
      startTrack,
      quality,
      bitrateKbps: bitrate,
      targetSizeMb,
      hwaccel: els.convertHwaccel.checked ? "auto" : "off",
      listOnly: els.convertListOnly.checked,
    },
  });
}

async function startSync() {
  const audioDir = els.syncAudio.value.trim();
  const epubPath = els.syncEpub.value.trim();
  const outputDir = els.syncOutput.value.trim();
  const model = els.syncModel.value;
  const backend = els.syncBackend.value;

  if (!audioDir || !epubPath || !outputDir) {
    window.alert("Sync requires audio folder, EPUB path, and output folder.");
    return;
  }

  let tracks = [];
  try {
    tracks = parseTracks(els.syncTracks.value);
  } catch (err) {
    window.alert(err.message);
    return;
  }

  await startTask({
    taskType: "sync",
    options: {
      audioDir,
      epubPath,
      outputDir,
      model,
      backend,
      tracks,
      resume: els.syncResume.checked,
      rocmCompat: els.syncRocm.checked,
    },
  });
}

async function startTask(payload) {
  if (running) {
    return;
  }

  resetProgress();
  stopRequested = false;
  setBackend(null);
  setStatus("Running");
  setRunning(true);
  appendLog(`$ Starting ${payload.taskType} task...`);

  try {
    await window.afm.startTask(payload);
  } catch (err) {
    appendLog(`ERROR: ${err.message || String(err)}`);
    setStatus("Failed to start");
    setRunning(false);
  }
}

els.tabConvert.addEventListener("click", () => setActiveTab("convert"));
els.tabSync.addEventListener("click", () => setActiveTab("sync"));

els.browseConvertInput.addEventListener("click", () => browse("file", els.convertInput).then(() => scheduleConvertEstimate()));
els.browseConvertOutput.addEventListener("click", () => browse("dir", els.convertOutput));
els.browseSyncAudio.addEventListener("click", () => browse("dir", els.syncAudio));
els.browseSyncEpubFile.addEventListener("click", () => browse("epub-file", els.syncEpub));
els.browseSyncEpubDir.addEventListener("click", () => browse("dir", els.syncEpub));
els.browseSyncOutput.addEventListener("click", () => browse("dir", els.syncOutput));

els.runConvert.addEventListener("click", () => startConvert());
els.runSync.addEventListener("click", () => startSync());
els.stopTask.addEventListener("click", async () => {
  stopRequested = true;
  const res = await window.afm.stopTask();
  if (res && res.stopped) {
    setStatus("Stopping...");
  } else {
    setStatus("Stop requested (task already ending)");
  }
});
els.refreshDiag.addEventListener("click", () => refreshDiagnostics());

function setBackend(info) {
  const el = els.backendBadge;
  if (!info) {
    el.textContent = "";
    el.className = "backend-badge backend-hidden";
    return;
  }
  el.textContent = info.label;
  el.className = `backend-badge backend-${info.level}`;
}

window.afm.onLog((line) => appendLog(line));
window.afm.onProgress((p) => setProgress(p.current || 0, p.total || 0));
window.afm.onBackend((info) => setBackend(info));
window.afm.onDone(({ code, stopped }) => {
  if (stopped || stopRequested || code === 130 || code === 137 || code === 143) {
    setStatus("Stopped");
  } else if (code === 0) {
    setStatus("Completed");
  } else {
    setStatus(`Failed (exit ${code})`);
  }
  stopRequested = false;
  setRunning(false);
});

els.convertInput.addEventListener("change", scheduleConvertEstimate);
els.convertBitrate.addEventListener("input", scheduleConvertEstimate);
els.convertFitCdr.addEventListener("change", scheduleConvertEstimate);

setActiveTab("convert");
setProgress(0, 0);
setStatus("Ready");
appendLog("AudioBookFormat Maker ready.");
window.setTimeout(() => {
  refreshDiagnostics();
}, 250);
