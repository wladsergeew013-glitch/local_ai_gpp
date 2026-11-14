import unittest
from unittest.mock import Mock, patch
from backend.app import core


class RuntimeProfileTests(unittest.TestCase):
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
