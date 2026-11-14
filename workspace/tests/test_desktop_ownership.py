import importlib
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from workspace.app.main import create_app
from workspace.app import desktop_host
from workspace.app.mini_avatar import clamp_position, position_window


class Hook:
    def __iadd__(self, callback):
        return self


def window():
    result = Mock()
    result.events = types.SimpleNamespace(closing=Hook())
    return result


class DesktopOwnershipTests(unittest.TestCase):
    def shell(self):
        fake = types.SimpleNamespace(
            create_window=Mock(side_effect=[window(), window()])
        )
        with patch.dict(sys.modules, {"webview": fake}):
            module = importlib.import_module("workspace.app.desktop_shell")
        return module, fake

    def test_main_close_hides_to_workspace_avatar_without_stopping_tasks(self):
        module, fake = self.shell()
        avatar = Mock()
        app = Mock()
        server = Mock()
        server.should_exit = False
        with patch.object(module, "webview", fake), patch.object(
            module, "MiniAvatar", return_value=avatar
        ), patch.object(module.WorkspaceShell, "_start_tray"), patch.object(
            desktop_host, "_callbacks", {}
        ):
            shell = module.WorkspaceShell(
                app, server, Mock(), 8776, Path("owned-workspace")
            )
            self.assertFalse(shell._close_main())
            shell.main.hide.assert_called_once()
            avatar.show.assert_called_once()
            app.state.tasks.close.assert_not_called()
            self.assertFalse(server.should_exit)
            self.assertEqual(desktop_host.status()["mini_helper_owner"], "workspace")

    def test_explicit_exit_closes_owned_tasks_before_windows(self):
        module, fake = self.shell()
        avatar = Mock()
        app = Mock()
        server = Mock()
        server.should_exit = False
        finished = threading.Event()
        with patch.object(module, "webview", fake), patch.object(
            module, "MiniAvatar", return_value=avatar
        ), patch.object(module.WorkspaceShell, "_start_tray"), patch.object(
            desktop_host, "_callbacks", {}
        ):
            shell = module.WorkspaceShell(
                app, server, Mock(), 8776, Path("owned-workspace")
            )
            shell.main.destroy.side_effect = finished.set
            shell.request_exit()
            self.assertTrue(finished.wait(5))
            app.state.tasks.close.assert_called_once()
            self.assertTrue(server.should_exit)
            avatar.close.assert_called_once()
            fake.create_window.assert_called_once()

    def test_footer_shows_avatar_and_dialog_uses_native_panel_without_webview_window(
        self,
    ):
        module, fake = self.shell()
        avatar = Mock()
        with patch.object(module, "webview", fake), patch.object(
            module, "MiniAvatar", return_value=avatar
        ), patch.object(module.WorkspaceShell, "_start_tray"), patch.object(
            desktop_host, "_callbacks", {}
        ):
            shell = module.WorkspaceShell(
                Mock(), Mock(), Mock(), 8776, Path("owned-workspace")
            )
            shell.show_avatar()
            avatar.show.assert_called_once()
            avatar.show_chat.assert_not_called()
            shell.toggle_mini((300, 300))
            avatar.toggle_chat.assert_called_once()
            fake.create_window.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows native positioning")
    def test_hidden_avatar_position_does_not_pollute_webview_ctypes_functions(self):
        import ctypes
        import tkinter as tk
        from ctypes import wintypes

        original = ctypes.windll.user32.SetWindowPos.argtypes
        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.geometry("40x40")
        try:
            position_window(root, -120, -70)
            self.assertIs(ctypes.windll.user32.SetWindowPos.argtypes, original)
            api = ctypes.WinDLL("user32", use_last_error=True)
            api.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            api.GetAncestor.restype = ctypes.c_void_p
            api.GetWindowRect.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(wintypes.RECT),
            ]
            rect = wintypes.RECT()
            self.assertTrue(
                api.GetWindowRect(
                    api.GetAncestor(root.winfo_id(), 2), ctypes.byref(rect)
                )
            )
            self.assertEqual((rect.left, rect.top), (-120, -70))
        finally:
            root.destroy()

    def test_headless_has_task_mini_page_but_no_native_shell(self):
        with tempfile.TemporaryDirectory() as root, patch.object(
            desktop_host, "_callbacks", {}
        ), TestClient(create_app(root), base_url="http://127.0.0.1") as client:
            status = client.get("/api/host/status").json()
            self.assertFalse(status["desktop"])
            self.assertEqual(status["mini_helper_owner"], "workspace")
            self.assertEqual(
                client.post("/api/host/show-mini", json={}).status_code, 404
            )

    def test_avatar_clamps_on_negative_and_disconnected_monitors(self):
        self.assertEqual(
            clamp_position(-400, -90, 154, 210, (-1920, -1080, 0, 0)), (-400, -210)
        )
        self.assertEqual(
            clamp_position(6000, 4000, 154, 210, (0, 0, 1920, 1040)), (1766, 830)
        )


if __name__ == "__main__":
    unittest.main()
