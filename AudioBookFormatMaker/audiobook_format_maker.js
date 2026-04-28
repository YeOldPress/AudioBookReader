#!/usr/bin/env node
"use strict";

const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const APP_DIR = __dirname;
const ROOT_DIR = path.resolve(APP_DIR, "..");
const WORK_DIR = process.cwd();

const COMMAND_TO_SCRIPT = {
  convert: "convert_m4b.py",
  sync: "sync_audiobook.py",
  "sync-rocm": "sync_audiobook_amd_rocm.py",
  "sync-rocm-v2": "sync_audiobook_amd_rocm_v2.py",
  inspect: "inspect_epub.py",
};

function printHelp() {
  console.log(`AudioBookFormat Maker (Node.js)

Usage:
  node audiobook_format_maker.js <command> [args...]

Commands:
  gui            Launch the modern Electron GUI app
  convert        Convert M4B to chapter MP3 files
  sync           Build sync JSON with default backend
  sync-rocm      Build sync JSON using AMD ROCm variant
  sync-rocm-v2   Build sync JSON using AMD ROCm v2 variant
  inspect        Inspect EPUB XHTML structure

Examples:
  node audiobook_format_maker.js convert input.m4b --out ./audio
  node audiobook_format_maker.js sync --audio ./audio --epub ./ebook/book.epub --out ./sync --model medium
  node audiobook_format_maker.js sync --audio ./audio --epub ./ebook --out ./sync --model large --device cuda
  node audiobook_format_maker.js sync-rocm-v2 --audio ./audio --epub ./ebook --out ./sync --model medium --device cuda

Notes:
  - This Node launcher reuses the existing Python formatter engine.
  - Set PYTHON=/path/to/python if python3 is not on PATH.
`);
}

function findPython() {
  if (process.env.PYTHON && process.env.PYTHON.trim()) {
    return process.env.PYTHON.trim();
  }

  const candidates = ["python3", "python"];
  for (const cmd of candidates) {
    const result = spawnSync(cmd, ["--version"], { stdio: "ignore" });
    if (result.status === 0) {
      return cmd;
    }
  }

  return null;
}

function findElectron() {
  const localElectronInApp = path.join(APP_DIR, "node_modules", ".bin", process.platform === "win32" ? "electron.cmd" : "electron");
  if (fs.existsSync(localElectronInApp)) {
    return localElectronInApp;
  }

  const localElectronInRoot = path.join(ROOT_DIR, "node_modules", ".bin", process.platform === "win32" ? "electron.cmd" : "electron");
  if (fs.existsSync(localElectronInRoot)) {
    return localElectronInRoot;
  }

  const localElectron = path.join(WORK_DIR, "node_modules", ".bin", process.platform === "win32" ? "electron.cmd" : "electron");
  if (fs.existsSync(localElectron)) {
    return localElectron;
  }

  const globalResult = spawnSync("electron", ["--version"], { stdio: "ignore" });
  if (globalResult.status === 0) {
    return "electron";
  }

  return null;
}

function formatPercent(current, total) {
  if (!total || total <= 0) {
    return "";
  }
  const pct = Math.max(0, Math.min(100, Math.round((current / total) * 100)));
  return `${pct}%`;
}

function runWithProgress(pythonExe, scriptPath, scriptArgs) {
  let mode = "idle";
  if (path.basename(scriptPath) === "convert_m4b.py") {
    mode = "convert";
  }
  if (path.basename(scriptPath).startsWith("sync_audiobook")) {
    mode = "sync";
  }

  let total = 0;
  let current = 0;

  const child = spawn(pythonExe, ["-u", scriptPath, ...scriptArgs], {
    cwd: WORK_DIR,
    env: process.env,
    stdio: ["inherit", "pipe", "pipe"],
  });

  function updateProgressFromLine(line) {
    if (mode === "convert") {
      const totalMatch = line.match(/Chapters:\s*(\d+)/);
      if (totalMatch) {
        total = parseInt(totalMatch[1], 10);
      }
      if (/^\s*\[\d+\]/.test(line)) {
        current += 1;
      }
    }

    if (mode === "sync") {
      const withEpubMatch = line.match(/Chapters with EPUB text\s*:\s*(\d+)/);
      if (withEpubMatch) {
        total = parseInt(withEpubMatch[1], 10);
      }

      const trackFilterMatch = line.match(/After --tracks filter\s*:\s*(\d+)/);
      if (trackFilterMatch) {
        total = parseInt(trackFilterMatch[1], 10);
        current = 0;
      }

      const resumeMatch = line.match(/After --resume filter\s*:\s*(\d+)/);
      if (resumeMatch) {
        total = parseInt(resumeMatch[1], 10);
        current = 0;
      }

      if (/^\s*Track\s+\d+\s*:/.test(line)) {
        current += 1;
      }

      const tqdmMatch = line.match(/(\d+)\/(\d+)/);
      if (tqdmMatch) {
        current = parseInt(tqdmMatch[1], 10);
        total = parseInt(tqdmMatch[2], 10);
      }
    }

    if (total > 0) {
      process.stdout.write(`\r[progress] ${current}/${total} ${formatPercent(current, total)}    `);
    }
  }

  function handleStream(data) {
    const text = data.toString("utf8");
    process.stdout.write(text);
    const lines = text.split(/\r?\n/);
    for (const line of lines) {
      if (line.trim()) {
        updateProgressFromLine(line);
      }
    }
  }

  child.stdout.on("data", handleStream);
  child.stderr.on("data", (d) => process.stderr.write(d.toString("utf8")));

  child.on("close", (code) => {
    if (total > 0) {
      process.stdout.write("\n");
    }
    process.exit(code ?? 1);
  });
}

function runModernGui() {
  const electronExe = findElectron();
  if (!electronExe) {
    console.error("ERROR: Electron not found. Run 'npm install' first.");
    process.exit(1);
  }

  const guiMain = path.join(APP_DIR, "gui-main.js");
  const child = spawn(electronExe, [guiMain], {
    cwd: WORK_DIR,
    env: process.env,
    stdio: "inherit",
  });

  child.on("close", (code) => {
    process.exit(code ?? 0);
  });
}

function main() {
  const [, , command, ...restArgs] = process.argv;

  if (!command || command === "-h" || command === "--help") {
    printHelp();
    process.exit(0);
  }

  if (command === "gui") {
    runModernGui();
    return;
  }

  const script = COMMAND_TO_SCRIPT[command];
  if (!script) {
    console.error(`ERROR: Unknown command '${command}'.\n`);
    printHelp();
    process.exit(1);
  }

  const scriptPath = path.join(APP_DIR, script);
  if (!fs.existsSync(scriptPath)) {
    console.error(`ERROR: Missing script: ${scriptPath}`);
    process.exit(1);
  }

  const pythonExe = findPython();
  if (!pythonExe) {
    console.error("ERROR: Could not find python3/python in PATH. Set PYTHON=/path/to/python.");
    process.exit(1);
  }

  runWithProgress(pythonExe, scriptPath, restArgs);
}

main();
