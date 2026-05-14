from __future__ import annotations

import os
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
OUT = ROOT / "tools" / "out"

REQUIRED = [
    DIST / "LocalAIGPP.exe",
    DIST / "worker_runtime" / "python.exe",
    DIST / "backend" / "app" / "llama_worker.py",
    DIST / "models_storage" / "models.json",
]

EXCLUDE_DIR_NAMES = {"logs", "assistant_state", "__pycache__"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".tmp", ".log"}


def should_skip(path: Path) -> bool:
    rel_parts = path.relative_to(DIST).parts
    if any(part in EXCLUDE_DIR_NAMES for part in rel_parts):
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    return False


def main() -> int:
    missing = [path for path in REQUIRED if not path.exists()]
    if missing:
        print("[ERROR] dist is incomplete. Missing:")
        for path in missing:
            print("  ", path)
        print("Run tools\\02_build_exe.bat --cpu first.")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    target = OUT / f"LocalAIGPP_portable_dist_{stamp}.zip"
    print("[INFO] Creating portable package:", target)
    count = 0
    size = 0
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        for path in DIST.rglob("*"):
            if not path.is_file() or should_skip(path):
                continue
            arcname = Path("LocalAIGPP_dist") / path.relative_to(DIST)
            archive.write(path, arcname.as_posix())
            count += 1
            size += path.stat().st_size
    print(f"[OK] files={count}, unpacked_size={size:,} bytes")
    print(f"[OK] zip={target}")
    print("Copy this ZIP to another PC, unpack it, then run LocalAIGPP_dist\\CHECK_DIST_HEALTH.bat first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
