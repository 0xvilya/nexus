import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import bootstrap
from rainforge_worker.hub import import_model, connector_query
from rainforge_native import NativeRuntime

class HubTests(unittest.TestCase):
    def test_import_is_persistent_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'own.gguf'; key=b'general.architecture'; value=b'llama'
            source.write_bytes(b'GGUF'+struct.pack('<IQQ',3,0,1)+struct.pack('<Q',len(key))+key+struct.pack('<IQ',8,len(value))+value)
            a=import_model(root/'models',str(source)); b=import_model(root/'models',str(source))
            self.assertEqual(a['id'],b['id'])
            self.assertEqual(len(json.loads((root/'models/custom.json').read_text())),1)
            self.assertEqual(source.read_bytes(),(root/'models'/a['filename']).read_bytes())

    def test_invalid_import_connector_and_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError): import_model(Path(tmp),'relative.gguf')
        with self.assertRaises(ValueError): connector_query('unknown','hello')
        with self.assertRaises(ValueError): connector_query('github','')
        with self.assertRaises(ValueError): NativeRuntime(bootstrap.ROOT/'runtime').start('model.gguf',mode='arbitrary')

    def test_expanded_catalog_has_pinned_downloads(self):
        catalog=json.loads((bootstrap.ROOT/'runtime/catalog.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(len(catalog['models']),12)
        self.assertEqual(len({m['id'] for m in catalog['models']}),len(catalog['models']))
        for m in catalog['models']:
            self.assertIn(m['architecture'],['llama','qwen2'])
            self.assertRegex(m['sha256'],r'^[0-9a-f]{64}$')
            self.assertIn('/resolve/'+m['revision']+'/',m['url'])
            self.assertGreater(m['bytes'],0)
            self.assertNotEqual(m['license'],'other')

    def test_new_connector_contracts(self):
        from unittest.mock import patch,MagicMock
        cases=[('gitlab',[{'path_with_namespace':'group/project','description':'test','web_url':'https://gitlab.com/group/project'}]),('wikipedia',{'pages':[{'title':'Python','key':'Python','excerpt':'<b>Language</b>'}]}),('huggingface_datasets',[{'id':'org/data'}]),('huggingface_spaces',[{'id':'org/space'}])]
        for provider,payload in cases:
            response=MagicMock();response.__enter__.return_value=response;response.read.return_value=json.dumps(payload).encode()
            with patch('urllib.request.urlopen',return_value=response) as opened:
                result=connector_query(provider,'test&limit=999')
                self.assertEqual(len(result),1)
                self.assertTrue(result[0]['url'].startswith('https://'))
                self.assertNotIn('<b>',result[0]['description'])
                self.assertIn('%26limit%3D999',opened.call_args.args[0].full_url)
