/**
 * Run a Python module: node scripts/run-python.mjs -m uvicorn ...
 * Forwards all args after node to python -m ...
 */
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { resolvePython, platform } from "./lib.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const isWin = platform.isWin;
const python = resolvePython();
const args = process.argv.slice(2);

if (args[0] !== "-m") {
  console.error("Usage: node scripts/run-python.mjs -m <module> [args...]");
  process.exit(1);
}

const result = spawnSync(python, args, {
  cwd: root,
  stdio: "inherit",
  shell: isWin,
  env: process.env,
});

process.exit(result.status ?? 1);
