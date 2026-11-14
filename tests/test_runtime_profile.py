import unittest
from unittest.mock import Mock, patch
from backend.app import core


class RuntimeProfileTests(unittest.TestCase):
    def test_runtime_desktop_never_starts_workspace_companion(self):
        import sys,types
        from tools import exe_launcher as desktop
        class Hook:
            callback=None
            def __iadd__(self,callback):self.callback=callback;return self
        closing=Hook();window=Mock();window.events=types.SimpleNamespace(closing=closing)
        view=types.SimpleNamespace(create_window=Mock(return_value=window),start=Mock())
        frontend=Mock();frontend.exists.return_value=True
        with patch.dict(sys.modules,{'webview':view}),patch.object(desktop,'FRONTEND_DIST',frontend),\
             patch.object(desktop,'configure_windows_app_identity'),patch.object(desktop,'find_free_port',return_value=8765),\
             patch.object(desktop,'wait_for_server',return_value=True),patch.object(desktop,'write_instance_info'),\
             patch.object(desktop,'start_tray'),patch.object(desktop,'NativeAssistantAgent') as avatar,\
             patch.object(desktop.threading,'Thread'),patch.object(desktop.uvicorn,'Server'),\
             patch.object(desktop,'request_exit'),patch.object(desktop,'hide_main_window') as hide,\
             patch.object(desktop,'show_assistant_window') as show,patch('backend.app.desktop_host.register') as register,\
             patch.object(desktop,'quitting',False),patch.object(desktop,'main_window',None),patch.object(desktop,'server',None),\
             patch.object(desktop,'server_thread',None),patch.object(desktop,'server_port',0),patch.object(desktop,'webview_module',None):
            desktop._run_desktop();avatar.assert_not_called()
            self.assertNotIn('show_assistant',register.call_args.kwargs)
            self.assertFalse(closing.callback());hide.assert_called_once();show.assert_not_called()

    def test_other_clients_inherit_loaded_weights_context_and_gpu_but_current_sampler(
        self,
    ):
        worker = Mock()
        worker.process.poll.return_value = None
        model = {"id": "m", "path": "model.gguf", "runtime": {"n_ctx": 1024}}
        entry = {
            "runtime": worker,
            "model_loaded": True,
            "model_signature": ("signature",),
            "config": {
                "n_ctx": 16384,
                "n_gpu_layers": 12,
                "temperature": 1.5,
                "max_tokens": 32,
            },
        }
        with patch.dict(core.RUNTIMES, {"m": entry}, clear=True), patch.object(
            core, "model_file_signature", return_value=("signature",)
        ):
            selected = core.selected_load_profile(model)
            self.assertEqual(selected, {"n_ctx": 16384, "n_gpu_layers": 12})
            merged = core._merge_runtime(
                model, {"runtime": {"temperature": 0.4, "max_tokens": 2048}}, selected
            )
            self.assertEqual(
                (merged["n_ctx"], merged["temperature"], merged["max_tokens"]),
                (16384, 0.4, 2048),
            )
            worker.process.poll.return_value = 1
            self.assertEqual(core.selected_load_profile(model), {})

    def test_retargeted_model_does_not_inherit_previous_weights_profile(self):
        worker = Mock()
        worker.process.poll.return_value = None
        entry = {
            "runtime": worker,
            "model_loaded": True,
            "model_signature": ("old",),
            "config": {"n_ctx": 16384},
        }
        with patch.dict(core.RUNTIMES, {"m": entry}, clear=True), patch.object(
            core, "model_file_signature", return_value=("new",)
        ):
            self.assertEqual(
                core.selected_load_profile({"id": "m", "path": "new.gguf"}), {}
            )


if __name__ == "__main__":
    unittest.main()
