/**
 * Cross-platform install: project .venv + Python deps + Playwright + web npm packages.
 * Uses only ASCII paths under the repo (no OS temp / user-profile venvs).
 */
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  ensureProjectVenv,
  platform,
  playwrightInstallEnv,
  projectVenvDir,
  pythonInstallEnv,
} from "./lib.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const isWin = platform.isWin;
const constraints = path.join(root, "constraints.txt");
const installEnv = pythonInstallEnv(process.env, root);

function runPython(step, args, executable, { optional = false, env = installEnv } = {}) {
  console.log(`\n> ${step}\n`);
  const result = spawnSync(executable, args, {
    cwd: root,
    stdio: "inherit",
    shell: isWin,
    env,
  });
  if (result.status !== 0) {
    if (optional) {
      console.warn(
        `\n[documind] Warning: ${step} exited with code ${result.status ?? 1}. Install continues.\n`
      );
      return false;
    }
    console.error(`Failed: ${step}`);
    process.exit(result.status ?? 1);
  }
  return true;
}

function runNpm(step, args, cwd, { optional = false } = {}) {
  console.log(`\n> ${step}\n`);
  const result = spawnSync("npm", args, {
    cwd,
    stdio: "inherit",
    shell: isWin,
    env: process.env,
  });
  if (result.status !== 0) {
    if (optional) {
      console.warn(
        `\n[documind] Warning: ${step} exited with code ${result.status ?? 1}. Install continues.\n`
      );
      return false;
    }
    console.error(`Failed: ${step}`);
    process.exit(result.status ?? 1);
  }
  return true;
}

const venvPython = ensureProjectVenv(root);
console.log(`Using project venv: ${projectVenvDir(root)}`);
console.log(`Python executable: ${venvPython}`);

runPython("pip install --upgrade pip", ["-m", "pip", "install", "--upgrade", "pip"], venvPython);
runPython(
  "pip install (editable + dev + bedrock)",
  ["-m", "pip", "install", "-c", constraints, "-e", ".[dev,bedrock]"],
  venvPython
);
runPython(
  "pip audit (project deps)",
  ["-m", "pip_audit", "--local", "--skip-editable"],
  venvPython
);
const playwrightEnv = playwrightInstallEnv(process.env, root, venvPython);
const playwrightOk = runPython(
  "playwright install chromium",
  ["-m", "playwright", "install", "chromium"],
  venvPython,
  { optional: true, env: playwrightEnv }
);
if (!playwrightOk) {
  console.warn(
    "[documind] Playwright browser download failed (corporate TLS/proxy is common).\n" +
      "  1. Set NODE_EXTRA_CA_CERTS to your company root CA .pem (see Playwright docs).\n" +
      "  2. Or retry:\n" +
      "     npm run install:playwright\n" +
      "  PPTX QA screenshots need Chromium; other features may still run.\n"
  );
}
runNpm("npm install (web)", ["install"], path.join(root, "web"));
runNpm("npm audit (root)", ["audit", "--audit-level=moderate"], root, { optional: true });
runNpm("npm audit (web)", ["audit", "--audit-level=moderate"], path.join(root, "web"), {
  optional: true,
});

console.log("\nInstall complete.");
console.log("Activate the venv before manual Python commands:");
if (isWin) {
  console.log("  .\\.venv\\Scripts\\Activate.ps1");
} else {
  console.log("  source .venv/bin/activate");
}
console.log("Or use npm scripts (they auto-detect .venv).\n");
