from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
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
RELEASE_ROOT_FILES = {'LocalAIGPP.exe', 'LocalAIGPP.ico', 'VERSION', 'runtime_info.json',
                      'CHECK_DIST_HEALTH.bat', 'RUN_LocalAIGPP.bat', 'README_PORTABLE_DIST.txt', '_dist_health_check.py'}


def release_settings(runtime_kind: str) -> dict:
    sys.path.insert(0, str(ROOT))
    from backend.app.core import DEFAULT_SETTINGS
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings['runtime'].update(n_ctx=4096, max_tokens=512, n_batch=128,
                               n_gpu_layers=8 if runtime_kind.startswith('cu') else 0,
                               gpu_fallback_to_cpu=False, context_overflow='trim')
    return settings


def write_release_archive(dist: Path, target: Path, version: str, settings: dict, branding: list[Path]) -> tuple[int, int]:
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Expected a semantic release version.')
    if (dist / 'VERSION').read_text(encoding='utf-8').strip() != version:
        raise ValueError('Build VERSION does not match the release. Rebuild the EXE first.')
    folder = f'LocalAIGPP-{version}'
    count, size = 0, 0
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        for path in sorted(dist.rglob('*')):
            relative = path.relative_to(dist)
            if not path.is_file() or path.is_symlink():
                continue
            if any(part in EXCLUDE_DIR_NAMES for part in relative.parts) or path.suffix.lower() in EXCLUDE_SUFFIXES:
                continue
            if relative.parts[0] not in {'backend', 'worker_runtime'} and relative.as_posix() not in RELEASE_ROOT_FILES:
                continue
            if path.name == 'direct_url.json':
                continue
            archive.write(path, f'{folder}/{relative.as_posix()}')
            count += 1
            size += path.stat().st_size
        for filename, data in (('models.json', []), ('settings.json', settings)):
            archive.writestr(f'{folder}/models_storage/{filename}', json.dumps(data, ensure_ascii=False, indent=2))
            count += 1
        for path in branding:
            relative = path.relative_to(ROOT / 'models_storage')
            archive.write(path, f'{folder}/models_storage/{relative.as_posix()}')
            count += 1
        archive.write(ROOT / 'README.md', f'{folder}/README.md')
        archive.write(ROOT / 'LICENSE', f'{folder}/LICENSE')
        count += 2
    return count, size


def should_skip(path: Path) -> bool:
    rel_parts = path.relative_to(DIST).parts
    if any(part in EXCLUDE_DIR_NAMES for part in rel_parts):
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description='Build a local portable package or a clean public release.')
    parser.add_argument('--release', action='store_true', help='Exclude user settings, models, history and logs; inject clean defaults.')
    args = parser.parse_args()
    missing = [path for path in REQUIRED if not path.exists()]
    if missing:
        print("[ERROR] dist is incomplete. Missing:")
        for path in missing:
            print("  ", path)
        print("Run tools\\02_build_exe.bat first.")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    if args.release:
        version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
        runtime = json.loads((DIST / 'runtime_info.json').read_text(encoding='utf-8'))
        runtime_kind = str(runtime['effective'])
        target = OUT / f'LocalAIGPP-{version}-win-x64-{runtime_kind}.zip'
        tracked = subprocess.run(['git', 'ls-files', '-z', 'models_storage/branding'], cwd=ROOT,
                                 check=True, capture_output=True).stdout.decode('utf-8').split('\0')
        branding = [ROOT / name for name in tracked if name]
        count, size = write_release_archive(DIST, target, version, release_settings(runtime_kind), branding)
        print(f'[OK] Public release: {target}\n[OK] files={count}, unpacked_size={size:,} bytes')
        print('[OK] Models, local settings, history and logs are excluded.')
        return 0
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
