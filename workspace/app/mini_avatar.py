"""Workspace's companion avatar. Its bubble talks to Workspace tasks over HTTP.

Tk is owned by one thread. Other threads submit commands through a bounded
event loop; no model, Runtime registry or diagnostic-chat imports live here.
"""

from __future__ import annotations
import contextlib
import json
import os
import queue
import sys
import threading
from pathlib import Path


def assets():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "workspace_assistant"
    return Path(__file__).resolve().parents[2] / "frontend/public/assistant"


def icon_path():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "workspace_icons/mini_agent_head_v2.ico"
    return (
        Path(__file__).resolve().parents[2]
        / "models_storage/branding/icons/mini_agent_head_v2.ico"
    )


def work_area(x=None, y=None):
    if os.name != "nt":
        return (0, 0, 1920, 1040)
    import ctypes
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("monitor", wintypes.RECT),
            ("work", wintypes.RECT),
            ("flags", wintypes.DWORD),
        ]

    api = ctypes.windll.user32
    if x is None or y is None:
        rect = wintypes.RECT()
        api.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        return rect.left, rect.top, rect.right, rect.bottom
    api.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    api.MonitorFromPoint.restype = ctypes.c_void_p
    api.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(Info)]
    handle = api.MonitorFromPoint(wintypes.POINT(x, y), 2)
    info = Info(size=ctypes.sizeof(Info))
    if handle and api.GetMonitorInfoW(handle, ctypes.byref(info)):
        return info.work.left, info.work.top, info.work.right, info.work.bottom
    return work_area()


def clamp_position(x, y, width, height, bounds):
    left, top, right, bottom = bounds
    return max(left, min(x, right - width)), max(top, min(y, bottom - height))


def position_window(root, x, y):
    if os.name == "nt":
        # Tk treats a negative geometry offset as distance from the right edge.
        # Windows desktop coordinates can be negative on a secondary monitor.
        import ctypes

        api = ctypes.windll.user32
        api.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        api.GetAncestor.restype = ctypes.c_void_p
        api.SetWindowPos.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        root.update_idletasks()
        handle = api.GetAncestor(root.winfo_id(), 2)
        if not api.SetWindowPos(handle, None, x, y, 0, 0, 0x0015):
            raise ctypes.WinError()
    else:
        root.geometry(f"{x:+d}{y:+d}")


class MiniAvatar:
    def __init__(self, root, on_bubble, on_main, on_exit, *, start_hidden=False):
        self.data = root
        self.on_bubble = on_bubble
        self.on_main = on_main
        self.on_exit = on_exit
        self.commands = queue.SimpleQueue()
        self.width = 154
        self.height = 210
        self.state = "ready"
        self.visible = True
        self.initialized = threading.Event()
        self.error = None
        self.start_hidden = start_hidden
        self.frame_counts = {}
        self.stopped = threading.Event()
        self.position = (0, 0)
        self.thread = threading.Thread(
            target=self._run, daemon=True, name="workspace-avatar"
        )
        self.thread.start()

    def _submit(self, name, value=None):
        self.commands.put((name, value))

    def show(self):
        self._submit("show")

    def hide(self):
        self._submit("hide")

    def set_state(self, state):
        self._submit("state", state)

    def close(self):
        self._submit("close")
        self.thread.join(3)
        if self.thread.is_alive():
            raise RuntimeError("Workspace avatar thread did not stop.")

    def _save(self):
        target = self.data / "mini-helper.json"
        stage = target.with_suffix(".tmp")
        stage.write_text(
            json.dumps({"version": 1, "x": self.position[0], "y": self.position[1]}),
            encoding="utf-8",
        )
        os.replace(stage, target)

    def _run(self):
        try:
            self._tk_loop()
        finally:
            # Tcl and PhotoImage finalizers belong to the thread that created them.
            import gc

            gc.collect()

    def _tk_loop(self):
        root = None
        try:
            import tkinter as tk
            from PIL import Image, ImageTk

            root = tk.Tk()
            if self.start_hidden:
                root.withdraw()
            root.title("Local AI Workspace · мини-помощник")
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            transparent = "#010203"
            root.configure(bg=transparent)
            if os.name == "nt":
                root.wm_attributes("-transparentcolor", transparent)
            if icon_path().is_file():
                root.iconbitmap(default=str(icon_path()))
            left, top, right, bottom = work_area()
            x, y = right - self.width - 30, bottom - self.height - 25
            try:
                saved = json.loads(
                    (self.data / "mini-helper.json").read_text(encoding="utf-8")
                )
                x, y = int(saved["x"]), int(saved["y"])
            except (OSError, ValueError, KeyError, TypeError):
                pass
            x, y = clamp_position(x, y, self.width, self.height, work_area(x, y))
            self.position = (x, y)
            root.geometry(f"{self.width}x{self.height}")
            position_window(root, x, y)
            pictures = {}
            for state, prefix in [
                ("ready", "ready"),
                ("thinking", "thinking"),
                ("speaking", "answered"),
            ]:
                frames = []
                for index in range(1 if state == "ready" else 4):
                    path = assets() / "frames" / f"{prefix}_{index}.png"
                    if not path.is_file():
                        continue
                    with Image.open(path) as original:
                        picture = original.convert("RGBA")
                        picture.thumbnail(
                            (self.width, self.height), Image.Resampling.LANCZOS
                        )
                    frames.append(ImageTk.PhotoImage(picture, master=root))
                pictures[state] = frames
            avatar = tk.Label(root, bg=transparent, borderwidth=0)
            avatar.pack(fill="both", expand=True)
            if not pictures["ready"]:
                fallback = icon_path().with_suffix(".png")
                if fallback.is_file():
                    with Image.open(fallback) as original:
                        picture = original.convert("RGBA")
                        picture.thumbnail(
                            (self.width, self.height), Image.Resampling.LANCZOS
                        )
                    pictures["ready"] = [ImageTk.PhotoImage(picture, master=root)]
                else:
                    avatar.configure(
                        text="W", fg="#2867dc", font=("Segoe UI", 60, "bold")
                    )
            badge = tk.Label(
                root,
                text="W",
                font=("Segoe UI", 11, "bold"),
                fg="white",
                bg="#2969df",
                padx=5,
                pady=2,
            )
            badge.place(relx=0.74, rely=0.08)
            drag = {"origin": None, "moved": False}

            def begin(event):
                drag.update(
                    origin=(event.x_root, event.y_root, *self.position), moved=False
                )

            def move(event):
                if not drag["origin"]:
                    return
                px, py, ox, oy = drag["origin"]
                dx, dy = event.x_root - px, event.y_root - py
                if abs(dx) + abs(dy) > 4:
                    drag["moved"] = True
                x, y = clamp_position(
                    ox + dx,
                    oy + dy,
                    self.width,
                    self.height,
                    work_area(event.x_root, event.y_root),
                )
                self.position = (x, y)
                position_window(root, x, y)

            def end(event):
                if drag["moved"]:
                    self._save()
                else:
                    threading.Thread(
                        target=lambda: self.on_bubble(self.position), daemon=True
                    ).start()
                drag["origin"] = None

            menu = tk.Menu(root, tearoff=False)
            menu.add_command(
                label="Открыть мини-помощника",
                command=lambda: threading.Thread(
                    target=lambda: self.on_bubble(self.position), daemon=True
                ).start(),
            )
            menu.add_command(
                label="Открыть Workspace",
                command=lambda: threading.Thread(
                    target=self.on_main, daemon=True
                ).start(),
            )
            menu.add_command(label="Скрыть помощника", command=root.withdraw)
            menu.add_separator()
            menu.add_command(
                label="Выход из Workspace",
                command=lambda: threading.Thread(
                    target=self.on_exit, daemon=True
                ).start(),
            )

            def context(event):
                try:
                    menu.tk_popup(event.x_root, event.y_root)
                finally:
                    menu.grab_release()

            for widget in (avatar, badge):
                widget.bind("<ButtonPress-1>", begin)
                widget.bind("<B1-Motion>", move)
                widget.bind("<ButtonRelease-1>", end)
                widget.bind("<Button-3>", context)
            frame = [0]

            def tick():
                while not self.commands.empty():
                    name, value = self.commands.get_nowait()
                    if name == "close":
                        self._save()
                        root.destroy()
                        return
                    if name == "show":
                        root.deiconify()
                        root.lift()
                    if name == "hide":
                        root.withdraw()
                    if name == "state":
                        self.state = value
                        frame[0] = 0
                frames = pictures.get(self.state) or pictures["ready"]
                if frames:
                    avatar.configure(image=frames[frame[0] % len(frames)])
                    frame[0] += 1
                badge.configure(
                    text=(
                        "!"
                        if self.state == "awaiting_permission"
                        else ("…" if self.state == "thinking" else "W")
                    )
                )
                root.after(350 if self.state == "thinking" else 650, tick)

            self.frame_counts = {name: len(frames) for name, frames in pictures.items()}
            self.initialized.set()
            tick()
            root.mainloop()
        except BaseException as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.initialized.set()
            import traceback

            traceback.print_exc()
        finally:
            if root:
                with contextlib.suppress(Exception):
                    root.destroy()
            self.stopped.set()
