const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const PID_FILE = path.join(ROOT, "storage", "server.pid");
const LOG_FILE = path.join(ROOT, "storage", "server.log");
const PORT = Number(process.env.PORT || 8090);

const pid = readPid(PID_FILE);
if (!pid) {
  console.log("WebApp server status: stopped");
  process.exit(0);
}

if (!isPidAlive(pid)) {
  console.log(`WebApp server status: stopped (stale pid ${pid})`);
  process.exit(0);
}

console.log(`WebApp server status: running (pid ${pid})`);
console.log(`URL: http://localhost:${PORT}`);
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
