"""Read a plugin manifest and capture exactly the bytes allowed by the package."""

import hashlib
import json
import math
import os
import posixpath
import re
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator

from workspace.app.skills import SECTION, instruction_sections, local_mcp_url


MANIFEST_BYTES = 256_000
MARKDOWN_BYTES = 128_000
PACKAGE_BYTES = 2 * 1024 * 1024
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "skill-package-v2.json"
SCHEMA = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]\n]+\]\(\s*(<[^>\n]+>|[^\s)]+)(?:\s+[^)\n]+)?\)")


def parse_json(source):
    def object_from(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Повторяется ключ JSON «{key}».")
            result[key] = value
        return result

    def invalid_number(value):
        raise ValueError(f"Значение {value} недопустимо в JSON.")

    def finite_number(value):
        number = float(value)
        if not math.isfinite(number):
            return invalid_number(value)
        return number

    return json.loads(source, object_pairs_hook=object_from, parse_constant=invalid_number, parse_float=finite_number)


def relative_path(value):
    path = PurePosixPath(value.replace("\\", "/"))
    windows = PureWindowsPath(value)
    parts = value.replace("\\", "/").split("/")
    if (path.anchor or windows.anchor or ":" in value or
            any(part in {"", ".", ".."} or part.endswith((" ", ".")) for part in parts) or
            any(PureWindowsPath(part).is_reserved() for part in parts) or
            any(ord(c) < 32 for c in value) or any(c in value for c in '<>"|?*')):
        raise ValueError(f"«{value}» должен быть относительным путём внутри пакета.")
    return path.as_posix()


def _windows_opened_path(fd):
    import ctypes
    import msvcrt
    from ctypes import wintypes

    get_path = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
    get_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    get_path.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    count = get_path(msvcrt.get_osfhandle(fd), buffer, len(buffer), 0)
    if not count or count >= len(buffer):
        raise OSError("Не удалось подтвердить расположение открытого файла пакета.")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def read_member(root, relative, limit):
    parts = PurePosixPath(relative_path(relative)).parts
    try:
        target = root.joinpath(*parts).resolve(strict=True)
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError("Файл находится за пределами пакета или имеет неподдерживаемый тип.")
        if os.name == "nt":
            with target.open("rb") as file:
                if not _windows_opened_path(file.fileno()).is_relative_to(root):
                    raise ValueError("Открытый файл находится за пределами пакета.")
                return _bounded_read(file, limit)
        parts = target.relative_to(root).parts
        with ExitStack() as stack:
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory = os.open(root, directory_flags)
            stack.callback(os.close, directory)
            for part in parts[:-1]:
                directory = os.open(part, directory_flags, dir_fd=directory)
                stack.callback(os.close, directory)
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, "rb") as file:
                return _bounded_read(file, limit)
    except (OSError, ValueError) as exc:
        raise ValueError(f"«{relative}» — {exc}") from exc


def _bounded_read(file, limit):
    info = os.fstat(file.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError(f"Нужен обычный файл размером до {limit} байт.")
    data = file.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"Размер файла превышает {limit} байт.")
    return data


def _normalize(source):
    try:
        config = parse_json(source.lstrip("\ufeff"))
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"Манифест — некорректный JSON. {exc}") from exc
    errors = sorted(SCHEMA.iter_errors(config), key=lambda error: tuple(map(str, error.absolute_path)))
    if errors:
        error = errors[0]
        field = ".".join(map(str, error.absolute_path)) or "manifest"
        if error.validator == "additionalProperties" and "properties" in error.schema:
            detail = "неизвестные поля " + ", ".join(sorted(set(error.instance) - set(error.schema["properties"])))
        elif error.validator == "required":
            detail = "отсутствуют поля " + ", ".join(key for key in error.validator_value if key not in error.instance)
        elif error.validator == "oneOf":
            detail = "укажите tools либо совместимую запись tool с одним из file и files"
        else:
            detail = f"нарушено условие схемы {error.validator}"
        raise ValueError(f"{field} — {detail}.")
    if type(config["schemaVersion"]) is not int:
        raise ValueError("schemaVersion — требуется целое число 2.")
    mcp = config["mcp"]
    if not any("tool" in entry for entry in config["skills"]):
        if "packageVersion" not in config or "transport" not in mcp:
            raise ValueError("Для пакета с tools укажите packageVersion и mcp.transport.")
    try:
        if not mcp["url"].isprintable() or any(char.isspace() for char in mcp["url"]):
            raise ValueError()
        mcp["url"] = local_mcp_url(mcp["url"])
        if urlsplit(mcp["url"]).port == 0:
            raise ValueError()
    except ValueError as exc:
        raise ValueError("mcp.url — укажите локальный HTTP-адрес с действительным портом и путём.") from exc
    mcp.setdefault("transport", "streamable-http")
    names, paths = set(), {}
    for entry in config["skills"]:
        name = entry["name"].casefold()
        if name in names:
            raise ValueError(f"skills.name — повторяется «{entry['name']}».")
        names.add(name)
        entry["path"] = relative_path(entry["path"])
        previous = paths.setdefault(entry["path"].casefold(), entry["path"])
        if previous != entry["path"]:
            raise ValueError(f"skills.path — конфликт имён «{previous}» и «{entry['path']}».")
        if "tool" in entry:
            entry["tools"] = [{"name": entry.pop("tool"), **{k: entry.pop(k) for k in ("file", "files") if k in entry}}]
        tool_names = set()
        for tool in entry["tools"]:
            if tool["name"] in tool_names:
                raise ValueError(f"{entry['name']}.tools — повторяется «{tool['name']}».")
            tool_names.add(tool["name"])
            binding = tool.get("file") or tool.get("files")
            if binding and binding["filename"] == binding["base64"]:
                raise ValueError(f"{entry['name']}/{tool['name']} — поля имени и данных должны различаться.")
        entry["accepts"] = list(dict.fromkeys(ext.lower() for ext in entry.get("accepts", [])))
        if not entry["title"].strip() or not entry["description"].strip():
            raise ValueError(f"{entry['name']} — заполните title и description.")
    if any(
        not value.strip() or not value.isprintable()
        for value in [config["skillGroup"]["name"], config.get("packageVersion", "unversioned"),
                      mcp.get("expectedServerName", "unnamed")]
    ):
        raise ValueError("Проверьте имя группы, версию пакета и имя MCP-сервера.")
    return config


def _clean_markdown(source):
    body = source.lstrip("\ufeff").replace("\r\n", "\n")
    body = re.sub(r"\A---\n.*?\n---(?:\n|$)", "", body, count=1, flags=re.S)
    return SECTION.sub("", body).strip()


@dataclass(frozen=True)
class Package:
    raw_manifest: str
    manifest: dict
    markdown: dict
    instructions: dict
    revision: str


def package_from_snapshot(source, markdown):
    manifest = _normalize(source)
    members = {skill["path"] for skill in manifest["skills"]}
    if set(markdown) != members:
        raise ValueError("Снимок пакета не соответствует списку Markdown в манифесте.")
    total = len(source.encode("utf-8"))
    clean = {}
    for path, content in markdown.items():
        size = len(content.encode("utf-8"))
        if size > MARKDOWN_BYTES or "\x00" in content or not (body := _clean_markdown(content)):
            raise ValueError(f"«{path}» — нужен непустой Markdown в UTF-8 до {MARKDOWN_BYTES} байт.")
        clean[path] = body
        total += size
    if total > PACKAGE_BYTES:
        raise ValueError("Суммарный размер пакета превышает 2 МиБ.")

    def instruction(entry):
        assembled, seen = [], set()

        def include(path):
            if path in seen:
                return
            seen.add(path)
            assembled.append(clean[path])
            for match in MARKDOWN_LINK.finditer(clean[path]):
                link = urlsplit(match.group(1).strip("<>"))
                local = unquote(link.path)
                if link.scheme or link.netloc or not local.lower().endswith(".md"):
                    continue
                if local.startswith(("/", "\\")) or PureWindowsPath(local).anchor:
                    raise ValueError(f"«{path}» — абсолютная ссылка на Markdown «{local}».")
                target = posixpath.normpath(posixpath.join(posixpath.dirname(path), local.replace("\\", "/")))
                if target not in members:
                    raise ValueError(f"«{path}» — добавьте Markdown «{target}» в пакет.")
                include(target)

        include(entry["path"])
        text = "\n\n".join(assembled)
        instruction_sections(text)
        return text

    instructions = {entry["name"]: instruction(entry) for entry in manifest["skills"]}
    encoded = json.dumps([source, markdown], sort_keys=True, ensure_ascii=False).encode("utf-8")
    return Package(source, manifest, markdown, instructions, hashlib.sha256(encoded).hexdigest())


def read_package(manifest_path):
    path = Path(manifest_path).absolute()
    root = path.parent.resolve(strict=True)
    try:
        source = read_member(root, path.name, MANIFEST_BYTES).decode("utf-8")
        manifest = _normalize(source)
        markdown = {}
        total = len(source.encode("utf-8"))
        for path in dict.fromkeys(skill["path"] for skill in manifest["skills"]):
            data = read_member(root, path, min(MARKDOWN_BYTES, PACKAGE_BYTES - total))
            try:
                markdown[path] = data.decode("utf-8")
            except UnicodeError as exc:
                raise ValueError(f"«{path}» — файл должен быть в UTF-8.") from exc
            total += len(data)
    except UnicodeError as exc:
        raise ValueError("Манифест должен быть в UTF-8.") from exc
    return package_from_snapshot(source, markdown)
