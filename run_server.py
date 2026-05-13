from __future__ import annotations

import os

import uvicorn

from backend.app.core import load_settings


def main() -> None:
    settings = load_settings().get("server", {})
    host = os.getenv("LOCAL_AI_GPP_HOST", str(settings.get("host") or "127.0.0.1"))
    port = int(os.getenv("LOCAL_AI_GPP_PORT", str(settings.get("port") or 8000)))
    uvicorn.run("backend.app.main:app", host=host, port=port)


if __name__ == "__main__":
    main()
