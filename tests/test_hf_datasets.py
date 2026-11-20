import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import bootstrap
from rainforge_datasets import DatasetEngine
from rainforge_recovery import atomic_json
from rainforge_worker.hf_datasets import HFDatasets, _trusted_url, describe


class Response(io.BytesIO):
    status = 206


class HFDatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = DatasetEngine(Path(self.tmp.name))
        self.hf = HFDatasets(self.engine)

    def tearDown(self):
        self.hf.close()
        self.engine.close()
        self.tmp.cleanup()

    def metadata(self, data, kind='git-sha1'):
        hash = hashlib.sha256(data).hexdigest() if kind == 'sha256' else hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        return {'repo': 'owner/data', 'revision': 'a'*40, 'license': 'CC0-1.0', 'url': 'https://huggingface.co/datasets/owner/data/tree/'+'a'*40,
                'files': [{'name': 'input.csv', 'bytes': len(data), 'hash': hash, 'hash_kind': kind}]}

    def start(self, info):
        self.hf.start(info['repo'], info['revision'], 'input.csv', True)
        self.hf.thread.join(timeout=10)
        self.assertFalse(self.hf.thread.is_alive())
        return self.hf.status()

    def test_pinned_git_and_lfs_download_hashes_and_cache(self):
        data = b'x,y\n1,2\n'
        for kind in ('git-sha1', 'sha256'):
            info = self.metadata(data, kind)
            response = Response(data)
            response.headers = {'Content-Range': f'bytes 0-{len(data)-1}/{len(data)}'}
            with patch('rainforge_worker.hf_datasets.describe', return_value=info), patch('rainforge_worker.hf_datasets._open', return_value=response) as request:
                result = self.start(info)
                self.assertEqual(result['status'], 'COMPLETED', result.get('error'))
                self.assertEqual(Path(result['source']).read_bytes(), data)
                self.assertEqual(result['sha256'], hashlib.sha256(data).hexdigest())
                self.start(info)
                self.assertEqual(request.call_count, 1 if kind == 'git-sha1' else 0)

    def test_cancellation_retains_partial_and_resume_checks_range(self):
        data = b'x\n' + b'row\n'*40000
        info = self.metadata(data)
        response = Response(data)
        response.headers = {'Content-Range': f'bytes 0-{len(data)-1}/{len(data)}'}
        original = response.read
        def cancelling_read(size):
            block = original(size)
            self.hf.cancel()
            return block
        response.read = cancelling_read
        with patch('rainforge_worker.hf_datasets.describe', return_value=info), patch('rainforge_worker.hf_datasets._open', return_value=response):
            result = self.start(info)
        self.assertEqual(result['status'], 'CANCELLED')
        partial = next(self.hf.root.glob('*.part'))
        offset = partial.stat().st_size
        self.assertEqual(offset, 65536)
        response = Response(data[offset:])
        response.headers = {'Content-Range': f'bytes {offset}-{len(data)-1}/{len(data)}'}
        with patch('rainforge_worker.hf_datasets.describe', return_value=info), patch('rainforge_worker.hf_datasets._open', return_value=response) as request:
            result = self.start(info)
            self.assertEqual(request.call_args.args[1]['Range'], f'bytes={offset}-')
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual(Path(result['source']).read_bytes(), data)

    def test_corruption_and_bad_range_are_never_registered(self):
        info = self.metadata(b'x\n1\n')
        for content_range, body in [('bytes 1-4/4', b'x\n1\n'), ('bytes 0-3/4', b'evil')]:
            response = Response(body)
            response.headers = {'Content-Range': content_range}
            with patch('rainforge_worker.hf_datasets.describe', return_value=info), patch('rainforge_worker.hf_datasets._open', return_value=response):
                result = self.start(info)
            self.assertEqual(result['status'], 'FAILED')
            self.assertIsNone(result['source'])
        self.assertFalse(list(self.hf.root.glob('*.csv')))

    def test_metadata_consent_paths_and_redirect_boundaries(self):
        raw = {'sha': 'b'*40, 'cardData': {'license': 'cc0-1.0'}, 'siblings': [
            {'rfilename': 'data/test.csv', 'size': 100, 'blobId': 'c'*40},
            {'rfilename': '../bad.csv', 'size': 100, 'blobId': 'd'*40},
            {'rfilename': 'script.py', 'size': 100, 'blobId': 'd'*40}]}
        with patch('rainforge_worker.hf_datasets._open', return_value=Response(json.dumps(raw).encode())):
            result = describe('owner/data')
        self.assertEqual([f['name'] for f in result['files']], ['data/test.csv'])
        with self.assertRaises(ValueError):
            self.hf.start('owner/data', 'b'*40, 'data/test.csv', False)
        for repo in ('../outside', 'https://evil/data', 'owner/data?url=evil'):
            with self.assertRaises(ValueError):
                describe(repo)
        for url in ('http://huggingface.co/file', 'https://huggingface.co.evil.test/file', 'https://127.0.0.1/file', 'https://user:secret@huggingface.co/file'):
            with self.assertRaises(ValueError):
                _trusted_url(url)
        _trusted_url('https://cas-bridge.xethub.hf.co/file')
        self.hf._update(status='DOWNLOADING')
        self.assertEqual(HFDatasets(self.engine).status()['status'], 'INTERRUPTED')

    @unittest.skipUnless(os.getenv('RF_HF_DATASET_TEST') == '1', 'Live Hugging Face dataset check not requested')
    def test_real_public_iris_download_and_pipeline(self):
        info = describe('scikit-learn/iris')
        self.hf.start(info['repo'], info['revision'], 'Iris.csv', True)
        self.hf.thread.join(timeout=90)
        result = self.hf.status()
        self.assertEqual(result['status'], 'COMPLETED', result.get('error'))
        key = self.engine.start(result['source'], result['license'], result['provenance'])['id']
        self.engine.thread.join(timeout=15)
        run = next(r for r in self.engine.listing()['runs'] if r['id'] == key)
        self.assertEqual(run['status'], 'COMPLETED', run['error'])
        self.assertEqual(run['stats']['kept'], 150)
        atomic_json(bootstrap.ROOT / 'work/hf-dataset-results.json', {'status': 'PASS', 'repository': info['repo'],
                    'revision': info['revision'], 'source_sha256': result['sha256'], 'license': result['license'],
                    'stats': run['stats'], 'files': run['files']})


if __name__ == '__main__':
    unittest.main()
