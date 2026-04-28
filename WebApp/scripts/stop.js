const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const PID_FILE = path.join(ROOT, "storage", "server.pid");

const pid = readPid(PID_FILE);
if (!pid) {
  console.log("WebApp server is not running (no pid file). ");
  process.exit(0);
}

if (!isPidAlive(pid)) {
  fs.rmSync(PID_FILE, { force: true });
  console.log(`Removed stale pid file (${pid}).`);
  process.exit(0);
}

try {
  process.kill(pid, "SIGTERM");
} catch (err) {
  console.error(`Failed to stop pid ${pid}: ${err.message}`);
  process.exit(1);
}

waitForExit(pid, 3000)
  .then((exited) => {
    if (!exited) {
      process.kill(pid, "SIGKILL");
      return waitForExit(pid, 1000);
    }
    return true;
  })
  .then(() => {
    fs.rmSync(PID_FILE, { force: true });
    console.log(`Stopped WebApp server (pid ${pid}).`);
  })
  .catch((err) => {
    console.error(`Error stopping pid ${pid}: ${err.message}`);
    process.exit(1);
  });

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

function waitForExit(pid, timeoutMs) {
  const started = Date.now();
  return new Promise((resolve) => {
    const timer = setInterval(() => {
      if (!isPidAlive(pid)) {
        clearInterval(timer);
        resolve(true);
        return;
      }

      if (Date.now() - started >= timeoutMs) {
        clearInterval(timer);
        resolve(false);
      }
    }, 120);
  });
}
