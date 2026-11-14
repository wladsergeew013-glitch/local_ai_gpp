"""Stable codes used by HTTP and streaming completions."""


def error_code(status, detail):
    if isinstance(detail, dict) and detail.get("code"):
        return detail["code"]
    message = str(detail)
    for code in (
        "SETTINGS_POLICY_UNAVAILABLE",
        "TOOL_CALL_INCOMPLETE",
        "TOOL_CALL_INVALID",
        "TOOL_CALL_REQUIRED",
        "TOOL_CHOICE_MISMATCH",
        "TOOLS_UNSUPPORTED",
    ):
        if message.startswith(code):
            return code
    if "disabled" in message.lower():
        return "API_DISABLED"
    if "контекст" in message.lower() or "context" in message.lower():
        return "CONTEXT_OVERFLOW"
    return {
        401: "AUTH_REQUIRED",
        403: "FORBIDDEN",
        404: "MODEL_NOT_FOUND",
        409: "RUNTIME_BUSY",
        499: "REQUEST_CANCELLED",
        502: "WORKER_FAILED",
        503: "RUNTIME_UNAVAILABLE",
        504: "WORKER_TIMEOUT",
    }.get(status, "INVALID_REQUEST" if status < 500 else "INFERENCE_FAILED")
