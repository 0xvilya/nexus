import os,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_worker.workspace import WorkspaceTools
from rainforge_worker.checks import ProjectChecks
class ProjectCheckTests(unittest.TestCase):
 def _assert_aliased_workspace(self,data):
  workspace=WorkspaceTools(data)
  self.assertEqual(workspace.root,workspace.root.resolve())
  source='import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertEqual(2+2,4)\n'
  workspace.write('project/test_alias.py',source)
  checks=ProjectChecks(SimpleNamespace(workspace_tools=workspace),data)
  self.assertEqual(workspace.listing()['files'],['project/test_alias.py'])
  self.assertEqual(checks._test_hashes('project'),{'project/test_alias.py':workspace.read('project/test_alias.py')['sha256']})
  self.assertTrue(checks.run(True,directory='project')['ok'])
  with self.assertRaises(ValueError):workspace.path('../outside.py')
  with self.assertRaises(ValueError):checks._directory('../')
 def test_noncanonical_workspace_root_uses_one_path_spelling(self):
  with tempfile.TemporaryDirectory() as tmp:
   parent=Path(tmp)/'alias-parent';parent.mkdir()
   self._assert_aliased_workspace(parent/'..')
 def test_hashes_resolve_legacy_workspace_root_alias(self):
  with tempfile.TemporaryDirectory() as tmp:
   workspace=WorkspaceTools(Path(tmp));workspace.write('test_alias.py','pass\n')
   alias_parent=workspace.root/'alias-parent';alias_parent.mkdir()
   workspace.root=alias_parent/'..'
   checks=ProjectChecks(SimpleNamespace(workspace_tools=workspace),Path(tmp))
   self.assertEqual(checks._test_hashes(),{'test_alias.py':workspace.read('test_alias.py')['sha256']})
 @unittest.skipUnless(os.name=='nt','Windows 8.3 path aliases only')
 def test_windows_short_workspace_root_alias(self):
  import ctypes
  from ctypes import wintypes
  get_short=ctypes.WinDLL('kernel32',use_last_error=True).GetShortPathNameW
  get_short.argtypes=[wintypes.LPCWSTR,wintypes.LPWSTR,wintypes.DWORD];get_short.restype=wintypes.DWORD
  with tempfile.TemporaryDirectory(prefix='nexus-long-workspace-alias-') as tmp:
   original=Path(tmp).resolve();buffer=ctypes.create_unicode_buffer(32768)
   length=get_short(str(original),buffer,len(buffer))
   if not length or length>=len(buffer):self.skipTest('GetShortPathNameW unavailable for fixture')
   alias=Path(buffer.value)
   if str(alias).casefold()==str(original).casefold():self.skipTest('8.3 name creation disabled on fixture volume')
   self.assertEqual(alias.resolve(),original)
   self._assert_aliased_workspace(alias)
 def test_real_pass_fail_empty_and_consent(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=SimpleNamespace(workspace_tools=WorkspaceTools(Path(tmp)));c=ProjectChecks(w,Path(tmp))
   with self.assertRaises(ValueError):c.run()
   self.assertFalse(c.run(True)['ok'])
   w.workspace_tools.write('test_sample.py','import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertEqual(2+2,4)\n')
   result=c.run(True);self.assertTrue(result['ok'],result);self.assertEqual(result['tests'],1)
   r=w.workspace_tools.read('test_sample.py');w.workspace_tools.write('test_sample.py','import unittest\nclass T(unittest.TestCase):\n def test_bad(self): self.fail("expected failure")\n',r['sha256'])
   result=c.run(True);self.assertFalse(result['ok']);self.assertIn('expected failure',result['output'])
 def test_timeout(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=SimpleNamespace(workspace_tools=WorkspaceTools(Path(tmp)));c=ProjectChecks(w,Path(tmp))
   w.workspace_tools.write('test_slow.py','import time\ntime.sleep(20)\n')
   result=c.run(True,1);self.assertFalse(result['ok']);self.assertTrue(result['timed_out'])

 def test_changed_same_size_module_is_not_cached(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=SimpleNamespace(workspace_tools=WorkspaceTools(Path(tmp)));c=ProjectChecks(w,Path(tmp))
   r=w.workspace_tools.write('calc.py','def value(): return 0\n')
   w.workspace_tools.write('test_calc.py','import unittest\nfrom calc import value\nclass T(unittest.TestCase):\n def test_value(self): self.assertEqual(value(),1)\n')
   self.assertFalse(c.run(True)['ok'])
   w.workspace_tools.write('calc.py','def value(): return 1\n',r['sha256'])
   result=c.run(True);self.assertTrue(result['ok'],result)
 def test_all_skipped_is_not_accepted(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=SimpleNamespace(workspace_tools=WorkspaceTools(Path(tmp)));c=ProjectChecks(w,Path(tmp))
   w.workspace_tools.write('test_skipped.py','import unittest\n@unittest.skip("not implemented")\nclass T(unittest.TestCase):\n def test_skip(self): pass\n')
   result=c.run(True);self.assertFalse(result['ok']);self.assertEqual(result['skipped'],1)
 def test_invalid_model_reply_preserves_original(self):
  from unittest.mock import MagicMock,patch
  with tempfile.TemporaryDirectory() as tmp:
   w=MagicMock();w.workspace_tools=WorkspaceTools(Path(tmp));w.runtime.status.return_value={'state':'ready'};w.begin_chat.return_value={'chat_id':'one'};w.poll_chat.return_value={'state':'completed','text':'not valid Python !!'}
   w.workspace_tools.write('app.py','def add(a,b): return a-b\n');original=w.workspace_tools.read('app.py');c=ProjectChecks(w,Path(tmp))
   with patch.object(c,'run',return_value={'ok':False,'tests':1,'output':'AssertionError: -1 != 5'}):
    with self.assertRaises(ValueError):c.autofix('app.py',True)
   self.assertEqual(w.workspace_tools.read('app.py'),original)
