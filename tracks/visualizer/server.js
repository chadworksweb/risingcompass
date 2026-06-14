/*
  rc-track-visualizer - a zero-dependency local dashboard for the RC track system.

  Shows, in real time, every parallel work track (git worktree on branch
  track/<name>): whether its backend/frontend stack is listening, its git state
  (branch, dirty working tree, ahead/behind origin/master) and its last commit.
  The main repo is shown the same way.

  Data sources, all read-only:
    - rc-tracks/registry.json   (the track -> slot/port/branch map)
    - netstat -ano              (which ports are currently LISTENING)
    - git -C <worktree> ...     (branch / status / ahead-behind / last commit)

  Run:  node tracks/visualizer/server.js   (then open http://127.0.0.1:4310)
  No build step, no npm install. Plain Node http + child_process.
*/

"use strict";

const http = require("http");
const fs = require("fs");
const path = require("path");
const { execFile, exec } = require("child_process");

// --- paths (mirror rc-track.ps1's layout discovery) -------------------------
const MAIN_REPO = path.resolve(__dirname, "..", "..");          // rising-compass/
const SITES_ROOT = path.resolve(MAIN_REPO, "..");               // Local Sites/
const TRACKS_ROOT = path.join(SITES_ROOT, "rc-tracks");
const REGISTRY = path.join(TRACKS_ROOT, "registry.json");
const PUBLIC_DIR = path.join(__dirname, "public");

const PORT = Number(process.env.RC_VIZ_PORT) || 4310;
const MAIN_BACKEND = 8000;
const MAIN_FRONTEND = 3005;

// --- small promise wrappers around child_process ----------------------------
function run(cmd, args, opts) {
  return new Promise((resolve) => {
    execFile(cmd, args, { windowsHide: true, ...opts }, (err, stdout) => {
      if (err) return resolve(null);
      resolve(String(stdout));
    });
  });
}

function shell(cmd) {
  return new Promise((resolve) => {
    exec(cmd, { windowsHide: true, maxBuffer: 4 * 1024 * 1024 }, (err, stdout) => {
      if (err) return resolve("");
      resolve(String(stdout));
    });
  });
}

// --- which TCP ports are currently listening --------------------------------
async function listeningPorts() {
  const out = await shell("netstat -ano -p tcp");
  const ports = new Set();
  for (const line of out.split(/\r?\n/)) {
    if (!line.includes("LISTENING")) continue;
    const parts = line.trim().split(/\s+/);
    const local = parts[1] || "";
    const port = Number(local.slice(local.lastIndexOf(":") + 1));
    if (port) ports.add(port);
  }
  return ports;
}

// --- git state for one worktree ---------------------------------------------
const US = ""; // unit separator, safe inside commit subjects

async function gitInfo(repoPath) {
  if (!fs.existsSync(repoPath)) return { exists: false };

  const [branch, porcelain, aheadBehind, last] = await Promise.all([
    run("git", ["-C", repoPath, "rev-parse", "--abbrev-ref", "HEAD"]),
    run("git", ["-C", repoPath, "status", "--porcelain"]),
    run("git", ["-C", repoPath, "rev-list", "--left-right", "--count", "origin/master...HEAD"]),
    run("git", ["-C", repoPath, "log", "-1", `--format=%h${US}%s${US}%cr`]),
  ]);

  if (branch === null) return { exists: true, isRepo: false };

  const dirtyLines = (porcelain || "").split(/\r?\n/).filter((l) => l.trim().length);
  let behind = null;
  let ahead = null;
  if (aheadBehind) {
    const m = aheadBehind.trim().split(/\s+/);
    behind = Number(m[0]);
    ahead = Number(m[1]);
  }

  let lastCommit = null;
  if (last) {
    const [hash, subject, when] = last.trim().split(US);
    lastCommit = { hash, subject, when };
  }

  return {
    exists: true,
    isRepo: true,
    branch: branch.trim(),
    dirty: dirtyLines.length,
    ahead,
    behind,
    lastCommit,
  };
}

// --- registry ----------------------------------------------------------------
function loadRegistry() {
  try {
    const raw = fs.readFileSync(REGISTRY, "utf8");
    if (!raw.trim()) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [parsed];
  } catch {
    return [];
  }
}

// --- assemble the full live state -------------------------------------------
async function buildState() {
  const ports = await listeningPorts();
  const registry = loadRegistry();

  const main = {
    name: "main",
    label: "rising-compass (main)",
    slot: 0,
    path: MAIN_REPO,
    backendPort: MAIN_BACKEND,
    frontendPort: MAIN_FRONTEND,
    backendUp: ports.has(MAIN_BACKEND),
    frontendUp: ports.has(MAIN_FRONTEND),
    ...(await gitInfo(MAIN_REPO)),
  };

  const tracks = await Promise.all(
    registry.map(async (t) => ({
      name: t.name,
      slot: t.slot,
      branch: t.branch,
      path: t.path,
      backendPort: t.backendPort,
      frontendPort: t.frontendPort,
      backendUp: ports.has(t.backendPort),
      frontendUp: ports.has(t.frontendPort),
      ...(await gitInfo(t.path)),
    }))
  );
  tracks.sort((a, b) => (a.slot || 0) - (b.slot || 0));

  // slot -> port reference (matches Ports-ForSlot in rc-track.ps1)
  const slotTable = [];
  for (let s = 1; s <= 8; s++) {
    slotTable.push({ slot: s, backend: 8000 + s * 10, frontend: 3005 + s * 10 });
  }

  return {
    ok: true,
    generatedAt: new Date().toISOString(),
    tracksRoot: TRACKS_ROOT,
    registryExists: fs.existsSync(REGISTRY),
    main,
    tracks,
    slotTable,
  };
}

// --- http server -------------------------------------------------------------
const server = http.createServer(async (req, res) => {
  const url = (req.url || "/").split("?")[0];

  if (url === "/api/state") {
    try {
      const state = await buildState();
      res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
      res.end(JSON.stringify(state));
    } catch (e) {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: false, error: String(e && e.message) }));
    }
    return;
  }

  if (url === "/favicon.ico") {
    const ico = path.join(__dirname, "rc-viz.ico");
    if (fs.existsSync(ico)) {
      res.writeHead(200, { "Content-Type": "image/x-icon", "Cache-Control": "max-age=86400" });
      res.end(fs.readFileSync(ico));
    } else {
      res.writeHead(204);
      res.end();
    }
    return;
  }

  // static: only index.html is served (single-file dashboard)
  const file = url === "/" ? "index.html" : url.replace(/^\/+/, "");
  const full = path.join(PUBLIC_DIR, file);
  if (!full.startsWith(PUBLIC_DIR) || !fs.existsSync(full)) {
    res.writeHead(404, { "Content-Type": "text/plain" });
    res.end("Not found");
    return;
  }
  const type = full.endsWith(".html") ? "text/html" : "text/plain";
  res.writeHead(200, { "Content-Type": type });
  res.end(fs.readFileSync(full));
});

server.on("error", (err) => {
  if (err && err.code === "EADDRINUSE") {
    console.log("");
    console.log("  RC Track Visualizer is already running on http://127.0.0.1:" + PORT);
    console.log("  (this window can be closed - the existing one keeps serving)");
    console.log("");
    process.exit(0);
  }
  throw err;
});

server.listen(PORT, "127.0.0.1", () => {
  console.log("");
  console.log("  RC Track Visualizer");
  console.log("  -------------------");
  console.log("  Dashboard:  http://127.0.0.1:" + PORT);
  console.log("  Main repo:  " + MAIN_REPO);
  console.log("  Tracks dir: " + TRACKS_ROOT);
  console.log("  Registry:   " + (fs.existsSync(REGISTRY) ? "found" : "none yet (no tracks created)"));
  console.log("");
  console.log("  Ctrl+C to stop.");
});
