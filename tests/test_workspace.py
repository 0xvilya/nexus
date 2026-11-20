import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_worker.workspace import WorkspaceTools
class WorkspaceTests(unittest.TestCase):
 def test_create_read_change_and_syntax(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=WorkspaceTools(Path(tmp));r=w.write('app.py','def add(a,b):\n return a+b\n');self.assertTrue(w.check('app.py')['ok'])
   with self.assertRaises(ValueError):w.write('app.py','overwritten')
   w.write('app.py','def broken(',r['sha256']);self.assertFalse(w.check('app.py')['ok'])
   r=w.read('app.py');w.write('app.py','def fixed():\n return 1',r['sha256']);self.assertTrue(w.check('app.py')['ok'])
 def test_boundary_and_launch_arguments(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=WorkspaceTools(Path(tmp))
   for p in ['../outside.py','C:/outside.py','.hidden.py','app.exe']:
    with self.assertRaises(ValueError):w.write(p,'bad')
   with self.assertRaises(ValueError):w.launch('notepad & powershell')
   with patch('subprocess.Popen',return_value=MagicMock(pid=123)) as process:
    r=w.launch('notepad');self.assertEqual(r['pid'],123);self.assertFalse(process.call_args.kwargs['shell']);self.assertEqual(len(process.call_args.args[0]),1)

 def test_intent_routes_exact_command_and_rejects_unknown(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=WorkspaceTools(Path(tmp))
   with patch.object(w,'launch',return_value={'executable':'chrome.exe','state':'launch_requested'}) as launch:
    w.execute('открой гугл браузер');launch.assert_called_once_with('chrome')
   with self.assertRaises(ValueError):w.execute('создай приложение')
   with self.assertRaises(ValueError):w.execute('открой chrome & powershell')
   self.assertEqual(w.listing()['files'],[])
