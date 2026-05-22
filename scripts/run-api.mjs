import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { resolvePython, platform, tryFreePort } from "./lib.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const isWin = platform.isWin;
const python = resolvePython();
const PORT = 8000;

// Kill any existing process on port 8000 before starting
await tryFreePort("API", PORT);

const args = [
  "-m",
  "uvicorn",
  "src.main:app",
  "--host",
  "127.0.0.1",
  "--port",
  String(PORT),
];

const child = spawn(python, args, {
  cwd: root,
  stdio: "inherit",
  shell: isWin,
  env: process.env,
});

child.on("exit", (code) => process.exit(code ?? 0));
