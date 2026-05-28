"""Sync the minimal DocuMind runtime into the SDK package source tree."""

from __future__ import annotations

import shutil
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]
SOURCE_ROOT = REPO_ROOT / "src"
TARGET_ROOT = PACKAGE_ROOT / "src" / "src"

EXCLUDED_DIRS = {
    "__pycache__",
    "api",
}

EXCLUDED_FILES = {
    SOURCE_ROOT / "__main__.py",
    SOURCE_ROOT / "cli.py",
    SOURCE_ROOT / "main.py",
    SOURCE_ROOT / "schemas" / "api.py",
    SOURCE_ROOT / "infrastructure" / "database.py",
    SOURCE_ROOT / "infrastructure" / "models.py",
    SOURCE_ROOT / "infrastructure" / "storage.py",
}


def _ignore(directory: str, names: list[str]) -> set[str]:
    root = Path(directory)
    ignored = {name for name in names if name in EXCLUDED_DIRS}
    for name in names:
        path = root / name
        if path in EXCLUDED_FILES:
            ignored.add(name)
    return ignored


def main() -> None:
    if TARGET_ROOT.exists():
        shutil.rmtree(TARGET_ROOT)
    shutil.copytree(SOURCE_ROOT, TARGET_ROOT, ignore=_ignore)


if __name__ == "__main__":
    main()
