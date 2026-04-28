#!/usr/bin/env node
/**
 * launch.js — Start the Heaven's River audiobook reader in your browser.
 *
 * Usage:  node launch.js
 *         node launch.js --port 8080
 */

const http = require("http");
const fs   = require("fs");
const path = require("path");

const args = process.argv.slice(2);
const portArg = args.indexOf("--port");
const parsedPort = portArg !== -1 ? parseInt(args[portArg + 1], 10) : parseInt(args[0], 10);
const PORT = Number.isFinite(parsedPort) && parsedPort > 0 ? parsedPort : 8765;
const ROOT = path.resolve(__dirname, "..");
const URL  = `http://localhost:${PORT}/ReaderApp/reader.html`;

const MIME = {
  ".html": "text/html",
  ".js":   "application/javascript",
  ".css":  "text/css",
  ".json": "application/json",
  ".mp3":  "audio/mpeg",
  ".png":  "image/png",
  ".ico":  "image/x-icon",
};

const server = http.createServer((req, res) => {
  // Strip query string, decode URI, prevent path traversal
  let urlPath = decodeURIComponent(req.url.split("?")[0]);
  if (urlPath === "/") urlPath = "/ReaderApp/reader.html";
  const filePath = path.join(ROOT, urlPath);

  // Block traversal outside ROOT
  if (!filePath.startsWith(ROOT + path.sep) && filePath !== ROOT) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }

  fs.stat(filePath, (err, stat) => {
    if (err || !stat.isFile()) {
      res.writeHead(404);
      res.end("Not found");
      return;
    }
    const ext  = path.extname(filePath).toLowerCase();
    const mime = MIME[ext] || "application/octet-stream";
    res.writeHead(200, { "Content-Type": mime, "Content-Length": stat.size });
    fs.createReadStream(filePath).pipe(res);
  });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log("");
  console.log("  Heaven's River Reader");
  console.log("  ───────────────────────────────");
  console.log(`  Serving from : ${ROOT}`);
  console.log(`  Open in browser: ${URL}`);
  console.log("");
  console.log("  Press Ctrl+C to stop.");
  console.log("");

  // Open browser after server is listening
  const { execSync } = require("child_process");
  const open =
    process.platform === "win32"  ? `start "" "${URL}"` :
    process.platform === "darwin" ? `open "${URL}"` :
                                    `xdg-open "${URL}"`;
  try { execSync(open); } catch (_) {}
});

process.on("SIGINT", () => {
  console.log("\n  Server stopped.");
  process.exit(0);
});
