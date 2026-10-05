import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from tools import exe_launcher

spec = importlib.util.spec_from_file_location('portable_package', Path(exe_launcher.__file__).with_name('29_make_portable_package.py'))
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class ReleasePackageTests(unittest.TestCase):
    def test_public_archive_excludes_private_runtime_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dist = root / 'dist'
            data = {'VERSION': '1.4.0', 'LocalAIGPP.exe': 'exe', 'worker_runtime/python.exe': 'python',
                    'worker_runtime/Lib/package/direct_url.json': 'PRIVATE_BUILD_PATH', 'backend/app/core.py': 'code',
                    'models_storage/settings.json': 'PRIVATE_SETTINGS', 'models_storage/models.json': 'PRIVATE_MODEL_PATH',
                    'models_storage/private.gguf': 'PRIVATE_MODEL', 'assistant_state/chat.json': 'PRIVATE_DIALOG',
                    'logs/request.log': 'PRIVATE_LOG', 'unexpected-private.json': 'PRIVATE_DATA'}
            for filename, text in data.items():
                path = dist / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding='utf-8')
            (root / 'README.md').write_text('Public readme')
            (root / 'LICENSE').write_text('Public license')
            target = root / 'release.zip'
            with patch.object(package, 'ROOT', root):
                package.write_release_archive(dist, target, '1.4.0', {'runtime': {'n_ctx': 4096}}, [])
            with zipfile.ZipFile(target) as archive:
                self.assertIsNone(archive.testzip())
                self.assertTrue(all(b'PRIVATE' not in archive.read(name) for name in archive.namelist()))
                self.assertEqual(json.loads(archive.read('LocalAIGPP-1.4.0/models_storage/models.json')), [])
                self.assertIn('LocalAIGPP-1.4.0/worker_runtime/python.exe', archive.namelist())

    def test_stale_build_cannot_be_released_under_a_new_version(self):
        with tempfile.TemporaryDirectory() as folder:
            dist = Path(folder)
            (dist / 'VERSION').write_text('1.3.0')
            with self.assertRaisesRegex(ValueError, 'Rebuild'):
                package.write_release_archive(dist, dist / 'release.zip', '1.4.0', {}, [])
            self.assertFalse((dist / 'release.zip').exists())
