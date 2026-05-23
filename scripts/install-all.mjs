/**
 * Cross-platform install: Python deps + Playwright + web npm packages.
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { resolvePython, platform } from "./lib.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const isWin = platform.isWin;
const python = resolvePython();
const constraints = path.join(root, "constraints.txt");

function run(step, args, cwd = root) {
  runPython(step, args, cwd, python);
}

function runPython(step, args, cwd = root, executable = python) {
  console.log(`\n> ${step}\n`);
  const result = spawnSync(executable, args, {
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

function venvPython(venvDir) {
  return path.join(venvDir, isWin ? "Scripts/python.exe" : "bin/python");
}

function removeTempDir(tempDir) {
  if (!fs.existsSync(tempDir)) return;

  const realTempRoot = fs.realpathSync(os.tmpdir());
  const realTempDir = fs.realpathSync(tempDir);
  if (
    realTempDir !== realTempRoot &&
    realTempDir.startsWith(`${realTempRoot}${path.sep}`)
  ) {
    fs.rmSync(realTempDir, { recursive: true, force: true });
  } else {
    throw new Error(`Refusing to remove non-temp directory: ${realTempDir}`);
  }
}

function auditProjectPythonDeps() {
  const auditDir = fs.mkdtempSync(path.join(os.tmpdir(), "documind-audit-"));
  try {
    run("create Python audit venv", ["-m", "venv", auditDir]);
    const auditPython = venvPython(auditDir);
    runPython(
      "pip install --upgrade pip (audit venv)",
      ["-m", "pip", "install", "--upgrade", "pip"],
      root,
      auditPython
    );
    runPython(
      "pip install audit target",
      ["-m", "pip", "install", "-c", constraints, "-e", ".[dev,bedrock]"],
      root,
      auditPython
    );
    runPython(
      "pip audit (project deps)",
      ["-m", "pip_audit", "--local", "--skip-editable"],
      root,
      auditPython
    );
  } finally {
    removeTempDir(auditDir);
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

run("pip install --upgrade pip", ["-m", "pip", "install", "--upgrade", "pip"]);
run("pip install (editable + bedrock)", [
  "-m",
  "pip",
  "install",
  "-c",
  constraints,
  "-e",
  ".[dev,bedrock]",
]);
auditProjectPythonDeps();
run("playwright install chromium", ["-m", "playwright", "install", "chromium"]);
runNpm("npm install (web)", ["install"], path.join(root, "web"));
runNpm("npm audit (root)", ["audit", "--audit-level=moderate"], root);
runNpm("npm audit (web)", ["audit", "--audit-level=moderate"], path.join(root, "web"));

console.log("\nInstall complete.\n");
