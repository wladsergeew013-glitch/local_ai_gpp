"""An operator capability kept separate from the task's MCP token."""

import re
import secrets

UI_TOKEN_HEADER = "X-Workspace-UI-Token"
UI_TOKEN_ENV = "WORKSPACE_UI_TOKEN"  # Used by operator-side check scripts.


def operator_token(value=None):
    if value is None:
        return secrets.token_urlsafe(32)
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", value):
        raise ValueError(
            "Expected a Workspace token generated with secrets.token_urlsafe(32)."
        )
    return value


def requires_operator(path, method):
    if not path.startswith("/api/") or (path == "/api/health" and method == "GET"):
        return False
    # This route does its own task-scoped authorization. No other task routes
    # accept the MCP credential.
    return not (
        method == "POST" and re.fullmatch(r"/api/(?:chats|tasks)/[^/]+/mcp", path)
    )
