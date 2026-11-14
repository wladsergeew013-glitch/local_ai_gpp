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
        self.root.destroy()

    def test_classic_panel_is_attached_on_left_and_has_no_modal_grab(self):
        self.assertEqual(self.panel.anchor["side"], "left")
        self.assertLess(self.panel.anchor["x"] + self.panel.width, 1000)
        self.assertIsNone(self.root.grab_current())
        self.assertTrue(self.panel.window.overrideredirect())
        self.assertEqual(self.panel.window.cget("bg"), "#0e4f97")
        self.assertEqual(len(self.panel.tail_canvas.find_all()), 2)
        self.assertEqual(self.panel.history.tag_cget("user", "justify"), "right")
        self.assertEqual(self.panel.history.tag_cget("assistant", "justify"), "left")
        self.assertIn("01.11.2026", self.panel.history.get("1.0", "end"))

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


if __name__ == "__main__":
    unittest.main()
