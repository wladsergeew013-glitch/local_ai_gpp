"""The build may discard only owned staging paths and must retain a usable install."""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools import exe_launcher

spec = importlib.util.spec_from_file_location('transaction_builder', Path(exe_launcher.__file__).with_name('02_build_exe.py'))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BuildTransactionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('tools','backend','out','dist'):
            (self.root/name).mkdir()
        overrides = patch.multiple(builder,ROOT=self.root,OUT=self.root/'out',TOOLS=self.root/'tools',DIST=None)
        overrides.start(); self.addCleanup(overrides.stop)

    def test_C01_source_and_junctions_are_never_deletion_targets(self):
        sentinel = self.root/'backend/keep.py'; sentinel.write_text('source')
        link = self.root/'dist/backend'
        if os.name == 'nt':
            subprocess.run(['cmd','/c','mklink','/J',str(link),str(sentinel.parent)],check=True,capture_output=True)
            self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        else:
            link.symlink_to(sentinel.parent, target_is_directory=True)
        for path in (sentinel.parent,link):
            with self.assertRaises(ValueError): builder.remove_build_tree(path)
        self.assertEqual(sentinel.read_text(),'source')
        staged=self.root/'out/disposable'; staged.mkdir()
        builder.remove_build_tree(staged)
        self.assertFalse(staged.exists())

    def test_R41_pyinstaller_failure_preserves_old_executable(self):
        old=self.root/'dist/LocalAIGPP.exe'; old.write_bytes(b'previous executable')
        (self.root/'tools/exe_launcher.py').write_text('pass')
        with patch.object(builder,'write_windows_version_file',return_value=self.root/'version.txt'), \
             patch.object(builder,'run',side_effect=RuntimeError('PyInstaller failed')):
            with self.assertRaisesRegex(RuntimeError,'PyInstaller failed'): builder.build_pyinstaller(Path(sys_executable()))
        self.assertEqual(old.read_bytes(),b'previous executable')

    def test_R41_promotion_failure_restores_program_and_user_history(self):
        old=self.root/'dist/LocalAIGPP.exe'; old.write_bytes(b'previous')
        state=self.root/'dist/assistant_state'; state.mkdir(); (state/'history.json').write_text('private')
        candidate=self.root/'out/candidate'; candidate.mkdir(); (candidate/'LocalAIGPP.exe').write_bytes(b'new')
        original=Path.rename
        def fail(source,target):
            if source == candidate: raise OSError('cannot promote')
            return original(source,target)
        with patch.object(Path,'rename',fail):
            with self.assertRaisesRegex(OSError,'cannot promote'): builder.promote_distribution(candidate)
        self.assertEqual(old.read_bytes(),b'previous')
        self.assertEqual((state/'history.json').read_text(),'private')


def sys_executable():
    import sys
    return sys.executable
