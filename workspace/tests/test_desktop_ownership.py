import importlib
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
from workspace.app.mini_avatar import clamp_position


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
            shell.mini.destroy.assert_called_once()

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
