import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import bootstrap
from rainforge_native.smartload import plan


class SmartLoadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = self.root / 'model.gguf'
        self.model.write_bytes(b'not-a-real-gguf')
        self.runtime = SimpleNamespace(model_path=lambda name: self.model)
        self.meta = patch('rainforge_native.smartload.metadata', return_value={
            'general.architecture': 'qwen2', 'general.file_type': 15})
        self.meta.start()
        self.addCleanup(self.meta.stop)

    def test_low_ram_warning_and_recommendation_are_explicit_estimates(self):
        with patch('rainforge_native.smartload.physical_memory', return_value=(16 * 1024**3, 1024**3)):
            result = plan(self.runtime, 'model.gguf', 'economy')
        self.assertEqual(result['estimate']['ram_budget_bytes'], 0)
        self.assertEqual(result['recommendation']['context'], 512)
        self.assertEqual(result['recommendation']['mode'], 'cpu')
        self.assertEqual(result['model']['kind'], 'dense')
        self.assertIsNone(result['hardware']['vram_available_bytes'])
        self.assertIsNone(result['estimate']['kv_cache_bytes'])
        self.assertFalse(result['limits_enforced'])
        self.assertTrue(any('бюджет RAM' in text for text in result['warnings']))
        json.dumps(result, ensure_ascii=False)

    def test_recommendation_adapts_to_available_memory(self):
        for available, expected in [(2*1024**3,512),(5*1024**3,1024),(10*1024**3,4096),(None,1024)]:
            with patch('rainforge_native.smartload.physical_memory',return_value=(16*1024**3,available)):
                result=plan(self.runtime,'model.gguf','performance')
            self.assertEqual(result['recommendation']['context'],expected)

    def test_unknown_hardware_and_experimental_do_not_claim_streaming(self):
        with patch('rainforge_native.smartload.physical_memory', return_value=(None, None)):
            result = plan(self.runtime, 'model.gguf', 'experimental')
        self.assertIsNone(result['estimate']['ram_budget_bytes'])
        self.assertTrue(any('не реализованы' in text for text in result['warnings']))

    def test_rejects_invalid_profile_missing_or_invalid_model(self):
        with self.assertRaises(ValueError):
            plan(self.runtime, 'model.gguf', 'bogus')
        self.model.unlink()
        with self.assertRaises(ValueError):
            plan(self.runtime, 'model.gguf')
        self.model.write_bytes(b'not-a-real-gguf')
        self.meta.stop()
        with self.assertRaises(ValueError):
            plan(self.runtime, 'model.gguf')

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows physical RAM probe')
    def test_worker_rpc_delegates_without_starting_model(self):
        from unittest.mock import MagicMock
        from rainforge_worker.rpc import Worker
        worker=Worker.__new__(Worker);worker.runtime=MagicMock()
        with patch('rainforge_worker.rpc.smartload_plan',return_value={'limits_enforced':False}) as evaluate:
            result=worker.plan_smartload('model.gguf','balanced')
        self.assertFalse(result['limits_enforced'])
        evaluate.assert_called_once_with(worker.runtime,'model.gguf','balanced')
        worker.runtime.start.assert_not_called()

    def test_windows_physical_ram_probe(self):
        from rainforge_native.smartload import physical_memory
        total, available = physical_memory()
        self.assertIsInstance(total, int)
        self.assertGreater(total, 0)
        self.assertGreaterEqual(available, 0)
        self.assertLessEqual(available, total)

    def test_does_not_change_model_or_start_runtime(self):
        before = self.model.read_bytes()
        with patch('rainforge_native.smartload.physical_memory', return_value=(16 * 1024**3, 8 * 1024**3)):
            result = plan(self.runtime, 'model.gguf', 'balanced')
        self.assertEqual(self.model.read_bytes(), before)
        self.assertEqual(result['estimate']['model_bytes'], len(before))
        self.assertEqual(result['recommendation']['context'], 2048)


if __name__ == '__main__':
    unittest.main()
