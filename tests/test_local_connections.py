import sys,tempfile,unittest,subprocess
from pathlib import Path
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_worker.local_connections import LocalConnections

class LocalConnectorTests(unittest.TestCase):
 def test_files_are_scoped_metadata_only(self):
  worker=MagicMock();worker.workspace_tools.listing.return_value={'files':['src/a.py','src/b.py','README.md']}
  result=LocalConnections(worker).read('local_filesystem','a.py')
  self.assertEqual([i['id'] for i in result['items']],['src/a.py']);self.assertTrue(result['read_only']);worker.workspace_tools.read.assert_not_called()
 def test_browser_requires_existing_target_and_uses_owner_lock(self):
  worker=MagicMock();worker.browser.target=None;local=LocalConnections(worker)
  with self.assertRaises(ValueError):local.read('playwright')
  worker.browser.target='owned';worker.manual_browser.return_value={'title':'Example','text':'English exercise','url':'https://example.com/'}
  result=local.read('playwright');self.assertEqual(result['items'][0]['name'],'Example');worker.manual_browser.assert_called_once_with('inspect')
 def test_search_is_explicit_and_does_not_report_unexecuted_test_as_verified(self):
  local=LocalConnections(MagicMock())
  self.assertFalse(local.test('web_search')['verified'])
  with patch('rainforge_worker.local_connections.connector_query',return_value=[]) as query:
   with self.assertRaises(ValueError):local.read('web_search','')
   query.assert_not_called();local.read('web_search','London');query.assert_called_once_with('wikipedia','London')
 def test_docker_fixed_read_only_args_and_no_command_secrets(self):
  def run(args,**kw):
   self.assertNotIn('shell',args);self.assertFalse(kw['shell']);self.assertEqual(args[1:4],['ps','--last','20']);self.assertNotIn('Command',args[-1]);kw['stdout'].write(b'{"id":"abc","name":"fixture","image":"alpine","state":"running"}\n');return MagicMock(returncode=0)
  with patch('rainforge_worker.local_connections.shutil.which',return_value='C:/Docker/docker.exe'),patch('rainforge_worker.local_connections.subprocess.run',side_effect=run):
   result=LocalConnections(MagicMock()).read('docker','fixture');self.assertEqual(result['items'][0]['name'],'fixture')
 def test_docker_timeout_failure_and_absent_binary(self):
  local=LocalConnections(MagicMock())
  with patch('rainforge_worker.local_connections.shutil.which',return_value=None):
   with self.assertRaisesRegex(ValueError,'not installed'):local.read('docker')
  with patch('rainforge_worker.local_connections.shutil.which',return_value='C:/Docker/docker.exe'),patch('rainforge_worker.local_connections.subprocess.run',side_effect=subprocess.TimeoutExpired('docker',10)):
   with self.assertRaisesRegex(ValueError,'respond'):local.test('docker')
 def test_invalid_query_is_not_executed(self):
  local=LocalConnections(MagicMock())
  with self.assertRaises(ValueError):local.read('docker','x'*201)
  with self.assertRaises(ValueError):local.read('unknown','')
