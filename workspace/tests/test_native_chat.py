import os
import tempfile
import time
import unittest
from unittest.mock import Mock


@unittest.skipUnless(os.name == "nt", "Native Windows Tk panel")
class NativeChatTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from workspace.app.native_chat import NativeChatBubble

        self.root = tk.Tk()
        self.root.withdraw()
        self.data = [
            {
                "id": i,
                "role": "user" if i % 2 else "assistant",
                "text": "A saved message with enough words to wrap. " * 5,
                "error": "",
                "time": "2026-11-01T12:00:00+00:00",
            }
            for i in range(1, 65)
        ]

        def api(path, body=None):
            if path == "/bootstrap":
                return {
                    "providers": [{"id": "p", "model": "Qwen3.5 4B"}],
                    "tasks": [
                        {"id": "a", "title": "First dialogue"},
                        {"id": "b", "title": "Second dialogue"},
                    ],
                }
            if "/messages?" in path:
                return {"version": len(self.data), "messages": list(self.data)}
            return {
                "id": path.rsplit("/", 1)[-1],
                "title": "First dialogue",
                "provider_id": "p",
                "status": "completed",
                "details": {"mode": "chat", "memory": True},
            }

        self.panel = NativeChatBubble(
            self.root,
            "http://127.0.0.1:8776",
            lambda: (1000, 500),
            Mock(),
            Mock(),
            prefs={"selected": "a"},
            hidden=True,
            transport=api,
        )
        self.panel.show()
        self.until(
            lambda: self.panel.listbox.size() == 2 and len(self.panel.messages) == 64
        )

    def until(self, condition):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.root.update()
            if condition():
                return
            time.sleep(0.01)
        self.assertTrue(condition())

    def tearDown(self):
        self.panel.close()
        # The mock transport closes over this TestCase. Drop that reference on
        # Tk's thread before a later ASGI worker has a chance to collect it.
        self.panel.transport = None
        self.panel = None
        self.root.destroy()
        self.root = None
        import gc
        gc.collect()

    def test_classic_panel_is_attached_on_left_and_has_no_modal_grab(self):
        self.assertEqual(self.panel.anchor["side"], "left")
        self.assertLess(self.panel.anchor["x"] + self.panel.width, self.panel.anchor["tip"][0])
        self.assertIsNone(self.root.grab_current())
        self.assertIs(self.panel.window.winfo_toplevel(), self.root)
        self.assertIs(self.panel.stage.winfo_toplevel(), self.root)
        self.assertEqual(self.panel.window.cget("bg"), "#0e4f97")
        self.assertEqual(len(self.panel.tail_canvas.find_all()), 2)
        self.assertEqual(self.panel.history.tag_cget("user", "justify"), "right")
        self.assertEqual(self.panel.history.tag_cget("assistant", "justify"), "left")
        self.assertIn("01.11.2026", self.panel.history.get("1.0", "end"))

    def test_open_drag_across_avatar_hide_and_reopen_share_one_window(self):
        from unittest.mock import patch
        from workspace.app.companion_layout import attached_layout

        pos = [1000, 500]
        self.panel.avatar_position = lambda: pos
        offsets = []
        self.panel.place_avatar = lambda x, y: offsets.append((x, y))
        bounds = (-1400, -60, 1400, 940)
        with patch("workspace.app.mini_avatar.work_area", return_value=bounds):
            for position in ((1100, 500), (-1330, 120), (-110, 710)):
                pos[:] = position
                self.panel.reposition()
                self.root.update_idletasks()
                layout = attached_layout(pos, (self.panel.width, self.panel.height), bounds)
                self.assertEqual(offsets[-1], layout.avatar)
                self.assertEqual(tuple(self.panel.stage.coords(self.panel.tail_outline)), layout.pointer)
                self.assertEqual((self.panel.window.winfo_x(), self.panel.window.winfo_y()), layout.panel[:2])
                self.assertEqual(self.panel.anchor["tip"][1], position[1] + 72)
            self.panel.hide()
            self.assertFalse(self.panel.window.winfo_manager())
            self.assertEqual(offsets[-1], (0, 0))
            self.panel.show()
            self.assertEqual(self.panel.window.winfo_manager(), "place")
            self.assertEqual(self.panel.stage.itemcget(self.panel.tail_fill, "state"), "normal")

    def test_translating_open_panel_reuses_its_layout(self):
        from unittest.mock import patch

        bounds = (0, 0, 2560, 1440)
        xy = [1100, 300]
        self.panel.avatar_position = lambda: xy
        self.panel.reposition(bounds=bounds)
        tip = self.panel.anchor["tip"]
        with patch.object(self.panel.window, "place", wraps=self.panel.window.place) as place:
            for _ in range(35):
                xy[0] += 2
                self.panel.reposition(bounds=bounds)
            place.assert_not_called()
        self.assertEqual(self.panel.anchor["tip"], (tip[0] + 70, tip[1]))

    def test_portrait_crosses_shared_monitor_edge_and_stays_out_of_gaps(self):
        from workspace.app.mini_avatar import clamp_position

        left, right = (-2560, 0, 0, 1400), (0, 0, 1920, 1040)
        screens = (left, right)
        self.assertEqual(clamp_position(-80, 350, 154, 210, right, screens), (-80, 350))
        self.assertEqual(clamp_position(-80, 1050, 154, 210, left, screens), (-154, 1050))
        self.assertEqual(clamp_position(1850, 350, 154, 210, right, screens), (1766, 350))

    def test_dialogue_menu_and_exact_draft_are_preserved(self):
        self.panel.toggle_sidebar()
        self.assertEqual(self.panel.sidebar.winfo_manager(), "pack")
        self.panel.input_var.set("  Unsent draft for this dialogue  ")
        self.panel.select("b")
        self.until(lambda: self.panel.current and self.panel.current["id"] == "b")
        self.assertEqual(self.panel.input_var.get(), "")
        self.panel.select("a")
        self.assertEqual(
            self.panel.input_var.get(), "  Unsent draft for this dialogue  "
        )
        self.panel.new_chat()
        self.assertEqual(self.panel.selected, "")
        self.assertEqual(
            self.panel.prefs()["drafts"]["a"], "  Unsent draft for this dialogue  "
        )

    def test_poll_does_not_scroll_reading_position_to_end(self):
        self.panel.history.yview("1.0")
        self.root.update_idletasks()
        top = self.panel.history.index("@0,0")
        self.data.append(
            {
                "id": 65,
                "role": "assistant",
                "text": "A newly arrived answer",
                "error": "",
                "time": "2026-11-01T12:01:00+00:00",
            }
        )
        self.panel.next_poll = 0
        self.until(lambda: len(self.panel.messages) == 65)
        self.assertEqual(self.panel.history.index("@0,0"), top)

    def test_followup_reuses_workspace_skills_and_disabled_sections(self):
        base = self.panel.transport
        sent = []
        def api(path, body=None):
            if path == "/chats/a/continue":
                sent.append(body)
                return {"id": "a", "revision": 3}
            result = base(path, body)
            if path == "/chats/a":
                result["revision"] = 2
                result["details"].update(skill_ids=["first", "second"],
                                         skill_disabled_sections={"second": ["dates"]})
            return result

        self.panel.transport = api
        self.panel.input_var.set("Continue.")
        self.panel.send()
        self.until(lambda: sent and not self.panel.sending)
        self.assertEqual(sent[0]["skills"], [
            {"id": "first", "disabled_sections": []}, {"id": "second", "disabled_sections": ["dates"]},
        ])

    def test_busy_start_keeps_new_dialogue_and_edited_draft(self):
        base_transport = self.panel.transport
        starts, creates = [], []

        def api(path, body=None):
            if path == "/chats":
                creates.append(body)
                return {"id": "created", "revision": 1}
            if path == "/chats/created/start":
                starts.append(body)
                if len(starts) == 1:
                    raise RuntimeError("WORKSPACE_BUSY")
                return {"id": "created", "revision": 2}
            if path == "/chats/created":
                return {
                    "id": "created", "status": "queued" if len(starts) < 2 else "running",
                    "revision": 1, "prompt": "First draft", "details": {"memory": True},
                }
            return base_transport(path, body)

        self.panel.transport = api
        self.panel.new_chat()
        self.panel.input_var.set("  First draft  ")
        self.panel.send()
        self.until(lambda: self.panel.selected == "created" and not self.panel.sending)
        self.assertEqual(self.panel.input_var.get(), "  First draft  ")
        self.panel.input_var.set("Edited message")
        self.panel.memory_var.set(False)
        self.panel.send()
        self.until(lambda: len(starts) == 2 and not self.panel.sending)
        self.assertEqual(len(creates), 1)
        self.assertEqual(starts[-1], {"revision": 1, "prompt": "Edited message", "memory": False})
        self.assertEqual(self.panel.input_var.get(), "")


if __name__ == "__main__":
    unittest.main()
