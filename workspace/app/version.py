from pathlib import Path
import sys

VERSION = (
    (
        Path(sys._MEIPASS) / "workspace_version" / "VERSION"
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[1] / "VERSION"
    )
    .read_text(encoding="utf-8")
    .strip()
)
