/**
 * Cross-platform dev runner: API + web UI.
 * Uses `python -m` so uvicorn works without being on PATH (common on Windows).
 */
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import path from "node:path";
import {
  ensureGraphvizForDiagrams,
  isPortAvailable,
  platform,
  pythonInstallEnv,
  resolvePython,
  tryFreePort,
} from "./lib.mjs";

const require = createRequire(import.meta.url);
const treeKill = require("tree-kill");

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const isWin = platform.isWin;
const python = resolvePython(root);
const defaultEnv = pythonInstallEnv(process.env, root);

ensureGraphvizForDiagrams(root);

const children = [];

function run(name, cmd, args, cwd = root, env = defaultEnv) {
  const child = spawn(cmd, args, {
    cwd,
    stdio: "inherit",
    shell: isWin,
    env,
  });
  child.on("exit", (code) => {
    if (code !== 0 && code !== null) {
      console.error(`[${name}] exited with code ${code}`);
      shutdown(code);
    }
  });
  children.push(child);
  return child;
}

const API_PORT_PREFERRED = 8000;
const WEB_PORT_PREFERRED = 3000;

// Kill existing processes on both ports before starting
await tryFreePort("API", API_PORT_PREFERRED);
await tryFreePort("Web", WEB_PORT_PREFERRED);

// Verify ports are free; if not, find alternatives
let API_PORT = API_PORT_PREFERRED;
let WEB_PORT = WEB_PORT_PREFERRED;

if (!(await isPortAvailable(API_PORT_PREFERRED))) {
  for (let p = API_PORT_PREFERRED + 1; p < API_PORT_PREFERRED + 20; p++) {
    if (await isPortAvailable(p)) { API_PORT = p; break; }
  }
  console.log(`[documind] API port ${API_PORT_PREFERRED} still busy; using ${API_PORT}`);
}

if (!(await isPortAvailable(WEB_PORT_PREFERRED))) {
  for (let p = WEB_PORT_PREFERRED + 1; p < WEB_PORT_PREFERRED + 20; p++) {
    if (await isPortAvailable(p)) { WEB_PORT = p; break; }
  }
  console.log(`[documind] Web port ${WEB_PORT_PREFERRED} still busy; using ${WEB_PORT}`);
}

console.log(`Using Python: ${python}`);
console.log(`API  -> http://localhost:${API_PORT}`);
console.log(`Web  -> http://localhost:${WEB_PORT}`);
console.log(`Logs -> data/logs/documind.log (backend; also printed below)\n`);

const apiUrl = `http://127.0.0.1:${API_PORT}`;
const webOrigin = `http://localhost:${WEB_PORT}`;
const webOriginLocalIp = `http://127.0.0.1:${WEB_PORT}`;
const apiEnv = pythonInstallEnv(
  {
    ...process.env,
    CORS_ORIGINS: [
      webOrigin,
      webOriginLocalIp,
      process.env.CORS_ORIGINS,
    ].filter(Boolean).join(","),
  },
  root,
);
const webEnv = pythonInstallEnv(
  {
    ...process.env,
    DOCUMIND_INTERNAL_API_URL: apiUrl,
    NEXT_PUBLIC_STREAM_API_URL: apiUrl,
  },
  root,
);

const api = run("api", python, [
  "-m",
  "uvicorn",
  "src.main:app",
  "--host",
  "127.0.0.1",
  "--port",
  String(API_PORT),
], root, apiEnv);

const web = run(
  "web",
  "npx",
  ["next", "dev", "--port", String(WEB_PORT)],
  path.join(root, "web"),
  webEnv,
);

let shuttingDown = false;
function shutdown(exitCode = 0) {
  if (shuttingDown) return;
  shuttingDown = true;

  let remaining = children.length;
  if (remaining === 0) return process.exit(exitCode);

  for (const child of children) {
    if (child.pid) {
      treeKill(child.pid, "SIGTERM", () => {
        remaining--;
        if (remaining <= 0) process.exit(exitCode);
      });
    } else {
      remaining--;
    }
  }

  // Force exit after 5 seconds if graceful shutdown stalls
  setTimeout(() => process.exit(exitCode), 5000).unref();
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));
if (isWin) {
  process.on("exit", () => shutdown(0));
}
