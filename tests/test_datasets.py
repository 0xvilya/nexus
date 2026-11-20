import base64
import json
import sys
import tempfile
import unittest
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import bootstrap
from rainforge_datasets import DatasetEngine
from rainforge_datasets.engine import digest
from rainforge_recovery import atomic_json


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = DatasetEngine(self.root / 'data')

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def process(self, source, **options):
        key = self.engine.start(str(source), 'CC0-1.0', **options)['id']
        self.engine.thread.join(timeout=15)
        self.assertFalse(self.engine.thread.is_alive())
        return next(r for r in self.engine.listing()['runs'] if r['id'] == key)

    def test_reproducible_cleaning_splits_hashes_and_tamper_detection(self):
        source = self.root / 'source.jsonl'
        rows = [{'text': f' example {i} ', 'index': i} for i in range(200)]
        rows += [rows[0], {'text': '   '}, {'email': 'person@example.org'}]
        source.write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
        first = self.process(source, exclude_email=True)
        self.assertEqual(first['status'], 'COMPLETED', first['error'])
        self.assertEqual(first['stats']['kept'], 200)
        self.assertEqual(first['stats']['duplicates'], 1)
        self.assertEqual(first['stats']['empty'], 1)
        self.assertEqual(first['stats']['email_excluded'], 1)
        self.assertEqual(first['source_sha256'], digest(source))
        second = self.process(source, exclude_email=True)
        self.assertEqual(first['files'], second['files'])
        groups = []
        for name in ('train', 'validation', 'test'):
            data = self.engine.read(first['id'], name + '.jsonl')
            values = [json.loads(line) for line in data['text'].splitlines()]
            self.assertEqual(len(values), first['stats'][name])
            groups.append({v['index'] for v in values})
        self.assertFalse(groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2])
        self.assertEqual(set.union(*groups), set(range(200)))
        (Path(first['output_dir']) / 'train.jsonl').write_text('tampered', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.engine.read(first['id'], 'train.jsonl')

    def test_csv_multiline_unicode_and_false_zero_are_preserved(self):
        source = self.root / 'input.csv'
        source.write_bytes('name,text\r\n e\u0301 ,"first\nsecond"\r\n'.encode('utf-8-sig'))
        run = self.process(source)
        self.assertEqual(run['status'], 'COMPLETED', run['error'])
        all_text = ''.join((Path(run['output_dir']) / (s + '.jsonl')).read_text(encoding='utf-8') for s in ('train', 'validation', 'test'))
        self.assertEqual(json.loads(all_text), {'name': 'é', 'text': 'first\nsecond'})
        source = self.root / 'values.jsonl'
        source.write_text('{"zero":0,"false":false}\n', encoding='utf-8')
        self.assertEqual(self.process(source)['stats']['kept'], 1)

    def test_invalid_inputs_never_become_verified(self):
        for text, suffix in [('{"x":1,"x":2}', '.jsonl'), ('{"a":NaN}', '.jsonl'), ('[]', '.jsonl'),
                             ('{"empty":" "}', '.jsonl'), ('x,x\n1,2', '.csv'), ('x,y\n1', '.csv'),
                             ('{"x":"' + 'a' * 1048576 + '"}', '.jsonl')]:
            with self.subTest(text=text[:50]):
                source = self.root / ('invalid' + suffix)
                source.write_text(text, encoding='utf-8')
                run = self.process(source)
                self.assertEqual(run['status'], 'FAILED')
                with self.assertRaisesRegex(ValueError, 'not verified'):
                    self.engine.read(run['id'], 'train.jsonl')
        for source in ('relative.csv', '//server/input.csv'):
            with self.assertRaises(ValueError):
                self.engine.start(source, 'unknown')

    def test_source_changes_are_detected_and_cancellation_is_not_completion(self):
        source = self.root / 'input.jsonl'
        source.write_text('{"value":1}\n', encoding='utf-8')
        with patch('rainforge_datasets.engine.digest', return_value='changed'):
            self.assertEqual(self.process(source)['status'], 'FAILED')
        original = self.engine._rows
        def cancelled(*args):
            self.engine.cancel()
            yield from original(*args)
        with patch.object(self.engine, '_rows', side_effect=cancelled):
            self.assertEqual(self.process(source)['status'], 'CANCELLED')

    def test_chunked_upload_offset_limits_and_recovery(self):
        data = b'{"hello":"world"}\n'
        key = self.engine.upload_begin('input.jsonl', len(data))['upload_id']
        with self.assertRaises(ValueError):
            self.engine.upload_chunk(key, 1, base64.b64encode(data).decode())
        with self.assertRaises(ValueError):
            self.engine.upload_finish(key)
        self.engine.upload_chunk(key, 0, base64.b64encode(data).decode())
        result = self.engine.upload_finish(key)
        self.assertEqual(Path(result['source']).read_bytes(), data)
        run = self.process(Path(result['source']))
        self.assertEqual(run['status'], 'COMPLETED')
        state = Path(run['output_dir']) / 'state.json'
        run['status'] = 'VERIFYING'
        atomic_json(state, run)
        recovered = DatasetEngine(self.root / 'data')
        self.assertEqual(recovered.listing()['runs'][0]['status'], 'INTERRUPTED')
        recovered.close()
        with self.assertRaises(ValueError):
            self.engine.read('../outside', 'train.jsonl')

    def test_row_limit_is_enforced_without_publishing_partial_files(self):
        source = self.root / 'input.jsonl'
        source.write_text('{"x":1}\n{"x":2}\n', encoding='utf-8')
        with patch('rainforge_datasets.engine.MAX_ROWS', 1):
            run = self.process(source)
        self.assertEqual(run['status'], 'FAILED')
        self.assertEqual(run['files'], [])
        self.assertFalse((Path(run['output_dir']) / 'train.jsonl').exists())

    def test_history_polling_does_not_race_atomic_progress_commits(self):
        source = self.root / 'polled.jsonl'
        source.write_text('\n'.join(json.dumps({'row': i}) for i in range(2500)), encoding='utf-8')
        original = Path.read_text
        def slow_reader(path, *args, **kwargs):
            if path.name != 'state.json':
                return original(path, *args, **kwargs)
            with path.open('r', encoding=kwargs.get('encoding', 'utf-8')) as stream:
                time.sleep(.005)  # Hold an actual Windows reader during progress updates.
                return stream.read()
        with patch.object(Path, 'read_text', slow_reader):
            key = self.engine.start(str(source), 'CC0-1.0')['id']
            deadline = time.monotonic() + 10
            while self.engine.thread.is_alive():
                self.engine.listing()
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.001)
        run = next(r for r in self.engine.listing()['runs'] if r['id'] == key)
        self.assertEqual(run['status'], 'COMPLETED', run['error'])
        self.assertEqual(run['stats']['kept'], 2500)


if __name__ == '__main__':
    unittest.main()
