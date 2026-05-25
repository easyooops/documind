/**
 * Install Playwright Chromium only (retry after install:all or when browsers are missing).
 */
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  ensureProjectVenv,
  platform,
  playwrightInstallEnv,
  projectVenvDir,
} from "./lib.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const isWin = platform.isWin;
const venvPython = ensureProjectVenv(root);
const env = playwrightInstallEnv(process.env, root, venvPython);

console.log(`Using project venv: ${projectVenvDir(root)}`);
console.log("NODE_USE_SYSTEM_CA=1 (Windows corporate TLS)");
if (env.NODE_EXTRA_CA_CERTS) {
  console.log(`NODE_EXTRA_CA_CERTS=${env.NODE_EXTRA_CA_CERTS}`);
}

console.log("\n> playwright install chromium\n");
const result = spawnSync(venvPython, ["-m", "playwright", "install", "chromium"], {
  cwd: root,
  stdio: "inherit",
  shell: isWin,
  env,
});

process.exit(result.status ?? 1);
