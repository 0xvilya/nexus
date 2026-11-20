"""Packaging selection tests use synthetic archives, without downloading assets."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import bootstrap
import packaged_assets


class PackagedAssetSelectionTests(unittest.TestCase):
    def prepare(self, folder):
        root = Path(folder)
        (root / 'runtime/models').mkdir(parents=True)
        (root / 'docs').mkdir()
        data = b'synthetic starter'
        (root / 'runtime/models/starter.gguf').write_bytes(data)
        (root / 'runtime/catalog.json').write_text(json.dumps({'models': [{
            'id': 'smollm2-135m-smoke', 'filename': 'starter.gguf',
            'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'url': 'fixture',
        }]}), encoding='utf-8')
        (root / 'docs/OFFLINE_ASSETS_NOTICE.txt').write_text(
            'Synthetic dictation and starter notices\n\nRussian synthesis: optional speech notice', encoding='utf-8')
        bundle = root / 'bundle'
        bundle.mkdir()
        requests = []

        def download(url, target, digest, size):
            requests.append(url)
            target.parent.mkdir(parents=True, exist_ok=True)
            if url == packaged_assets.WHISPER[0]:
                with zipfile.ZipFile(target, 'w') as archive:
                    for name in ('whisper-cli.exe', 'whisper.dll', 'ggml.dll', 'ggml-base.dll', 'ggml-cpu.dll'):
                        archive.writestr('Release/' + name, b'synthetic engine')
            else:
                target.write_bytes(b'synthetic pinned asset')
            return target
        return root, bundle, requests, download

    def test_core_selection_excludes_neural_speech_and_notices(self):
        with tempfile.TemporaryDirectory() as folder:
            root, bundle, requests, downloader = self.prepare(folder)
            with patch.object(packaged_assets, 'download', side_effect=downloader), patch.object(
                packaged_assets, 'include_speech', side_effect=AssertionError('Neural speech requested in Core')
            ):
                packaged_assets.include_offline_assets(root, bundle, include_neural_speech=False)
            self.assertFalse((bundle / 'runtime/speech').exists())
            self.assertTrue((bundle / 'runtime/voice/whisper-cli.exe').is_file())
            self.assertEqual((bundle / 'runtime/starter/starter.gguf').read_bytes(), b'synthetic starter')
            for name in ('LICENSE-espeak-ng.txt', 'LICENSE-piper-phonemize.txt',
                         'LICENSE-onnxruntime.txt', 'NOTICE-onnxruntime.txt'):
                self.assertFalse((bundle / name).exists())
                self.assertNotIn(packaged_assets.LICENSES[name][0], requests)
            for name in ('LICENSE-whisper.cpp.txt', 'LICENSE-Whisper-model.txt', 'LICENSE-SmolLM2.txt'):
                self.assertTrue((bundle / name).is_file())
            notice = (bundle / 'OFFLINE_ASSETS_NOTICE.txt').read_text(encoding='utf-8')
            self.assertIn('Core preview', notice)
            self.assertIn('not bundled', notice)
            self.assertNotIn('optional speech notice', notice)

    def test_default_build_retains_neural_speech(self):
        with tempfile.TemporaryDirectory() as folder:
            root, bundle, _, downloader = self.prepare(folder)
            with patch.object(packaged_assets, 'download', side_effect=downloader), patch.object(
                packaged_assets, 'include_speech'
            ) as speech:
                packaged_assets.include_offline_assets(root, bundle)
            speech.assert_called_once_with(root / 'work/voice-runtime', bundle)
            self.assertTrue((bundle / 'LICENSE-espeak-ng.txt').is_file())
            self.assertIn('optional speech notice', (bundle / 'OFFLINE_ASSETS_NOTICE.txt').read_text(encoding='utf-8'))

    def test_prepared_worker_notices_are_copied_with_versions(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder)
            packaged_assets.include_worker_notices(bootstrap.ROOT, bundle)
            self.assertIn("PYTHON SOFTWARE FOUNDATION", (bundle / "LICENSE-Python.txt").read_text(encoding="utf-8").upper())
            self.assertIn("exception", (bundle / "LICENSE-PyInstaller.txt").read_text(encoding="utf-8").lower())
            versions = json.loads((bundle / "WORKER_NOTICES.json").read_text(encoding="utf-8"))
            self.assertEqual(versions["pyinstaller_version"], "6.22.3")
            self.assertTrue(versions["python_version"].startswith("3."))

    def test_core_rejects_existing_optional_speech(self):
        with tempfile.TemporaryDirectory() as folder:
            root, bundle, _, downloader = self.prepare(folder)
            (bundle / 'runtime/speech').mkdir(parents=True)
            with patch.object(packaged_assets, 'download', side_effect=downloader):
                with self.assertRaisesRegex(ValueError, 'without neural speech'):
                    packaged_assets.include_offline_assets(root, bundle, include_neural_speech=False)


if __name__ == '__main__':
    unittest.main()

