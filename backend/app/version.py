from __future__ import annotations

import sys
from pathlib import Path


def _version_file() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "VERSION"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2] / "VERSION"


APP_VERSION = _version_file().read_text(encoding="utf-8").strip()
