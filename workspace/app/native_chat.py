"""The original blue speech panel, owned by the avatar's Tk thread.

This view talks only to Workspace HTTP. It never loads models, reads Runtime
files, grabs input globally or opens a modal window.
"""

from __future__ import annotations
import contextlib
import datetime
import queue
import threading
import time
import uuid
import tkinter as tk
import httpx

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
        self.visible = self.sidebar_open = self.polling = self.sending = self.closed = (
            False
        )
        self.boot, self.current, self.messages = {}, None, []
        self.version = -1
        self.memory_chat = None
        self.dialog_ids, self.list_signature, self.history_signature = [], None, None
        self.outputs = queue.SimpleQueue()
        self.next_poll = 0
        self._build()
        self.timer = self.root.after(100, self._pump)

    def _request(self, path, body=None):
        if self.transport:
            return self.transport(path, body)
        with httpx.Client(
            base_url=self.base_url + "/api", trust_env=False, timeout=15
        ) as client:
            response = (
                client.get(path) if body is None else client.post(path, json=body)
            )
            response.raise_for_status()
            return response.json()

    def _build(self):
        self.window = tk.Toplevel(self.root)
        self.window.title("Диалог Local AI")
        self.window.transient(self.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=BORDER)
        self.window.geometry(f"{self.width}x{self.height}")
        self.window.bind("<Escape>", lambda _: self.hide())
        self.tail = tk.Toplevel(self.root)
        self.tail.withdraw()
        self.tail.overrideredirect(True)
        self.tail.attributes("-topmost", True)
        self.tail.configure(bg=TRANSPARENT)
        import os

        if os.name == "nt":
            self.tail.wm_attributes("-transparentcolor", TRANSPARENT)
        self.tail.geometry("34x46")
        self.tail_canvas = tk.Canvas(
            self.tail,
            width=34,
            height=46,
            bg=TRANSPARENT,
            highlightthickness=0,
            borderwidth=0,
        )
        self.tail_canvas.pack(fill="both", expand=True)
        for sequence in ("<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>"):
            self.tail_canvas.bind(sequence, lambda _: "break")
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
        self.close_button.place(x=self.width - 46, y=11, width=34, height=34)
        self.close_button.bind("<Button-1>", lambda _: self.hide())
        self.open_button = tk.Label(
            header,
            text="↗",
            bg="#2f67ad",
            fg="white",
            font=("Segoe UI", 14),
            cursor="hand2",
        )
        self.open_button.place(x=self.width - 83, y=11, width=30, height=34)
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
        available = working or (
            bool(self.input_var.get().strip())
            and bool(self.boot.get("providers"))
            and not self.sending
        )
        self.send_button.configure(
            text="■" if working else "↵", bg="#1d5fa8" if available else "#b5cbe2"
        )

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
            payload["panel"] = "provider"
        threading.Thread(target=lambda: self.on_main(payload), daemon=True).start()

    def show(self):
        self.visible = True
        self.reposition()
        if not self.hidden_test:
            self.window.deiconify()
            self.tail.deiconify()
            self.window.lift()
            self.tail.lift()
            self.entry.focus_set()
        self.next_poll = 0

    def hide(self):
        self.drafts[self.selected] = self.input_var.get()
        self.visible = False
        self.window.withdraw()
        self.tail.withdraw()
        self.save()
        return "break"

    def toggle(self):
        self.hide() if self.visible else self.show()

    def reposition(self):
        if not self.visible:
            return
        from workspace.app.mini_avatar import position_window, work_area

        ax, ay = self.avatar_position()
        left, top, right, bottom = work_area(ax, ay)
        gap = 24
        if ax - self.width - gap >= left + 12:
            x = ax - self.width - gap
            side = "left"
        elif ax + 154 + gap + self.width <= right - 12:
            x = ax + 154 + gap
            side = "right"
        else:
            x = max(left + 12, min(ax - self.width - gap, right - self.width - 12))
            side = "left"
        y = max(top + 12, min(ay + 8, bottom - self.height - 12))
        self.window.geometry(f"{self.width}x{self.height}")
        position_window(self.window, x, y)
        ty = max(y + 58, min(y + self.height - 74, ay + 105 - 23))
        tx = x + self.width - 2 if side == "left" else x - 32
        position_window(self.tail, tx, ty)
        self.tail_canvas.delete("all")
        outer = [0, 7, 34, 23, 0, 39] if side == "left" else [34, 7, 0, 23, 34, 39]
        inner = [0, 12, 27, 23, 0, 34] if side == "left" else [34, 12, 7, 23, 34, 34]
        self.tail_canvas.create_polygon(outer, fill=BORDER, outline=BORDER)
        self.tail_canvas.create_polygon(inner, fill=BG, outline=BG)
        self.anchor = {"x": x, "y": y, "side": side, "tail_x": tx}

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
                self.sending = False
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
            elif kind == "sent":
                _, origin, chat_id, raw = item
                self.sending = False
                if self.selected == origin:
                    typed = self.input_var.get()
                    self.drafts[origin] = ""
                    self.selected = chat_id
                    self.version = -1
                    self.current = None
                    self.messages = []
                    self.history_signature = None
                    self.drafts[chat_id] = "" if typed == raw else typed
                    self.input_var.set(self.drafts[chat_id])
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
        state = {
            "starting": "Подключаюсь",
            "running": "Думаю",
            "awaiting_permission": "Нужно разрешение",
            "stopping": "Останавливаю",
            "failed": "Ошибка ответа",
            "interrupted": "История прервана",
            "uncertain": "Нужна сверка",
        }.get(status, "Готов помочь")
        self.status_label.configure(
            text="● "
            + (name + " · " if name else "")
            + (state if provider else "Подключите модель в настройках"),
            fg=(
                "#ffb2a6"
                if status in {"failed", "interrupted", "uncertain"}
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
        memory = self.memory_var.get()
        working = bool(self.current and self.current.get("status") in ACTIVE)
        if not working and (not raw.strip() or not self.boot.get("providers")):
            return
        self.sending = True
        self._refresh_button()

        def request():
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
                    action = "/start" if current["status"] == "queued" else "/continue"
                    self._request(
                        "/chats/" + selected + action,
                        {
                            "revision": current["revision"],
                            "prompt": raw.strip(),
                            "memory": memory,
                        },
                    )
                    chat_id = selected
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
                    self._request(
                        "/chats/" + chat_id + "/start", {"revision": chat["revision"]}
                    )
                self.outputs.put(("sent", selected, chat_id, raw))
            except Exception as exc:
                self.outputs.put(("error", str(exc)))

        threading.Thread(target=request, daemon=True).start()

    def close(self):
        if self.closed:
            return
        self.closed = True
        with contextlib.suppress(tk.TclError):
            self.root.after_cancel(self.timer)
        self.window.destroy()
        self.tail.destroy()
        # Tk handles and variables must be released on their creating thread.
        for name, value in list(vars(self).items()):
            if isinstance(value, (tk.Misc, tk.Variable)):
                setattr(self, name, None)
