const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const ROOT = path.join(__dirname, "..");
const STORAGE_DIR = path.join(ROOT, "storage");
const PID_FILE = path.join(STORAGE_DIR, "server.pid");
const LOG_FILE = path.join(STORAGE_DIR, "server.log");
const PORT = Number(process.env.PORT || 8090);

fs.mkdirSync(STORAGE_DIR, { recursive: true });

const existingPid = readPid(PID_FILE);
if (existingPid && isPidAlive(existingPid)) {
  console.log(`WebApp server is already running (pid ${existingPid}) at http://localhost:${PORT}`);
  process.exit(0);
}

if (existingPid) {
  fs.rmSync(PID_FILE, { force: true });
}

const outFd = fs.openSync(LOG_FILE, "a");
const child = spawn(process.execPath, ["server.js"], {
  cwd: ROOT,
  detached: true,
  stdio: ["ignore", outFd, outFd],
  env: process.env,
});

child.unref();
fs.writeFileSync(PID_FILE, String(child.pid), "utf8");

console.log(`Started WebApp server (pid ${child.pid}) at http://localhost:${PORT}`);
console.log(`Log file: ${LOG_FILE}`);

function readPid(filePath) {
  try {
    const raw = fs.readFileSync(filePath, "utf8").trim();
    const value = Number(raw);
    return Number.isInteger(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

function isPidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
