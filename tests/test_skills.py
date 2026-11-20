import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import bootstrap
from rainforge_core import NexusService
from rainforge_worker.skills import Skills
from rainforge_worker.workspace import WorkspaceTools
from rainforge_worker.rpc import Worker


class SkillTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        self.core = NexusService(self.data)
        self.workspace = WorkspaceTools(self.data)
        self.skills = Skills(self.core, self.data, self.workspace)
        self.written = self.workspace.write('greet.py', 'print("Hello")\n')
        self.record = {
            'id': 'a' * 32, 'goal': 'Create Python greeting program', 'state': 'file_created',
            'plan': [{'tool': 'create_python', 'path': 'greet.py'}],
            'results': [{**self.written, 'syntax_verified': True}], 'error': None,
        }

    def test_failed_setting_save_keeps_live_and_persisted_state_consistent(self):
        with patch('rainforge_worker.skills.atomic_json', side_effect=OSError('write rejected')):
            with self.assertRaises(OSError):
                self.skills.configure(True)
        self.assertFalse(self.skills.listing()['enabled'])
        self.assertFalse(self.skills.path.exists())
        self.assertEqual(self.skills.capture(self.record)['state'], 'disabled')

        self.skills.configure(True)
        before = self.skills.path.read_bytes()
        with patch('rainforge_worker.skills.atomic_json', side_effect=OSError('write rejected')):
            with self.assertRaises(OSError):
                self.skills.configure(False)
        self.assertTrue(self.skills.listing()['enabled'])
        self.assertEqual(self.skills.path.read_bytes(), before)
        restored = Skills(self.core, self.data, self.workspace)
        self.assertTrue(restored.listing()['enabled'])

    def test_evidence_pending_approval_shared_recall_and_restart(self):
        self.assertEqual(self.skills.capture(self.record)['state'], 'disabled')
        self.skills.configure(True)
        self.assertEqual(self.skills.capture(self.record)['state'], 'proposed')
        item = self.skills.listing()['items'][0]
        self.assertEqual(item['state'], 'pending')
        self.core.memory_enable(True)
        self.assertEqual(self.core.memory_context('Python'), '')
        self.skills.approve(item['id'], item['sha256'])
        self.assertIn('create_python', self.core.memory_context('Python'))
        self.assertIn('Execution and behavior were not verified', self.core.memory_context('Python'))
        restored = Skills(NexusService(self.data), self.data, self.workspace)
        self.assertTrue(restored.listing()['enabled'])
        self.assertEqual(restored.listing()['items'][0]['state'], 'approved')
        self.assertEqual(restored.capture(self.record)['state'], 'duplicate')
        self.core.memory_enable(False)
        self.assertEqual(self.core.memory_context('Python'), '')

    def test_no_skill_from_answer_failed_partial_or_changed_file(self):
        self.skills.configure(True)
        for state in ('answered', 'failed', 'partial', 'awaiting_permission'):
            self.assertEqual(self.skills.capture({**self.record, 'state': state})['state'], 'ignored')
        self.assertEqual(self.skills.capture({**self.record, 'in_flight': {'tool': 'create_python'}})['state'], 'ignored')
        self.workspace.write('greet.py', 'print("Changed")\n', self.written['sha256'])
        self.assertEqual(self.skills.capture(self.record)['state'], 'ignored')
        self.assertEqual(self.skills.listing()['items'], [])

    def test_verified_condition_and_no_plain_observation_success(self):
        self.skills.configure(True)
        record = {**self.record, 'state': 'checks_passed', 'plan': [{'tool': 'browser_assert'}],
                  'results': [{'verified': True}], 'proof': {'verified': True, 'actual': 'Done'}}
        self.assertEqual(self.skills.capture(record)['state'], 'proposed')
        other = {**record, 'id': 'b' * 32, 'goal': 'Different check', 'proof': {'verified': False}}
        self.assertEqual(self.skills.capture(other)['state'], 'ignored')
        self.assertEqual(self.skills.capture({**other, 'state': 'observed'})['state'], 'ignored')

    def test_delete_dedup_hash_conflict_and_general_memory_edit(self):
        self.skills.configure(True)
        self.skills.capture(self.record)
        item = self.skills.listing()['items'][0]
        with self.assertRaises(ValueError):
            self.skills.approve(item['id'], 'stale')
        self.skills.remove(item['id'], item['sha256'])
        self.assertEqual(self.core.memory_list()['notes'], [])
        self.assertEqual(self.skills.capture(self.record)['state'], 'duplicate')
        restored = Skills(self.core, self.data, self.workspace)
        self.assertEqual(restored.capture(self.record)['state'], 'duplicate')

    def test_scan_backfill_excludes_secret_and_full_memory_preserves_existing(self):
        self.skills.configure(True)
        self.data.joinpath('actions').mkdir()
        self.data.joinpath('actions', 'a' * 32 + '.json').write_text(json.dumps(self.record), encoding='utf-8')
        self.assertEqual(len(self.skills.listing()['items']), 1)
        secret = {**self.record, 'id': 'b' * 32, 'goal': 'Create script with api_key=private-value'}
        self.assertEqual(self.skills.capture(secret)['state'], 'ignored')
        with patch.object(self.core, 'memory_propose', side_effect=ValueError('memory full')):
            self.assertEqual(self.skills.capture({**self.record, 'id': 'b' * 32, 'goal': 'Create another Python file'})['state'], 'memory_full')
        self.assertEqual(len(self.skills.listing()['items']), 1)

    def test_interrupted_index_write_reuses_existing_pending_note(self):
        self.skills.configure(True)
        with patch('rainforge_worker.skills.atomic_json', side_effect=OSError('interrupted save')):
            with self.assertRaises(OSError):
                self.skills.capture(self.record)
        self.assertEqual(len(self.core.memory_list()['notes']), 1)
        restored = Skills(self.core, self.data, self.workspace)
        self.assertEqual(restored.capture(self.record)['state'], 'proposed')
        self.assertEqual(len(self.core.memory_list()['notes']), 1)

    def test_public_rpc_proposes_after_controller_file_creation(self):
        with patch('rainforge_worker.rpc.detect_gpu', return_value=[]):
            worker = Worker(self.data / 'rpc-profile', bootstrap.ROOT / 'runtime')
        self.addCleanup(worker.close)
        worker.dispatch('skills.configure', {'enabled': True})
        self.assertEqual(worker.dispatch('skills.list', {})['items'], [])
        goal = 'Напиши Python файл greeting.py с приветствием Hello'
        with patch.object(worker.actions, 'model_plan', return_value={
            'actions': [{'tool': 'create_python', 'path': 'greeting.py'}],
        }), patch.object(worker.actions, 'generate', return_value=(
            'def greeting():\n    return "Hello"\n\nif __name__ == "__main__":\n    print(greeting())\n'
        )):
            started = worker.dispatch('task.start', {'goal': goal, 'mode': 'auto'})
            worker.actions.thread.join(timeout=5)
        self.assertFalse(worker.actions.thread.is_alive())
        task = worker.dispatch('task.status', {'run_id': started['id']})
        self.assertEqual(task['state'], 'file_created')
        self.assertTrue(worker.workspace_tools.path('greeting.py').is_file())
        data = worker.dispatch('skills.list', {})
        self.assertEqual(len(data['items']), 1)
        item = data['items'][0]
        self.assertEqual(item['run_id'], started['id'])
        self.assertEqual(item['state'], 'pending')
        self.assertEqual(self.core.memory_list()['notes'], [])
        worker.dispatch('memory.enable', {'enabled': True})
        self.assertEqual(worker.core.memory_context('Python'), '')

        worker.dispatch('skills.approve', {'skill_id': item['id'], 'expected_sha256': item['sha256']})
        self.assertIn('greeting.py', worker.core.memory_context('Python'))
        worker.dispatch('skills.remove', {'skill_id': item['id'], 'expected_sha256': item['sha256']})
        self.assertEqual(worker.dispatch('skills.list', {})['items'], [])
        self.assertEqual(worker.core.memory_context('Python'), '')

    def test_corrupt_optional_index_does_not_break_existing_memory(self):
        note = self.core.memory_save('Prefer concise replies')
        self.skills.path.write_text('{incomplete JSON', encoding='utf-8')
        restored = Skills(self.core, self.data, self.workspace)
        self.assertIn('could not be read', restored.listing()['error'])
        self.assertEqual(self.core.memory_list()['notes'][0]['id'], note['id'])
        with self.assertRaises(ValueError):
            restored.configure(True)
        self.assertEqual(self.skills.path.read_text(encoding='utf-8'), '{incomplete JSON')


if __name__ == '__main__':
    unittest.main()
