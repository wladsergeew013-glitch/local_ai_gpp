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
import time
from collections import deque
from functools import cache
from pathlib import Path
from workspace.app.companion_layout import AVATAR_SIZE, FACE_ANCHORS


def assets():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "workspace_assistant"
    return Path(__file__).resolve().parents[2] / "frontend/public/assistant"


def rendered_assets():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "workspace_assistant_3d"
    return Path(__file__).resolve().parents[1] / "assets/assistant-3d"


def load_portraits(root, *, native=False):
    from PIL import Image, ImageSequence, ImageTk

    def photo(frame):
        picture = frame.convert("RGBA")
        picture.thumbnail(AVATAR_SIZE, Image.Resampling.LANCZOS)
        return picture if native else ImageTk.PhotoImage(picture, master=root)

    pixel, studio = {}, {}
    for state, prefix in (("ready", "ready"), ("thinking", "thinking"), ("speaking", "answered")):
        frames = []
        for path in sorted((assets() / "frames").glob(f"{prefix}_*.png")):
            with Image.open(path) as frame:
                frames.append(photo(frame))
            if state == "ready":
                break
        pixel[state] = frames
        with Image.open(rendered_assets() / f"{state}.webp") as animation:
            studio[state] = [photo(frame) for frame in ImageSequence.Iterator(animation)]
    with Image.open(rendered_assets() / "documents.webp") as animation:
        studio["documents"] = [photo(frame) for frame in ImageSequence.Iterator(animation)]
    pixel["documents"] = pixel["thinking"]
    if not pixel["ready"]:
        with Image.open(icon_path().with_suffix(".png")) as fallback:
            pixel["ready"] = [photo(fallback)]
    return {"pixel": pixel, "studio": studio}


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

    api = ctypes.WinDLL("user32", use_last_error=True)
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


def desktop_work_areas():
    if os.name != "nt":
        return (work_area(),)
    import ctypes
    from ctypes import wintypes

    areas = []
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                  ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    @callback
    def visit(handle, dc, rect, data):
        box = rect.contents
        areas.append(work_area((box.left + box.right) // 2, (box.top + box.bottom) // 2))
        return True

    api = ctypes.WinDLL("user32", use_last_error=True)
    api.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), callback, wintypes.LPARAM]
    api.EnumDisplayMonitors(None, None, visit, 0)
    return tuple(dict.fromkeys(areas))


def clamp_position(x, y, width, height, bounds, screens=()):
    left, top, right, bottom = bounds
    cx, cy = max(left, min(x, right - width)), max(top, min(y, bottom - height))
    if (cx, cy) == (x, y) or len(screens) < 2:
        return cx, cy
    # An extended desktop can contain gaps. Allow straddling a seam only
    # when the entire portrait is covered by adjacent monitor work areas.
    for px, py in ((x, y), (x, cy), (cx, y)):
        covered = sum(max(0, min(px + width, r) - max(px, l)) * max(0, min(py + height, b) - max(py, t))
                      for l, t, r, b in screens)
        if covered == width * height:
            return px, py
    return cx, cy


@cache
def _position_api():
    import ctypes

    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    api.GetAncestor.restype = ctypes.c_void_p
    api.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    return api


def position_window(root, x, y, *, flush_layout=True):
    if os.name == "nt":
        import ctypes
        # Native desktop coordinates also support monitors left of the origin.
        api = _position_api()
        if flush_layout:
            root.update_idletasks()
        handle = api.GetAncestor(root.winfo_id(), 2)
        if not api.SetWindowPos(handle, None, x, y, 0, 0, 0x0015):
            raise ctypes.WinError()
    else:
        root.geometry(f"{x:+d}{y:+d}")


class MiniAvatar:
    def __init__(
        self,
        root,
        on_bubble,
        on_main,
        on_exit,
        *,
        start_hidden=False,
        api_base=None,
        ui_token=None,
        appearance=None,
    ):
        self.data = root
        self.api_base = api_base
        self.ui_token = ui_token
        self.read_appearance = appearance or (lambda: "studio")
        self.chat = None
        self.on_bubble = on_bubble
        self.on_main = on_main
        self.on_exit = on_exit
        self.commands = queue.SimpleQueue()
        self.width, self.height = AVATAR_SIZE
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

    def show_chat(self):
        self._submit("show_chat")

    def hide_chat(self):
        self._submit("hide_chat")

    def toggle_chat(self):
        self._submit("toggle_chat")

    def set_state(self, state):
        self._submit("state", state)

    def refresh_appearance(self):
        self._submit("appearance")

    def close(self):
        self._submit("close")
        self.thread.join(3)
        if self.thread.is_alive():
            raise RuntimeError("Workspace avatar thread did not stop.")

    def _save(self):
        target = self.data / "mini-helper.json"
        stage = target.with_suffix(".tmp")
        stage.write_text(
            json.dumps(
                {
                    "version": 2,
                    "x": self.position[0],
                    "y": self.position[1],
                    "chat": self.chat.prefs() if self.chat else {},
                },
                ensure_ascii=False,
            ),
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
        layer = None
        try:
            import tkinter as tk

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
            saved = {}
            try:
                saved = json.loads(
                    (self.data / "mini-helper.json").read_text(encoding="utf-8")
                )
                x, y = int(saved["x"]), int(saved["y"])
            except (OSError, ValueError, KeyError, TypeError):
                pass
            x, y = clamp_position(x, y, self.width, self.height, work_area(x, y), desktop_work_areas())
            self.position = (x, y)
            root.geometry(f"{self.width}x{self.height}")
            position_window(root, x, y)
            native = os.name == "nt"
            portraits = load_portraits(root, native=native)
            style = self.read_appearance()
            stage = tk.Canvas(root, bg=transparent, borderwidth=0, highlightthickness=0)
            stage.pack(fill="both", expand=True)
            if not native:
                avatar = stage.create_image(0, 0, tags="avatar")
                badge = stage.create_oval(0, 0, 0, 0, outline="#f5faff", width=2, tags="avatar")
                symbol = stage.create_text(0, 0, font=("Segoe UI", 13, "bold"), fill="white", tags="avatar")

            def place_avatar(x, y):
                if native:
                    if layer:
                        layer.sync(self.position, root.winfo_ismapped())
                    return
                stage.coords(avatar, x + self.width // 2, y + self.height // 2)
                bx, by = x + self.width - 26, y + 29
                stage.coords(badge, bx - 13, by - 13, bx + 13, by + 13)
                stage.coords(symbol, bx, by)

            place_avatar(0, 0)
            if self.api_base:
                from workspace.app.native_chat import NativeChatBubble

                self.chat = NativeChatBubble(
                    root,
                    self.api_base,
                    lambda: self.position,
                    self.on_main,
                    self._save,
                    prefs=saved.get("chat"),
                    hidden=self.start_hidden,
                    ui_token=self.ui_token,
                    stage=stage,
                    place_avatar=place_avatar,
                    face_anchor=lambda: FACE_ANCHORS[style],
                )
                root.bind("<Map>", lambda e: root.after_idle(self.chat.reposition)
                          if e.widget is root and self.chat else None, add="+")
            drag = {"origin": None, "moved": False, "bounds": None, "screens": ()}

            def begin(px, py):
                drag.update(origin=(px, py, *self.position), moved=False, bounds=work_area(px, py),
                            screens=desktop_work_areas())

            def move(pointer_x, pointer_y):
                if not drag["origin"]:
                    return
                px, py, ox, oy = drag["origin"]
                dx, dy = pointer_x - px, pointer_y - py
                if abs(dx) + abs(dy) > 4:
                    drag["moved"] = True
                left, top, right, bottom = drag["bounds"]
                if not (left <= pointer_x < right and top <= pointer_y < bottom):
                    drag["bounds"] = work_area(pointer_x, pointer_y)
                x, y = clamp_position(
                    ox + dx,
                    oy + dy,
                    self.width,
                    self.height,
                    drag["bounds"],
                    drag["screens"],
                )
                if (x, y) != self.position:
                    self.position = (x, y)
                    if self.chat:
                        self.chat.reposition(bounds=drag["bounds"])
                    else:
                        position_window(root, x, y, flush_layout=False)

            def end():
                if not drag["origin"]:
                    return
                if drag["moved"]:
                    self._save()
                else:
                    if self.chat:
                        self.chat.toggle()
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
            menu.add_command(label="Скрыть помощника", command=self.hide)
            menu.add_separator()
            menu.add_command(
                label="Выход из Workspace",
                command=lambda: threading.Thread(
                    target=self.on_exit, daemon=True
                ).start(),
            )

            def open_menu(x, y):
                try:
                    menu.tk_popup(x, y)
                finally:
                    menu.grab_release()

            if native:
                from workspace.app.windows_portrait import PortraitWindow

                # The native callback and Tk run on the same thread. Keep only
                # the latest motion between button events, preserving their order.
                pointer_events = deque()
                def enqueue_pointer(name, point):
                    event = (name, point)
                    if name == "portrait_move" and pointer_events and pointer_events[-1][0] == name:
                        pointer_events[-1] = event
                    else:
                        pointer_events.append(event)

                def handle_pointer():
                    nonlocal pointer_timer
                    while pointer_events:
                        name, point = pointer_events.popleft()
                        if name == "portrait_press":
                            begin(*point)
                        elif name == "portrait_move":
                            move(*point)
                        elif name == "portrait_release":
                            move(*point)
                            end()
                        elif name == "portrait_cancel":
                            drag["origin"] = None
                        elif name == "portrait_menu":
                            open_menu(*point)
                    interval = 4 if drag["origin"] else (16 if self.visible else 100)
                    pointer_timer = root.after(interval, handle_pointer)

                layer = PortraitWindow(root, AVATAR_SIZE, enqueue_pointer)
                pointer_timer = root.after(8, handle_pointer)
            else:
                stage.tag_bind("avatar", "<ButtonPress-1>", lambda e: begin(e.x_root, e.y_root))
                stage.bind("<B1-Motion>", lambda e: move(e.x_root, e.y_root))
                stage.bind("<ButtonRelease-1>", lambda e: end())
                stage.tag_bind("avatar", "<Button-3>", lambda e: open_menu(e.x_root, e.y_root))
            epoch = time.monotonic()
            last_image = None
            last_badge = None

            def tick():
                nonlocal style, epoch, last_image, last_badge
                while not self.commands.empty():
                    name, value = self.commands.get_nowait()
                    if name == "close":
                        self._save()
                        if self.chat:
                            self.chat.close()
                            self.chat = None
                        if layer:
                            root.after_cancel(pointer_timer)
                            layer.close()
                        else:
                            stage.itemconfigure(avatar, image="")
                        last_image = None
                        portraits.clear()
                        root.destroy()
                        return
                    if name == "show":
                        root.deiconify()
                        root.lift()
                        self.visible = True
                    if name == "hide":
                        if self.chat:
                            self.chat.hide()
                        root.withdraw()
                        self.visible = False
                    if name == "show_chat" and self.chat:
                        self.chat.show()
                    if name == "hide_chat" and self.chat:
                        self.chat.hide()
                    if name == "toggle_chat" and self.chat:
                        self.chat.toggle()
                    if name == "appearance":
                        style = self.read_appearance()
                        self.frame_counts = {name: len(frames) for name, frames in portraits[style].items()}
                        epoch = time.monotonic()
                        if self.chat and self.chat.visible:
                            self.chat.reposition()
                    if name == "state" and value != self.state:
                        self.state = value
                        epoch = time.monotonic()
                pictures = portraits[style]
                frames = pictures.get(self.state) or pictures["ready"]
                delay = 1 / 14 if style == "studio" else (.35 if self.state in {"thinking", "documents"} else .65)
                current = int((time.monotonic() - epoch) / delay) % len(frames)
                image = frames[current]
                badge_style = {
                    "ready": ("✓", "#25a957"),
                    "thinking": ("?", "#1e67cd"),
                    "documents": ("?", "#1e67cd"),
                    "speaking": ("…", "#e3a51d"),
                    "error": ("!", "#c5453c"),
                    "awaiting_permission": ("!", "#e3a51d"),
                }.get(self.state, ("✓", "#25a957")) if style == "pixel" else None
                if last_image is not image or last_badge != badge_style:
                    if layer:
                        layer.paint(image, badge_style)
                    else:
                        stage.itemconfigure(avatar, image=image)
                        if badge_style:
                            glyph, color = badge_style
                            stage.itemconfigure(symbol, text=glyph, state="normal")
                            stage.itemconfigure(badge, fill=color, state="normal")
                        else:
                            stage.itemconfigure(symbol, state="hidden")
                            stage.itemconfigure(badge, state="hidden")
                    last_image, last_badge = image, badge_style
                if layer:
                    layer.sync(self.position, root.winfo_ismapped())
                root.after(35 if self.visible else 100, tick)

            self.frame_counts = {name: len(frames) for name, frames in portraits[style].items()}
            self.initialized.set()
            tick()
            root.mainloop()
        except BaseException as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.initialized.set()
            import traceback

            traceback.print_exc()
        finally:
            if layer:
                layer.close()
            if self.chat:
                with contextlib.suppress(Exception):
                    self.chat.close()
                self.chat = None
            if root:
                with contextlib.suppress(Exception):
                    root.destroy()
            self.stopped.set()
