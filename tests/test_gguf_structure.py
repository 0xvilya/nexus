import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import bootstrap
from rainforge_native.gguf import metadata
from rainforge_native.smartload import plan, model_structure, kv_estimate


def write_header(path, fields):
    def string(value):
        value = value.encode('utf-8')
        return struct.pack('<Q', len(value)) + value
    raw = b'GGUF' + struct.pack('<IQQ', 3, 0, len(fields))
    for key, value in fields:
        raw += string(key)
        if isinstance(value, str):
            raw += struct.pack('<I', 8) + string(value)
        elif isinstance(value, list):
            raw += struct.pack('<IIQ', 9, 4, len(value)) + struct.pack('<' + 'I' * len(value), *value)
        else:
            raw += struct.pack('<IQ', 10, value)
    path.write_bytes(raw)


class GGUFStructureTests(unittest.TestCase):
    def test_real_header_retains_dense_dimensions_and_uses_gqa(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'dense.gguf'
            fields = [
                ('general.architecture', 'llama'), ('llama.block_count', 32),
                ('llama.embedding_length', 4096), ('llama.attention.head_count', 32),
                ('llama.attention.head_count_kv', 8), ('llama.context_length', 4096),
                ('general.parameter_count', 7000000000),
            ]
            write_header(path, fields)
            info = metadata(path)
            self.assertEqual(info['general.parameter_count'], 7000000000)
            structure = model_structure(info)
            self.assertEqual(structure['kind'], 'dense')
            self.assertEqual(kv_estimate(structure, 2048), 268435456)
            runtime = SimpleNamespace(model_path=lambda filename: path)
            with patch('rainforge_native.smartload.physical_memory', return_value=(16*1024**3, 8*1024**3)):
                result = plan(runtime, 'dense.gguf', 'balanced')
            self.assertEqual(result['estimate']['kv_cache_bytes'], 268435456)
            self.assertEqual(result['estimate']['kv_cache_ram_bytes'], 268435456)
            self.assertEqual(result['estimate']['kv_cache_vram_bytes'], 0)
            self.assertIsNone(result['hardware']['vram_available_bytes'])
            self.assertFalse(result['limits_enforced'])
            self.assertEqual(result['estimate']['kv_cache_type'], 'f16')

    def test_moe_is_declared_not_claimed_as_validated_runtime_support(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'moe.gguf'
            write_header(path, [('general.architecture', 'llama'),
                                ('llama.expert_count', 8), ('llama.expert_used_count', 2),
                                ('llama.expert_shared_count', 1)])
            runtime = SimpleNamespace(model_path=lambda filename: path)
            result = plan(runtime, 'moe.gguf')
            self.assertEqual(result['model']['kind'], 'moe')
            self.assertEqual(result['model']['expert_count'], 8)
            self.assertEqual(result['model']['active_expert_count'], 2)
            self.assertEqual(result['model']['shared_expert_count'], 1)
            self.assertIsNone(result['estimate']['kv_cache_bytes'])
            self.assertTrue(any('не проверены' in warning for warning in result['warnings']))
            write_header(path, [('general.architecture', 'qwen2moe')])
            with self.assertRaisesRegex(ValueError, 'allowlist'):
                metadata(path)

    def test_missing_inconsistent_and_array_dimensions_produce_unknown_kv(self):
        base = {'general.architecture': 'qwen2', 'qwen2.block_count': 28,
                'qwen2.embedding_length': 1536, 'qwen2.attention.head_count': 12}
        for replacements in ({'qwen2.block_count': None}, {'qwen2.block_count': True},
                             {'qwen2.attention.head_count_kv': 24},
                             {'qwen2.attention.head_count_kv': 5},
                             {'qwen2.embedding_length': 1537}):
            with self.subTest(replacements=replacements):
                self.assertIsNone(kv_estimate(model_structure({**base, **replacements}), 2048))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'variable.gguf'
            write_header(path, list(base.items()) + [('qwen2.attention.head_count_kv', [2, 4])])
            self.assertIsNone(kv_estimate(model_structure(metadata(path)), 2048))

    def test_head_defaults_and_explicit_key_value_lengths(self):
        fields = {'general.architecture': 'llama', 'llama.block_count': 2,
                  'llama.embedding_length': 256, 'llama.attention.head_count': 4}
        self.assertEqual(kv_estimate(model_structure(fields), 512), 1048576)
        fields.update({'llama.attention.key_length': 80, 'llama.attention.value_length': 96})
        self.assertEqual(kv_estimate(model_structure(fields), 512), 1441792)
        fields['llama.expert_used_count'] = 2
        self.assertEqual(model_structure(fields)['kind'], 'unknown')

    def test_context_limits_and_large_file_assessment_are_estimates(self):
        info = {'general.architecture': 'llama', 'llama.block_count': 32,
                'llama.embedding_length': 4096, 'llama.attention.head_count': 32,
                'llama.attention.head_count_kv': 8, 'llama.context_length': 1024}
        for gib in (20, 35, 68, 100, 128):
            path = unittest.mock.MagicMock()
            path.is_file.return_value = True
            path.stat.return_value.st_size = gib * 1024**3
            runtime = SimpleNamespace(model_path=lambda filename: path)
            with patch('rainforge_native.smartload.metadata', return_value=info), \
                 patch('rainforge_native.smartload.physical_memory', return_value=(16*1024**3, 8*1024**3)), \
                 patch('rainforge_native.smartload.shutil.disk_usage', return_value=SimpleNamespace(free=200*1024**3)):
                result = plan(runtime, 'large.gguf', 'performance')
            self.assertEqual(result['recommendation']['context'], 1024)
            self.assertEqual(result['model']['bytes'], gib*1024**3)
            self.assertFalse(result['limits_enforced'])
            self.assertTrue(any('превышает' in warning for warning in result['warnings']))
            json.dumps(result)

    def test_duplicate_retained_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'duplicate.gguf'
            write_header(path, [('general.architecture', 'llama'),
                                ('llama.block_count', 2), ('llama.block_count', 4)])
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                metadata(path)


if __name__ == '__main__':
    unittest.main()
