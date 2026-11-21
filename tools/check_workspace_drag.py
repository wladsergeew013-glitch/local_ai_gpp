"""Measure dragging the installed Windows portrait using a disposable Workspace profile."""

import argparse
import ctypes
import json
import os
import socket
import subprocess
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

WIDTH, HEIGHT = 154, 210
SAMPLES_PER_SECOND = 400
SPEED = 640


class WindowsInput:
    def __init__(self):
        user = ctypes.WinDLL("user32", use_last_error=True)

        def bind(name, result, *arguments):
            fn = getattr(user, name)
            fn.restype, fn.argtypes = result, arguments
            return fn

        self.find = bind("FindWindowExW", wintypes.HWND, wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
        self.owner = bind("GetWindow", wintypes.HWND, wintypes.HWND, wintypes.UINT)
        self.pid_of = bind("GetWindowThreadProcessId", wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        self.get_rect = bind("GetWindowRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        self.visible = bind("IsWindowVisible", wintypes.BOOL, wintypes.HWND)
        self.send = bind("SendMessageTimeoutW", wintypes.LPARAM, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                         wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
        bind("SetThreadDpiAwarenessContext", wintypes.HANDLE, wintypes.HANDLE)(-4)
        self.primary = wintypes.RECT()
        if not user.SystemParametersInfoW(0x30, 0, ctypes.byref(self.primary), 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def portrait(self, pid):
        window = None
        while True:
            window = self.find(None, window, "STATIC", "Local AI Workspace Portrait")
            if not window:
                return None
            owner_pid = wintypes.DWORD()
            self.pid_of(window, ctypes.byref(owner_pid))
            if owner_pid.value == pid and self.visible(window):
                return window

    def rect(self, window):
        box = wintypes.RECT()
        if not self.get_rect(window, ctypes.byref(box)):
            raise ctypes.WinError(ctypes.get_last_error())
        return box.left, box.top, box.right - box.left, box.bottom - box.top

    def mouse(self, window, message, x, y, *, down=False):
        # Client coordinates are signed on captured mouse moves outside the portrait.
        coords = ((int(y) & 0xffff) << 16) | (int(x) & 0xffff)
        result = ctypes.c_size_t()
        if not self.send(window, message, int(down), coords, 2, 1000, ctypes.byref(result)):
            raise RuntimeError("The test window did not process a mouse message within one second.")


def wait_for(condition, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(.03)
    raise RuntimeError("Timed out waiting for the disposable Workspace window.")


def measure(windows, portrait, panel, opened):
    if opened:
        windows.mouse(portrait, 0x201, 77, 100, down=True)
        windows.mouse(portrait, 0x202, 77, 100)
        wait_for(lambda: windows.rect(panel)[2] > WIDTH)
    time.sleep(.18)
    lag, frame_ms, final_error = [], [], []
    for distance in (SPEED, -SPEED, SPEED, -SPEED):
        x, y, _, _ = windows.rect(portrait)
        windows.mouse(portrait, 0x201, 77, 100, down=True)
        time.sleep(.04)
        start = last_change = time.perf_counter()
        previous = (x, y)
        for sample in range(1, SAMPLES_PER_SECOND + 1):
            delay = start + sample / SAMPLES_PER_SECOND - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            now = time.perf_counter()
            target_x = x + round(distance * min(1, now - start))
            actual_x, actual_y, _, _ = windows.rect(portrait)
            lag.append(abs(target_x - actual_x))
            if (actual_x, actual_y) != previous:
                frame_ms.append((now - last_change) * 1000)
                previous, last_change = (actual_x, actual_y), now
            windows.mouse(portrait, 0x200, target_x + 77 - actual_x, y + 100 - actual_y, down=True)
        time.sleep(.12)
        actual_x, actual_y, _, _ = windows.rect(portrait)
        windows.mouse(portrait, 0x202, x + distance + 77 - actual_x, y + 100 - actual_y)
        time.sleep(.2)
        end_x, end_y, _, _ = windows.rect(portrait)
        final_error.append(abs(end_x - x - distance) + abs(end_y - y))
        if (windows.rect(panel)[2] > WIDTH) != opened:
            raise RuntimeError("Dragging unexpectedly toggled the chat.")
    if not frame_ms:
        raise RuntimeError("The portrait did not move.")

    def p95(values):
        return round(sorted(values)[round((len(values) - 1) * .95)], 2)

    return {"p95_lag_px": p95(lag), "max_lag_px": max(lag), "p95_move_interval_ms": p95(frame_ms),
            "max_move_interval_ms": round(max(frame_ms), 2), "final_position_error_px": final_error}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default_exe = Path(__file__).resolve().parents[1] / "dist/worker/LocalAIWorkspace.exe"
    parser.add_argument("--exe", type=Path, default=default_exe)
    parser.add_argument("--start-x", type=int, help="Initial desktop X coordinate, for example near a monitor seam")
    parser.add_argument("--start-y", type=int)
    parser.add_argument("--output", type=Path, help="Create a new JSON result")
    parser.add_argument("--max-lag-px", type=float, help="Fail if either 95th percentile exceeds this value")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This check requires a Windows desktop.")
    if args.output and args.output.exists():
        parser.error("The output file already exists.")
    exe = args.exe.resolve(strict=True)
    windows = WindowsInput()
    box = windows.primary
    x = args.start_x if args.start_x is not None else (box.left + box.right - WIDTH - SPEED) // 2
    y = args.start_y if args.start_y is not None else (box.top + box.bottom - HEIGHT) // 2
    with tempfile.TemporaryDirectory(prefix="workspace-drag-", ignore_cleanup_errors=True) as folder:
        (Path(folder) / "mini-helper.json").write_text(json.dumps({"x": x, "y": y}), encoding="utf8")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        process = subprocess.Popen([str(exe), "--data-dir", folder, "--port", str(port)], cwd=exe.parent,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            portrait = wait_for(lambda: windows.portrait(process.pid))
            wait_for(lambda: windows.rect(portrait)[:2] == (x, y))
            panel = windows.owner(portrait, 4)
            results = {name: measure(windows, portrait, panel, opened)
                       for name, opened in [("chat_closed", False), ("chat_open", True)]}
        finally:
            process.terminate()
            process.wait(timeout=15)
    payload = json.dumps({"input_hz": SAMPLES_PER_SECOND, "speed_px_s": SPEED, "start": [x, y], **results}, indent=2)
    print(payload)
    if args.output:
        with args.output.open("x", encoding="utf8") as stream:
            stream.write(payload + "\n")
    return int(any(any(r["final_position_error_px"]) or
                   (args.max_lag_px is not None and r["p95_lag_px"] > args.max_lag_px) for r in results.values()))


if __name__ == "__main__":
    raise SystemExit(main())
