from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from backend.app.core import collect_local_llama_diagnostics


def run_nvidia_smi() -> dict[str, object]:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return {"found": False, "snapshot": ""}
    try:
        result = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=name,driver_version,memory.used,memory.total",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        return {"found": True, "snapshot": (result.stdout or result.stderr or "").strip()}
    except Exception as exc:
        return {"found": True, "snapshot": f"nvidia-smi failed: {exc}"}


def main() -> int:
    diagnostics = collect_local_llama_diagnostics()
    report = {
        "python": sys.executable,
        "cwd": str(Path.cwd()),
        "diagnostics": diagnostics,
        "nvidia_smi": run_nvidia_smi(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if diagnostics.get("package_installed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
