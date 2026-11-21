"""The original blue speech panel, owned by the avatar's Tk thread.

This view talks only to Workspace HTTP and uses native dialogs for file selection.
"""

from __future__ import annotations
import contextlib
import datetime
import os
import queue
import tempfile
import threading
import time
import uuid
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
import httpx
from workspace.app.companion_layout import AVATAR_SIZE, FACE_ANCHORS, attached_layout
from workspace.app.control_access import UI_TOKEN_HEADER

BG = "#fbfdff"
BORDER = "#0e4f97"
HEADER = "#1d559c"
TRANSPARENT = "#010203"
ACTIVE = {"starting", "running", "awaiting_permission", "stopping"}


class NativeChatBubble:
    def __init__(
        self,
        root,
        base_url,
        avatar_position,
        on_main,
        save,
        *,
        prefs=None,
        hidden=False,
        transport=None,
        ui_token=None,
        stage=None,
        place_avatar=None,
        face_anchor=lambda: FACE_ANCHORS["pixel"],
    ):
        self.root, self.base_url = root, base_url.rstrip("/")
        self.avatar_position, self.on_main, self.save = avatar_position, on_main, save
        prefs = prefs if isinstance(prefs, dict) else {}
        self.selected = str(prefs.get("selected") or "")
        self.drafts = (
            {
                k: v
                for k, v in (prefs.get("drafts") or {}).items()
                if isinstance(k, str) and isinstance(v, str)
            }
            if isinstance(prefs.get("drafts"), dict)
            else {}
        )
        self.width = max(420, min(760, int(prefs.get("width", 512))))
        self.height = max(260, min(900, int(prefs.get("height", 304))))
        self.hidden_test, self.transport = hidden, transport
        self.ui_token = ui_token
        self.file_drafts = {}
        self.stage = stage if stage is not None else tk.Canvas(
            root, bg=TRANSPARENT, highlightthickness=0, borderwidth=0
        )
        if stage is None:
            self.stage.pack(fill="both", expand=True)
        self.place_avatar = place_avatar
        self.face_anchor = face_anchor
        self.surface_size = None
        self.panel_layout = None
        self.visible = self.sidebar_open = self.polling = self.sending = self.closed = (
            False
        )
        self.boot, self.current, self.messages = {}, None, []
        self.version = -1
        self.memory_chat = None
        self.send_error = ""
        self.pending_revision = None
        self.dialog_ids, self.list_signature, self.history_signature = [], None, None
        self.outputs = queue.SimpleQueue()
        self.next_poll = 0
        self._build()
        self.timer = self.root.after(100, self._pump)

    def _request(self, path, body=None):
        if self.transport:
            return self.transport(path, body)
        with httpx.Client(
            base_url=self.base_url + "/api",
            trust_env=False,
            timeout=15,
            headers={UI_TOKEN_HEADER: self.ui_token} if self.ui_token else {},
        ) as client:
            response = (
                client.get(path) if body is None else client.post(path, json=body)
            )
            if response.is_error:
                with contextlib.suppress(ValueError):
                    detail = response.json().get("detail")
                    if isinstance(detail, str):
                        raise RuntimeError(detail)
            response.raise_for_status()
            return response.json()

    def download_artifact(self, chat_id, artifact):
        selected = filedialog.asksaveasfilename(
            parent=self.root, initialfile=artifact["name"], title="Сохранить файл из диалога"
        )
        if not selected:
            return
        destination = Path(selected)

        def download():
            temporary = None
            try:
                url = self.base_url + f"/api/chats/{chat_id}/artifacts/{artifact['id']}"
                with httpx.Client(trust_env=False, timeout=30) as client, client.stream(
                    "GET", url, headers={UI_TOKEN_HEADER: self.ui_token}
                ) as response:
                    response.raise_for_status()
                    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".workspace-file-",
                                                     delete=False) as file:
                        temporary = Path(file.name)
                        for chunk in response.iter_bytes():
                            file.write(chunk)
                os.replace(temporary, destination)
            except (OSError, httpx.HTTPError) as exc:
                self.outputs.put(("error", f"Файл не сохранён. {exc}"))
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

        threading.Thread(target=download, daemon=True).start()

    def _build(self):
        self.window = tk.Frame(self.root, bg=BORDER)
        self.root.bind("<Escape>", lambda _: self.hide() if self.visible else None)
        self.tail_canvas = self.stage
        self.tail_outline = self.stage.create_polygon(
            0, 0, 0, 0, 0, 0, fill=BORDER, outline=BORDER, state="hidden", tags="pointer"
        )
        self.tail_fill = self.stage.create_polygon(
            0, 0, 0, 0, 0, 0, fill=BG, outline=BG, state="hidden", tags="pointer"
        )
        self.stage.tag_lower("pointer")
        header = tk.Frame(self.window, bg=HEADER, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)
        self.title_label = tk.Label(
            header,
            text="На связи",
            bg=HEADER,
            fg="white",
            font=("Segoe UI", 18, "bold"),
            anchor="w",
        )
        self.title_label.place(x=54, y=8, width=300, height=26)
        self.status_label = tk.Label(
            header,
            text="● Готов помочь",
            bg=HEADER,
            fg="#53f079",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )
        self.status_label.place(x=54, y=34, width=340, height=16)
        menu = tk.Label(
            header,
            text="☰",
            bg="#2f67ad",
            fg="white",
            font=("Segoe UI", 17, "bold"),
            cursor="hand2",
        )
        menu.place(x=12, y=11, width=32, height=34)
        menu.bind("<Button-1>", lambda _: self.toggle_sidebar())
        self.close_button = tk.Label(
            header,
            text="×",
            bg="#2f67ad",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            cursor="hand2",
        )
        self.close_button.place(relx=1, x=-46, y=11, width=34, height=34)
        self.close_button.bind("<Button-1>", lambda _: self.hide())
        self.open_button = tk.Label(
            header,
            text="↗",
            bg="#2f67ad",
            fg="white",
            font=("Segoe UI", 14),
            cursor="hand2",
        )
        self.open_button.place(relx=1, x=-83, y=11, width=30, height=34)
        self.open_button.bind("<Button-1>", lambda _: self.open_main())
        self.content = tk.Frame(self.window, bg=BG)
        self.content.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.sidebar = tk.Frame(self.content, bg="#edf4fb", width=166, padx=6, pady=10)
        self.sidebar.pack_propagate(False)
        tk.Button(
            self.sidebar,
            text="＋ Новый диалог",
            command=self.new_chat,
            bg="white",
            fg="#155eb9",
            relief="flat",
            font=("Segoe UI", 9),
        ).pack(fill="x", pady=(0, 8))
        tk.Button(
            self.sidebar,
            text="Скрыть меню",
            command=self.toggle_sidebar,
            bg="#edf4fb",
            fg="#155eb9",
            relief="flat",
            font=("Segoe UI", 9),
        ).pack(side="bottom", fill="x")
        self.listbox = tk.Listbox(
            self.sidebar,
            bg="#edf4fb",
            fg="#16324e",
            selectbackground="#c7ddf8",
            selectforeground="#145aa0",
            relief="flat",
            bd=0,
            highlightthickness=0,
            exportselection=False,
            activestyle="none",
            font=("Segoe UI", 9),
        )
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._choose_chat)
        self.body = tk.Frame(self.content, bg=BG, padx=14, pady=12)
        self.body.pack(side="left", fill="both", expand=True)
        self.history = tk.Text(
            self.body,
            wrap="word",
            bg=BG,
            fg="#0b1b33",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=("Segoe UI", 10),
            height=9,
        )
        for role in ("user", "assistant"):
            alignment = "right" if role == "user" else "left"
            left, right = (40, 4) if role == "user" else (4, 40)
            self.history.tag_configure(
                role,
                foreground="#155eb9",
                font=("Segoe UI", 10, "bold"),
                justify=alignment,
                lmargin1=left,
                lmargin2=left,
                rmargin=right,
            )
            self.history.tag_configure(
                "body-" + role,
                justify=alignment,
                lmargin1=left,
                lmargin2=left,
                rmargin=right,
            )
            self.history.tag_configure(
                "time-" + role,
                foreground="#66809e",
                font=("Segoe UI", 9),
                justify=alignment,
            )
        self.history.tag_configure("error", foreground="#ba3c32")
        self.history.configure(state="disabled")
        controls = tk.Frame(self.body, bg=BG)
        controls.pack(side="bottom", fill="x", pady=(8, 0))
        self.memory_var = tk.BooleanVar(self.window, value=True)
        self.file_button = tk.Button(controls, text="+ Файлы", command=self.choose_documents,
                                     bg="#edf4fb", fg="#155eb9", relief="flat", font=("Segoe UI", 9))
        self.file_button.pack(side="left", padx=(0, 5))
        tk.Checkbutton(
            controls,
            text="Память диалога",
            variable=self.memory_var,
            bg=BG,
            activebackground=BG,
            fg="#66809e",
            font=("Segoe UI", 9),
            bd=0,
        ).pack(side="left")
        tk.Button(
            controls,
            text="Настройки",
            command=lambda: self.open_main(True),
            bg="#edf4fb",
            fg="#155eb9",
            relief="flat",
            font=("Segoe UI", 9),
        ).pack(side="right")
        compose = tk.Frame(self.body, bg=BG)
        compose.pack(side="bottom", fill="x", pady=(8, 0))
        self.input_var = tk.StringVar(
            self.window, value=self.drafts.get(self.selected, "")
        )
        self.entry = tk.Entry(
            compose,
            textvariable=self.input_var,
            font=("Segoe UI", 11),
            relief="solid",
            bd=1,
            highlightthickness=2,
            highlightbackground="#b8d0ea",
            highlightcolor="#1d5fa8",
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=7)
        self.entry.bind("<Return>", lambda _: self._enter())
        self.send_button = tk.Label(
            compose,
            text="↵",
            bg="#b5cbe2",
            fg="white",
            font=("Segoe UI", 21, "bold"),
            cursor="hand2",
        )
        self.send_button.pack(side="left", padx=(10, 0), ipadx=16, ipady=3)
        self.send_button.bind("<Button-1>", lambda _: self.send())
        self.input_var.trace_add("write", lambda *_: self._edited())
        self.history.pack(fill="both", expand=True)
        context = tk.Menu(self.entry, tearoff=0)
        for title, event in (
            ("Вставить", "<<Paste>>"),
            ("Копировать", "<<Copy>>"),
            ("Вырезать", "<<Cut>>"),
        ):
            context.add_command(
                label=title, command=lambda e=event: self.entry.event_generate(e)
            )
        self.entry.bind("<Button-3>", lambda e: context.tk_popup(e.x_root, e.y_root))

    def prefs(self):
        self.drafts[self.selected] = self.input_var.get()
        return {
            "selected": self.selected,
            "drafts": self.drafts,
            "width": self.width,
            "height": self.height,
        }

    def _edited(self):
        if self.closed:
            return
        self.drafts[self.selected] = self.input_var.get()
        self._refresh_button()

    def _enter(self):
        if not self.current or self.current.get("status") not in ACTIVE:
            self.send()
        return "break"

    def _refresh_button(self):
        working = bool(self.current and self.current.get("status") in ACTIVE)
        count = len(self.file_drafts.get(self.selected, ()))
        agent = bool(self.current and self.current.get("details", {}).get("mode") == "agent")
        self.file_button.configure(text=f"Файлы · {count}" if count else "+ Файлы",
                                   state="disabled" if working or self.sending or agent else "normal")
        available = working or (
            bool(self.input_var.get().strip())
            and bool(self.boot.get("providers"))
            and not self.sending
        )
        self.send_button.configure(
            text="■" if working else "↵", bg="#1d5fa8" if available else "#b5cbe2"
        )

    def choose_documents(self):
        selected = self.selected
        paths = self.file_drafts.setdefault(selected, [])
        def add():
            chosen = filedialog.askopenfilenames(parent=self.root, title="Прикрепить файлы к сообщению")
            paths.extend(Path(p) for p in chosen)
            self._refresh_button()
        if not paths:
            add()
            return
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="Добавить файлы…", command=add)
        menu.add_separator()
        for i, path in enumerate(paths):
            menu.add_command(label="Убрать " + path.name, command=lambda i=i: paths.pop(i))
        try:
            menu.tk_popup(self.file_button.winfo_rootx(), self.file_button.winfo_rooty())
        finally:
            menu.grab_release()

    def upload_documents(self, chat_id, paths):
        result = []
        with httpx.Client(base_url=self.base_url + "/api", trust_env=False, timeout=60,
                          headers={UI_TOKEN_HEADER: self.ui_token}) as client:
            for path in paths:
                with path.open("rb") as file:
                    r = client.post(f"/chats/{chat_id}/documents", params={"filename": path.name}, content=file)
                if not r.is_success:
                    raise ValueError(r.json().get("detail", "Не удалось прикрепить файл."))
                result.append(r.json()["id"])
        return result

    def toggle_sidebar(self):
        self.sidebar_open = not self.sidebar_open
        if self.sidebar_open:
            self.sidebar.pack(side="left", fill="y", before=self.body)
        else:
            self.sidebar.pack_forget()

    def _choose_chat(self, _=None):
        chosen = self.listbox.curselection()
        if chosen and chosen[0] < len(self.dialog_ids):
            self.select(self.dialog_ids[chosen[0]])

    def select(self, chat_id):
        if chat_id == self.selected:
            return
        self.drafts[self.selected] = self.input_var.get()
        self.selected = chat_id
        self.version = -1
        self.current = None
        self.messages = []
        self.send_error = ""
        self.pending_revision = None
        self.history_signature = None
        self.input_var.set(self.drafts.get(chat_id, ""))
        self.next_poll = 0
        self.save()
        self._render()

    def new_chat(self):
        self.select("")
        self.input_var.set(self.drafts.get("", ""))
        self.title_label.configure(text="Новый диалог")
        self.entry.focus_set()

    def open_main(self, settings=False):
        payload = {"task_id": self.selected}
        if settings:
            payload["panel"] = "assistant"
        threading.Thread(target=lambda: self.on_main(payload), daemon=True).start()

    def show(self):
        self.visible = True
        self.reposition()
        if not self.hidden_test:
            self.root.lift()
            self.entry.focus_set()
        self.next_poll = 0

    def hide(self):
        self.drafts[self.selected] = self.input_var.get()
        self.visible = False
        self.panel_layout = None
        self.window.place_forget()
        self.stage.itemconfigure("pointer", state="hidden")
        self.reposition()
        self.save()
        return "break"

    def toggle(self):
        self.hide() if self.visible else self.show()

    def reposition(self, *, bounds=None):
        from workspace.app.mini_avatar import position_window, work_area

        ax, ay = self.avatar_position()
        aw, ah = AVATAR_SIZE
        layout_changed = False
        if self.visible:
            if bounds is None:
                bounds = work_area(ax + aw // 2, ay + ah // 2)
            layout = attached_layout((ax, ay), (self.width, self.height), bounds, self.face_anchor())
            (gx, gy), size, offset = layout.origin, layout.size, layout.avatar
            x, y, w, h = layout.panel
            p = layout.pointer
            if self.panel_layout != (layout.panel, p):
                layout_changed = True
                self.panel_layout = (layout.panel, p)
                self.window.place(x=x, y=y, width=w, height=h)
                self.window.lift()
                self.stage.coords(self.tail_outline, *p)
                center = ((p[0] + p[4]) / 2, (p[1] + p[5]) / 2)
                inner = [center[i % 2] + (v - center[i % 2]) * .73 for i, v in enumerate(p)]
                self.stage.coords(self.tail_fill, *inner)
                self.stage.itemconfigure("pointer", state="normal")
            self.anchor = {"x": gx + x, "y": gy + y, "side": layout.side,
                           "tail_x": gx + p[0], "tip": (gx + p[2], gy + p[3])}
        else:
            gx, gy, size, offset = ax, ay, AVATAR_SIZE, (0, 0)
        if self.place_avatar:
            self.place_avatar(*offset)
        if size != self.surface_size:
            layout_changed = True
            self.surface_size = size
            self.root.geometry(f"{size[0]}x{size[1]}")
        position_window(self.root, gx, gy, flush_layout=layout_changed)

    def _poll(self, selected, version):
        try:
            boot = self._request("/bootstrap")
            current = page = None
            if selected:
                current = self._request("/chats/" + selected)
                page = self._request(
                    "/chats/" + selected + "/messages?version=" + str(version)
                )
            self.outputs.put(("poll", selected, boot, current, page))
        except Exception as exc:
            self.outputs.put(("error", str(exc)))
        finally:
            self.outputs.put(("poll_done",))

    def _pump(self):
        if self.closed:
            return
        while not self.outputs.empty():
            item = self.outputs.get_nowait()
            kind = item[0]
            if kind == "poll_done":
                self.polling = False
            elif kind == "error":
                self.status_label.configure(text="● " + item[1][:58], fg="#ffb2a6")
            elif kind == "send_error":
                _, chat_id, message = item
                self.sending = False
                if self.selected == chat_id:
                    self.send_error = message
                    self._render()
            elif kind == "poll":
                _, selected, self.boot, current, page = item
                rows = self.boot.get("tasks", [])
                signature = [(r["id"], r["title"]) for r in rows]
                if signature != self.list_signature:
                    self.list_signature = signature
                    self.dialog_ids = [r["id"] for r in rows]
                    self.listbox.delete(0, "end")
                    for row in rows:
                        self.listbox.insert("end", row["title"][:34])
                    if self.selected in self.dialog_ids:
                        self.listbox.selection_set(self.dialog_ids.index(self.selected))
                if selected == self.selected:
                    self.current = current
                    if page:
                        self.version = page["version"]
                        if page["messages"] is not None:
                            self.messages = page["messages"]
                    self._render()
            elif kind == "created":
                _, chat_id, raw = item
                self.drafts[chat_id] = self.drafts.get("", raw)
                if "" in self.file_drafts:
                    self.file_drafts[chat_id] = self.file_drafts.pop("")
                if not self.selected:
                    self.select(chat_id)
                self.drafts.pop("", None)
                self.save()
            elif kind == "sent":
                _, chat_id, raw = item
                self.sending = False
                self.file_drafts.pop(chat_id, None)
                if self.drafts.get(chat_id) == raw:
                    self.drafts[chat_id] = ""
                    if self.selected == chat_id:
                        self.input_var.set("")
                self.next_poll = 0
                self.save()
            elif kind == "stopped":
                self.sending = False
                self.next_poll = 0
        if self.visible and not self.polling and time.monotonic() >= self.next_poll:
            self.polling = True
            self.next_poll = time.monotonic() + 0.5
            threading.Thread(
                target=self._poll, args=(self.selected, self.version), daemon=True
            ).start()
        self._refresh_button()
        self.timer = self.root.after(100, self._pump)

    def _render(self):
        current = self.current or {}
        if current.get("id") and current["id"] != self.memory_chat:
            self.memory_chat = current["id"]
            self.memory_var.set((current.get("details") or {}).get("memory", True))
        status = current.get("status", "")
        if (
            status in {"queued", "retryable"}
            and current.get("revision") != self.pending_revision
        ):
            self.pending_revision = current["revision"]
            if not self.input_var.get():
                self.input_var.set(
                    (current.get("details") or {}).get("initial_prompt")
                    or current.get("prompt", "")
                )
        provider = next(
            (
                p
                for p in self.boot.get("providers", [])
                if p["id"] == current.get("provider_id")
            ),
            None,
        ) or next(iter(self.boot.get("providers", [])), {})
        name = provider.get("model", "").split(":")[0]
        title = current.get("title") or "На связи"
        self.title_label.configure(text=title[:30])
        state = self.send_error or {
            "starting": "Подключаюсь",
            "running": "Думаю",
            "awaiting_permission": "Нужно разрешение",
            "stopping": "Останавливаю",
            "failed": "Ошибка ответа",
            "retryable": "Не удалось начать. Повторите отправку",
            "interrupted": "История прервана",
            "uncertain": "Нужна сверка",
        }.get(status, "Готов помочь")
        self.status_label.configure(
            text="● "
            + (name + " · " if name else "")
            + (state if provider else "Подключите модель в настройках"),
            fg=(
                "#ffb2a6"
                if self.send_error
                or status in {"failed", "retryable", "interrupted", "uncertain"}
                else "#53f079"
            ),
        )
        partial = (
            (current.get("details") or {}).get("partial", "")
            if status in ACTIVE
            else ""
        )
        signature = (self.messages, partial, status)
        if signature == self.history_signature:
            return
        self.history_signature = (list(self.messages), partial, status)
        top = self.history.index("@0,0")
        at_end = self.history.yview()[1] >= 0.98
        selection = self.history.tag_ranges("sel")
        self.history.configure(state="normal")
        self.history.delete("1.0", "end")
        for tag in self.history.tag_names():
            if tag.startswith("artifact-"):
                self.history.tag_delete(tag)
        if not self.messages and not partial:
            self.history.insert(
                "end",
                "На связи. Напиши вопрос ниже — отвечу с выбранной локальной модели.\n",
            )
        for m in self.messages:
            role = m["role"]
            label = "Вы" if role == "user" else "Помощник"
            try:
                stamp = (
                    datetime.datetime.fromisoformat(m["time"].replace("Z", "+00:00"))
                    .astimezone()
                    .strftime("%d.%m.%Y %H:%M")
                )
            except (ValueError, KeyError):
                stamp = ""
            self.history.insert("end", label + "\n", role)
            self.history.insert("end", stamp + "\n", "time-" + role)
            self.history.insert("end", m["text"] + "\n", "body-" + role)
            for artifact in m.get("artifacts", []):
                tag = "artifact-" + artifact["id"]
                self.history.tag_configure(tag, foreground=HEADER, underline=True)
                self.history.insert("end", "Сохранить " + artifact["name"] + "\n", tag)
                self.history.tag_bind(
                    tag, "<Button-1>",
                    lambda _event, file=artifact, chat=self.selected: self.download_artifact(chat, file),
                )
            if m.get("error"):
                self.history.insert("end", m["error"] + "\n", "error")
            self.history.insert("end", "\n")
        if partial:
            self.history.insert("end", "Помощник\n", "assistant")
            self.history.insert("end", partial + "\n", "body-assistant")
        self.history.configure(state="disabled")
        if at_end:
            self.history.see("end")
        else:
            self.history.yview(top)
        if len(selection) == 2:
            with contextlib.suppress(tk.TclError):
                self.history.tag_add("sel", *selection)

    def send(self):
        if self.sending:
            return
        selected = self.selected
        raw = self.input_var.get()
        paths = tuple(self.file_drafts.get(selected, ()))
        memory = self.memory_var.get()
        working = bool(self.current and self.current.get("status") in ACTIVE)
        if not working and (not raw.strip() or not self.boot.get("providers")):
            return
        self.sending = True
        self.send_error = ""
        self._refresh_button()

        def request():
            chat_id = selected
            try:
                if working:
                    current = self._request("/chats/" + selected)
                    self._request(
                        "/chats/" + selected + "/stop",
                        {"revision": current["revision"]},
                    )
                    self.outputs.put(("stopped",))
                    return
                if selected:
                    current = self._request("/chats/" + selected)
                    if current["status"] in {"interrupted", "uncertain"}:
                        raise ValueError(
                            "Откройте Workspace и восстановите историю перед продолжением."
                        )
                    action = (
                        "/start"
                        if current["status"] in {"queued", "retryable"}
                        else "/continue"
                    )
                    details = current.get("details", {})
                    single = details.get("skill_id")
                    skill_ids = details.get("skill_ids", [single] if single else [])
                    catalogue = self.boot.get("skills")
                    if catalogue is not None:
                        available = {item["id"]: item for item in catalogue if item.get("enabled", True)}
                        skill_ids = list(dict.fromkeys(
                            [key for key in skill_ids if key in available]
                            + [key for key, item in available.items() if item.get("product")]
                        ))
                    payload = {"revision": current["revision"], "prompt": raw.strip(), "memory": memory}
                    if paths:
                        payload["attachments"] = self.upload_documents(chat_id, paths)
                    if skill_ids:
                        sections = details.get("skill_disabled_sections", {})
                        payload["skills"] = [
                            {"id": key, "disabled_sections": sections.get(key, [])} for key in skill_ids
                        ]
                    self._request("/chats/" + selected + action, payload)
                else:
                    chat = self._request(
                        "/chats",
                        {
                            "provider_id": self.boot["providers"][0]["id"],
                            "prompt": raw.strip(),
                            "mode": "chat",
                            "memory": memory,
                            "action_id": uuid.uuid4().hex,
                        },
                    )
                    chat_id = chat["id"]
                    self.outputs.put(("created", chat_id, raw))
                    ids = self.upload_documents(chat_id, paths) if paths else []
                    self._request(
                        "/chats/" + chat_id + "/start",
                        {
                            "revision": chat["revision"],
                            "prompt": raw.strip(),
                            "memory": memory,
                            "attachments": ids,
                        },
                    )
                self.outputs.put(("sent", chat_id, raw))
            except Exception as exc:
                self.outputs.put(("send_error", chat_id, str(exc)))

        threading.Thread(target=request, daemon=True).start()

    def close(self):
        if self.closed:
            return
        self.closed = True
        with contextlib.suppress(tk.TclError):
            self.root.after_cancel(self.timer)
        self.window.destroy()
        self.stage.delete("pointer")
        self.place_avatar = None
        # Tk handles and variables must be released on their creating thread.
        for name, value in list(vars(self).items()):
            if isinstance(value, (tk.Misc, tk.Variable)):
                setattr(self, name, None)
