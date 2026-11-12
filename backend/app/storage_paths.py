"""Filesystem boundaries shared by model writes and desktop static serving."""
from pathlib import Path, PureWindowsPath

MAX_MODEL_UPLOAD_BYTES = 16 * 1024**3
_DEVICES = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$', 'CLOCK$'} | {
    prefix + number for prefix in ('COM', 'LPT') for number in '123456789¹²³'}


def checked_filename(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 240:
        raise ValueError("Invalid model filename")
    if value.strip(' .') == '' or value.endswith((' ', '.')):
        raise ValueError("Invalid model filename")
    try:
        if len(value.encode('utf-16-le')) // 2 > 240:
            raise ValueError("Invalid model filename")
    except UnicodeError:
        raise ValueError("Invalid model filename") from None
    if any(ord(ch) < 32 or ch in '/\\:*?"<>|' for ch in value):
        raise ValueError("Invalid model filename")
    windows = PureWindowsPath(value)
    if windows.drive or windows.root or value.split('.')[0].rstrip(' ').upper() in _DEVICES:
        raise ValueError("Invalid model filename")
    return value


def contained_path(root: Path, *parts: str) -> Path:
    base = root.resolve()
    target = base.joinpath(*parts).resolve()
    if not target.is_relative_to(base):
        raise ValueError("Path must remain inside its storage directory")
    return target


def frontend_path(root: Path, requested: str) -> Path:
    windows = PureWindowsPath(requested)
    if '\x00' in requested or windows.drive or windows.root or requested.startswith('/'):
        raise ValueError("Invalid static path")
    if any(part in {'.', '..'} for part in requested.replace('\\', '/').split('/')):
        raise ValueError("Invalid static path")
    return contained_path(root, requested)
