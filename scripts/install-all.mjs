/**
 * Cross-platform install: Python deps + Playwright + web npm packages.
 */
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { resolvePython, platform } from "./lib.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const isWin = platform.isWin;
const python = resolvePython();

function run(step, args, cwd = root) {
  console.log(`\n> ${step}\n`);
  const result = spawnSync(python, args, {
    cwd,
    stdio: "inherit",
    shell: isWin,
    env: process.env,
  });
  if (result.status !== 0) {
    console.error(`Failed: ${step}`);
    process.exit(result.status ?? 1);
  }
}

function runNpm(step, args, cwd) {
  console.log(`\n> ${step}\n`);
  const result = spawnSync("npm", args, {
    cwd,
    stdio: "inherit",
    shell: isWin,
    env: process.env,
  });
  if (result.status !== 0) {
    console.error(`Failed: ${step}`);
    process.exit(result.status ?? 1);
  }
}

console.log(`Using Python: ${python}`);

run("pip install (editable + bedrock)", ["-m", "pip", "install", "-e", ".[dev,bedrock]"]);
run("playwright install chromium", ["-m", "playwright", "install", "chromium"]);
runNpm("npm install (web)", ["install"], path.join(root, "web"));

console.log("\nInstall complete.\n");
