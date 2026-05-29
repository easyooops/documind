import fs from "node:fs/promises";
import path from "node:path";

const [sourceDirArg, destDirArg, mode = "source"] = process.argv.slice(2);

if (!sourceDirArg || !destDirArg) {
  console.error("Usage: node prepare-archive.mjs <source-dir> <dest-dir> [source|product]");
  process.exit(1);
}

const sourceDir = path.resolve(sourceDirArg);
const destDir = path.resolve(destDirArg);
const stagingMarker = `${path.sep}.terraform-staging${path.sep}`;

if (!destDir.includes(stagingMarker)) {
  console.error(`Refusing to clean non-staging destination: ${destDir}`);
  process.exit(1);
}

const commonExcludedDirs = new Set([
  ".git",
  ".cache",
  ".pytest_cache",
  ".ruff_cache",
  ".mypy_cache",
  ".venv",
  ".next",
  ".terraform",
  ".terraform-staging",
  "__pycache__",
  "node_modules",
]);

const sourceExcludedDirs = new Set([...commonExcludedDirs, "data", "documind.egg-info"]);
const productExcludedDirs = new Set([...commonExcludedDirs]);

const commonExcludedFiles = [
  ".env",
  ".terraform.lock.hcl",
  "terraform.tfstate",
  "tfplan",
];

function isExcludedFile(name) {
  return (
    commonExcludedFiles.includes(name) ||
    name.startsWith(".env.") ||
    name.startsWith("terraform.tfstate.") ||
    name.endsWith(".pyc") ||
    name.endsWith(".pyo")
  );
}

function shouldSkipDir(name, relativePath) {
  const excludedDirs = mode === "product" ? productExcludedDirs : sourceExcludedDirs;
  if (excludedDirs.has(name)) return true;
  if (mode === "product" && !relativePath.includes(path.sep)) {
    return !new Set(["compose", "images"]).has(name);
  }
  if (mode === "source" && (relativePath === "setup" || relativePath.startsWith(`setup${path.sep}`))) {
    return true;
  }
  return false;
}

async function copyTree(src, dest, relativePath = "") {
  let entries;
  try {
    entries = await fs.readdir(src, { withFileTypes: true });
  } catch (error) {
    console.warn(`[documind] Skipping unreadable directory: ${src} (${error.code || error.message})`);
    return;
  }

  await fs.mkdir(dest, { recursive: true });

  for (const entry of entries) {
    const nextRelative = relativePath ? path.join(relativePath, entry.name) : entry.name;
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);

    if (entry.isDirectory()) {
      if (shouldSkipDir(entry.name, nextRelative)) continue;
      await copyTree(srcPath, destPath, nextRelative);
      continue;
    }

    if (
      !entry.isFile() ||
      isExcludedFile(entry.name) ||
      (mode === "product" && !nextRelative.includes(path.sep))
    ) {
      continue;
    }

    await fs.mkdir(path.dirname(destPath), { recursive: true });
    try {
      await fs.copyFile(srcPath, destPath);
    } catch (error) {
      console.warn(`[documind] Skipping unreadable file: ${srcPath} (${error.code || error.message})`);
      continue;
    }
    try {
      const stat = await fs.stat(srcPath);
      await fs.utimes(destPath, stat.atime, stat.mtime);
    } catch {
      // Preserving mtimes is best effort; archive content is what matters.
    }
  }
}

await fs.rm(destDir, { recursive: true, force: true });
await copyTree(sourceDir, destDir);
console.log(`[documind] Prepared ${mode} archive staging: ${destDir}`);
