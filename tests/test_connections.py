import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import bootstrap
from rainforge_worker.connections import Connections
class ConnectionsTests(unittest.TestCase):
 def test_dpapi_roundtrip_and_no_secret_in_catalog(self):
  import os
  if os.name!='nt':self.skipTest('Windows DPAPI required')
  with tempfile.TemporaryDirectory() as tmp:
   c=Connections(Path(tmp));secret='example-secret-token'
   c.save('github',secret)
   self.assertNotIn(secret.encode(),c.path('github').read_bytes())
   self.assertNotIn(secret,str(c.catalog()))
   with patch('urllib.request.OpenerDirector.open',side_effect=OSError(secret)):
    self.assertNotIn(secret,str(c.test('github')))
   c.disconnect('github');self.assertFalse(c.path('github').exists())
 def test_invalid_providers_and_tokens(self):
  with tempfile.TemporaryDirectory() as tmp:
   c=Connections(Path(tmp))
   with self.assertRaises(ValueError):c.save('../escape','testtoken')
   with self.assertRaises(ValueError):c.save('github','bad\r\nheader')
   self.assertTrue({'github','gmail','slack','telegram','local_filesystem','playwright','web_search','docker','google_docs','google_calendar','outlook','onedrive','dropbox','discord','todoist','trello','huggingface'} <= {v['id'] for v in c.catalog()})
