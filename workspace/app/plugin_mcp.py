"""Streamable HTTP sessions for installed application packages."""

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass, field

import httpx

from workspace.app.plugin_manifest import parse_json
from workspace.app.skill_resources import MAX_OUTPUT_BYTES
from workspace.app.skills import REVISION
from workspace.app.version import VERSION


SUPPORTED_REVISIONS = {REVISION, "2025-06-18", "2025-03-26"}
SETUP_BYTES = 256_000
RESULT_BYTES = (MAX_OUTPUT_BYTES + 2) // 3 * 4 + 1_000_000


class McpError(ValueError):
    def __init__(self, category, message, *, uncertain=False):
        super().__init__(message)
        self.category = category
        self.uncertain = uncertain


@dataclass
class Session:
    protocol: str = ""
    identifier: str = ""
    capabilities: dict = field(default_factory=dict)
    number: int = 0
    generation: int = 0

    def reset(self):
        self.identifier = self.protocol = ""
        self.capabilities = {}


async def messages(response, cap):
    buffer, lines, size = bytearray(), [], 0
    async for chunk in response.aiter_bytes(64 * 1024):
        size += len(chunk)
        if size > cap:
            raise ValueError("Ответ MCP превышает допустимый размер.")
        buffer.extend(chunk)
        start = 0
        while (index := buffer.find(b"\n", start)) != -1:
            line = bytes(buffer[start:index]).removesuffix(b"\r")
            start = index + 1
            if line.startswith(b"data:"):
                lines.append(line[5:].removeprefix(b" "))
            elif not line and lines:
                yield parse_json(b"\n".join(lines))
                lines.clear()
        del buffer[:start]


class StreamableMcp:
    def __init__(self, url, key, session, *, connect_timeout, setup_timeout, tool_timeout, transport=None):
        self.url, self.key, self.session = url, key, session
        self.connect_timeout, self.setup_timeout, self.tool_timeout = connect_timeout, setup_timeout, tool_timeout
        self.client = httpx.AsyncClient(follow_redirects=False, trust_env=False, transport=transport)
        self.progress = None

    def headers(self, accept="application/json, text/event-stream"):
        headers = {"Accept": accept}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        if self.session.protocol:
            headers["MCP-Protocol-Version"] = self.session.protocol
        if self.session.identifier:
            headers["MCP-Session-Id"] = self.session.identifier
        return headers

    def notification(self, event):
        if not isinstance(event, dict):
            return
        if event.get("method") == "notifications/tools/list_changed":
            self.session.generation += 1
        elif self.progress and event.get("method") == "notifications/progress":
            params = event.get("params")
            if isinstance(params, dict):
                self.progress(params)

    async def watch_changes(self):
        try:
            async with self.client.stream(
                "GET", self.url, headers=self.headers("text/event-stream"),
                timeout=httpx.Timeout(self.tool_timeout, connect=self.connect_timeout),
            ) as response:
                if response.status_code == 200 and response.headers.get("content-type", "").startswith("text/event-stream"):
                    async for event in messages(response, SETUP_BYTES):
                        self.notification(event)
        except (ValueError, httpx.HTTPError):
            # Full tools/list before each business call also covers a lost notification stream.
            return

    async def rpc(self, method, params=None, *, notify=False):
        business = method == "tools/call"
        timeout = self.tool_timeout if business else self.setup_timeout
        cap = RESULT_BYTES if business or method == "resources/read" else SETUP_BYTES
        request = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            request["params"] = params
        if not notify:
            self.session.number += 1
            request["id"] = self.session.number
        try:
            async with asyncio.timeout(timeout):
                async with self.client.stream(
                    "POST", self.url, json=request, headers=self.headers(),
                    timeout=httpx.Timeout(timeout, connect=self.connect_timeout),
                ) as response:
                    status = response.status_code
                    if status == 404 and self.session.identifier:
                        self.session.reset()
                        raise McpError("session_expired", "Сессия MCP завершена сервером.", uncertain=business)
                    if status >= 300:
                        raise McpError("protocol", f"Endpoint MCP вернул HTTP {status}.", uncertain=business and status >= 500)
                    if notify:
                        return {}
                    data = await self._read_reply(response, request["id"], cap)
                    if method == "initialize":
                        session_id = response.headers.get("MCP-Session-Id", "")
                        if any(not 33 <= ord(c) <= 126 for c in session_id):
                            raise ValueError("MCP вернул недопустимый идентификатор сессии.")
                        self.session.identifier = session_id
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            self.session.reset()
            raise McpError("offline", "Нет соединения с MCP по указанному адресу. Запустите приложение или проверьте адрес.") from exc
        except (TimeoutError, httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            self.session.reset()
            raise McpError("timeout" if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else "protocol",
                           f"Ответ MCP на {method} не получен.", uncertain=business) from exc
        except McpError:
            raise
        except (UnicodeError, ValueError, httpx.HTTPError) as exc:
            raise McpError("protocol", f"Некорректный ответ MCP на {method}.", uncertain=business) from exc
        if not isinstance(data, dict) or data.get("jsonrpc") != "2.0" or type(data.get("id")) is not int or data["id"] != request["id"]:
            raise McpError("protocol", f"MCP вернул несогласованный ответ на {method}.", uncertain=business)
        if isinstance(data.get("error"), dict):
            code = data["error"].get("code")
            message = data["error"].get("message")
            # Only show the numeric protocol classification here. Tool data belongs in a result.
            detail = f" {code}" if type(code) is int else ""
            if isinstance(message, str) and re_safe_message(message):
                detail += f" ({message[:200]})"
            raise McpError("tool_error" if business else "protocol", f"Ошибка JSON-RPC{detail} при {method}.")
        if not isinstance(data.get("result"), dict):
            raise McpError("protocol", f"MCP не вернул result для {method}.", uncertain=business)
        return data["result"]

    async def _read_reply(self, response, number, cap):
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type == "text/event-stream":
            async for event in messages(response, cap):
                self.notification(event)
                if isinstance(event, dict) and event.get("id") == number:
                    return event
            raise ValueError("Поток MCP завершился без ответа.")
        if content_type != "application/json":
            raise ValueError("Ответ MCP должен быть JSON или SSE.")
        raw = bytearray()
        async for chunk in response.aiter_bytes(64 * 1024):
            if len(raw) + len(chunk) > cap:
                raise ValueError("Ответ MCP превышает допустимый размер.")
            raw.extend(chunk)
        return parse_json(raw)

    async def initialize(self, expected_name):
        if self.session.protocol and self.session.identifier:
            return
        self.session.reset()
        try:
            result = await self.rpc("initialize", {"protocolVersion": REVISION, "capabilities": {},
                                                   "clientInfo": {"name": "Local AI Workspace", "version": VERSION}})
            protocol = result.get("protocolVersion")
            if protocol not in SUPPORTED_REVISIONS:
                raise McpError("protocol", "Версия протокола MCP не поддерживается.")
            server = result.get("serverInfo")
            if not isinstance(server, dict) or not isinstance(server.get("name"), str):
                raise McpError("protocol", "В инициализации MCP отсутствует имя сервера.")
            if expected_name and server["name"] != expected_name:
                raise McpError("protocol", f"На данном адресе ожидался сервер «{expected_name}».")
            capabilities = result.get("capabilities")
            if not isinstance(capabilities, dict) or not isinstance(capabilities.get("tools"), dict):
                raise McpError("protocol", "Сервер MCP не объявил поддержку инструментов.")
            self.session.capabilities = capabilities
            self.session.protocol = protocol
            await self.rpc("notifications/initialized", notify=True)
        except Exception:
            self.session.reset()
            raise

    async def tools(self, expected_name):
        for attempt in range(2):
            await self.initialize(expected_name)
            try:
                generation = self.session.generation
                catalog = await self._list_tools()
                if self.session.generation != generation:
                    catalog = await self._list_tools()
                return catalog
            except McpError as exc:
                if exc.category != "session_expired" or attempt:
                    raise

    async def _list_tools(self):
        tools, cursors, cursor = {}, set(), None
        for _ in range(128):
            page = await self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            entries = page.get("tools")
            if not isinstance(entries, list):
                raise McpError("protocol", "MCP вернул неверный список инструментов.")
            for tool in entries:
                name = tool.get("name") if isinstance(tool, dict) else None
                if not isinstance(name, str) or name in tools:
                    raise McpError("protocol", "MCP вернул инструмент без имени или с повторяющимся именем.")
                tools[name] = tool
            cursor = page.get("nextCursor")
            if not cursor:
                return tools
            if not isinstance(cursor, str) or cursor in cursors:
                raise McpError("protocol", "Сервер MCP повторил курсор списка инструментов.")
            cursors.add(cursor)
        raise McpError("protocol", "Превышен предел страниц списка инструментов.")

    async def cancel(self, number):
        with suppress(McpError, TimeoutError):
            async with asyncio.timeout(0.8):
                await self.rpc("notifications/cancelled", {"requestId": number}, notify=True)

    async def close(self, watcher=None):
        if watcher:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
        await self.client.aclose()


def re_safe_message(value):
    # Avoid echoing server payloads, documents or tokens in diagnostic errors.
    return value in {"Parse error", "Invalid Request", "Method not found", "Invalid params", "Internal error"}
