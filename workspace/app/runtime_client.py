"""The only Workspace interface to Local AI Runtime. No shared files/imports."""

from urllib.parse import quote, urlsplit
import asyncio
from contextlib import suppress
import ipaddress
import httpx


def local_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "http"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Use an http:// loopback URL without credentials, query or fragment."
        )
    host = parsed.hostname or ""
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host.lower() == "localhost"
    if not local:
        raise ValueError("This local profile accepts loopback endpoints only.")
    return value.strip().rstrip("/")


def provider_url(value):
    parsed = urlsplit(value.strip())
    if parsed.scheme == "http":
        return local_url(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or any(ord(c) <= 32 for c in value.strip())):
        raise ValueError("Укажите HTTPS-адрес OpenAI-совместимого провайдера без параметров или учётных данных.")
    return value.strip().rstrip("/")


def decision_request(name, description, schema):
    return {
        "tools": [{"type": "function", "function": {"name": name, "description": description, "parameters": schema}}],
        "tool_choice": {"type": "function", "function": {"name": name}},
        "parallel_tool_calls": False,
    }


def decision_content(choice, tool_name=None):
    message = choice["message"]
    calls = message.get("tool_calls")
    if tool_name and calls and choice.get("finish_reason") in {"stop", "tool_calls"}:
        if len(calls) == 1 and calls[0]["function"]["name"] == tool_name:
            return calls[0]["function"]["arguments"]
        raise ValueError("Модель вернула несогласованное действие.")
    if choice.get("finish_reason") != "stop":
        raise ValueError("Решение модели не завершено.")
    return message.get("content") or ""


class RuntimeClient:
    def __init__(self, provider):
        self.base_url = provider_url(provider["base_url"])
        self.remote = self.base_url.startswith("https://")
        self.model = provider["model"]
        self.key = provider.get("api_key") or ""

    def request(self, method, path, **kwargs):
        headers = {"Authorization": "Bearer " + self.key} if self.key else {}
        headers.update(kwargs.pop("headers", {}))
        cancelled = kwargs.pop("cancelled", None)
        options = {"base_url": self.base_url + "/", "headers": headers, "timeout": 180,
                   "trust_env": False, "follow_redirects": False}
        if cancelled is None:
            with httpx.Client(**options) as client:
                result = client.request(method, path, **kwargs)
        else:
            result = asyncio.run(self._cancellable_request(method, path, options, kwargs, cancelled))
        result.raise_for_status()
        return result.json()

    @staticmethod
    async def _cancellable_request(method, path, options, kwargs, cancelled):
        if cancelled.is_set():
            raise ValueError("Запрос модели остановлен.")
        async with httpx.AsyncClient(**options) as client:
            request = asyncio.create_task(client.request(method, path, **kwargs))
            try:
                while not cancelled.is_set():
                    done, _ = await asyncio.wait({request}, timeout=0.15)
                    if done:
                        return request.result()
                raise ValueError("Запрос модели остановлен.")
            finally:
                if not request.done():
                    request.cancel()
                    with suppress(asyncio.CancelledError):
                        await request

    def capabilities(self, load=False):
        if self.remote:
            # OpenAI-compatible chat providers need not expose Runtime's management API.
            return {"max_output_tokens": 2048, "context_window": 8192, "tools": {"supported": True}}
        encoded = quote(self.model, safe="")
        try:
            result = self.request("GET", "models/" + encoded + "/capabilities")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ValueError(
                    "Runtime не нашёл выбранную модель. Проверьте связь и выберите её ID в настройках Workspace."
                ) from exc
            raise
        if load and not result.get("loaded"):
            result = self.request("POST", "models/" + encoded + "/load")
        if str(result.get("api_version", "")).split(".")[0] != "1":
            raise ValueError("Unsupported Runtime API version.")
        return result

    def probe(self):
        models = self.request("GET", "models", timeout=5).get("data", [])
        if self.remote:
            available = any(row.get("id") == self.model for row in models) if self.model else None
            return {"models": models, "ready": {}, "model_available": available, "model_loaded": False, "remote": True}
        readiness = self.request("GET", "runtime/readiness", timeout=5)
        available = (
            any(row.get("id") == self.model for row in models) if self.model else None
        )
        return {
            "models": models,
            "ready": readiness,
            "model_available": available,
            "model_loaded": bool(
                available and self.model in readiness.get("ready_model_ids", [])
            ),
        }

    def cancel_owner(self, owner_id, token):
        if self.remote:
            return {"cancelled": 0}
        return self.request(
            "POST",
            "owners/" + quote(owner_id, safe="") + "/cancel",
            headers={"X-Local-AI-Owner-Token": token},
            timeout=3,
        )

    def chat_payload(self, payload):
        if not self.remote:
            return payload
        return {k: v for k, v in payload.items()
                if k not in {"memory", "truncate_history", "chat_template_kwargs"}}
