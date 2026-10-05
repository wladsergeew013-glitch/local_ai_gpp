import argparse
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools import exe_launcher

spec = importlib.util.spec_from_file_location('exe_builder', Path(exe_launcher.__file__).with_name('02_build_exe.py'))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ExeBuilderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        root_patch = patch.object(builder, 'ROOT', self.root)
        out_patch = patch.object(builder, 'OUT', self.root / 'out')
        log_patch = patch.object(builder, 'log')
        root_patch.start()
        out_patch.start()
        log_patch.start()
        self.addCleanup(root_patch.stop)
        self.addCleanup(out_patch.stop)
        self.addCleanup(log_patch.stop)

    def args(self, cpu=False, cuda=None):
        return argparse.Namespace(cpu=cpu, cuda=cuda)

    def test_nvidia_enables_cuda_without_build_flags(self):
        with patch.object(builder, 'find_nvidia_smi', return_value='nvidia-smi.exe'):
            self.assertEqual(builder.choose_runtime_kind(self.args()), 'cu124')

    def test_explicit_cpu_overrides_nvidia(self):
        with patch.object(builder, 'find_nvidia_smi', return_value='nvidia-smi.exe'):
            self.assertEqual(builder.choose_runtime_kind(self.args(cpu=True)), 'cpu')

    def test_cuda_request_does_not_silently_become_cpu_without_gpu(self):
        with patch.object(builder, 'find_nvidia_smi', return_value=''):
            self.assertEqual(builder.choose_runtime_kind(self.args(cuda='auto')), 'cu124')
            with self.assertRaises(SystemExit):
                builder.choose_runtime_kind(self.args(cuda='unsupported'))

    def test_existing_cuda_package_survives_missing_detection(self):
        (self.root / 'dist').mkdir()
        (self.root / 'dist/runtime_info.json').write_text(json.dumps({'effective':'cu124'}))
        with patch.object(builder, 'find_nvidia_smi', return_value=''):
            self.assertEqual(builder.choose_runtime_kind(self.args()), 'cu124')

    @unittest.skipUnless(os.name == 'nt', 'Windows driver location')
    def test_windows_detection_does_not_require_nvidia_on_path(self):
        system = self.root / 'Windows/System32'
        system.mkdir(parents=True)
        binary = system / 'nvidia-smi.exe'
        binary.touch()
        with patch.object(builder.shutil, 'which', return_value=None), \
             patch.dict(builder.os.environ, {'SystemRoot':str(system.parent)}):
            self.assertEqual(Path(builder.find_nvidia_smi()), binary)

    def test_cpu_worker_cannot_pass_cuda_package_verification(self):
        output = json.dumps({'llama_cpp':'0.3.36','supports_gpu_offload':False,'system_info':'CPU : AVX2 = 1'})
        result = subprocess.CompletedProcess([], 0, output + '\n', '')
        with patch.object(builder.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(SystemExit, 'no CUDA offload'):
                builder.verify_worker_runtime(self.root/'python.exe', 'cu124', self.root)
            self.assertFalse(builder.verify_worker_runtime(self.root/'python.exe', 'cpu', self.root)['supports_gpu_offload'])

    def test_failed_dll_import_stops_packaging(self):
        result = subprocess.CompletedProcess([], 1, '', 'Missing cublas64_12.dll')
        with patch.object(builder.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(SystemExit, 'cublas64_12.dll'):
                builder.verify_worker_runtime(self.root/'python.exe', 'cu124', self.root)

    def test_cuda_dependencies_are_prepared_before_exe_is_replaced(self):
        with patch.object(builder, 'parse_args', return_value=self.args()), \
             patch.object(builder, 'choose_runtime_kind', return_value='cu124'), \
             patch.object(builder, 'reset_log_file'), \
             patch.object(builder, 'find_base_python', return_value='python.exe'), \
             patch.object(builder, 'ensure_backend_venv'), \
             patch.object(builder, 'prepare_worker_runtime', side_effect=SystemExit('CUDA install failed')), \
             patch.object(builder, 'build_pyinstaller') as package:
            with self.assertRaises(SystemExit):
                builder.main()
            package.assert_not_called()


if __name__ == '__main__':
    unittest.main()
