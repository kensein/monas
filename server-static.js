#!/usr/bin/env node
/**
 * Static frontend server — pola portal PSIMKG (subpath /monas).
 * PM2: monas-web
 */
const http = require("http");
const fs = require("fs");
const path = require("path");

const ROOT = __dirname;
const FRONTEND_DIR = path.join(ROOT, "frontend");
const PORT = parseInt(process.env.PORT || process.env.FRONTEND_PORT || "3013", 10);
const HOST = process.env.HOSTNAME || process.env.HOST || "127.0.0.1";
const BASE_PATH = (process.env.BASE_PATH || "/monas").replace(/\/$/, "");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
};

function stripBase(url) {
  const p = url.split("?")[0];
  if (p.startsWith(BASE_PATH)) {
    return p.slice(BASE_PATH.length) || "/";
  }
  return p;
}

function serveFile(res, filePath) {
  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404);
      res.end("Not found");
      return;
    }
    const ext = path.extname(filePath);
    res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
    res.end(data);
  });
}

const server = http.createServer((req, res) => {
  let rel = stripBase(req.url);
  if (rel === "/" || rel === "") rel = "/index.html";
  const filePath = path.join(FRONTEND_DIR, rel.replace(/^\//, ""));

  if (!filePath.startsWith(FRONTEND_DIR)) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }

  fs.stat(filePath, (err, stat) => {
    if (err || !stat.isFile()) {
      serveFile(res, path.join(FRONTEND_DIR, "index.html"));
      return;
    }
    serveFile(res, filePath);
  });
});

server.listen(PORT, HOST, () => {
  console.log(`Frontend: http://${HOST}:${PORT}${BASE_PATH}/`);
});
