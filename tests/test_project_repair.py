import sys,tempfile,time,unittest,json
from pathlib import Path
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_worker.workspace import WorkspaceTools
from rainforge_worker.checks import ProjectChecks

class RepairTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name)
  self.worker=MagicMock();self.worker.workspace_tools=WorkspaceTools(self.data)
  self.worker.runtime.status.return_value={'state':'ready'}
  self.worker.begin_chat.return_value={'chat_id':'one'}
  self.worker.poll_chat.return_value={'state':'completed','text':'def value(): return 1\n'}
  self.checks=ProjectChecks(self.worker,self.data)
  self.original=self.worker.workspace_tools.write('calc.py','def value(): return 0\n')
  self.test=self.worker.workspace_tools.write('test_calc.py','import unittest\nfrom calc import value\nclass T(unittest.TestCase):\n def test_value(self): self.assertEqual(value(),1)\n')
 def tearDown(self):self.checks.close();self.tmp.cleanup()
 def wait(self):
  self.checks.thread.join(timeout=15);self.assertFalse(self.checks.thread.is_alive());return self.checks.repair_status()
 def test_real_test_repair_verify_restore_and_test_immutability(self):
  self.checks.repair_start(['calc.py'],True,3);result=self.wait()
  self.assertEqual(result['state'],'passed',result);self.assertEqual(len(result['checks']),2)
  self.assertFalse(result['checks'][0]['ok']);self.assertTrue(result['checks'][1]['ok'])
  self.assertEqual(self.worker.workspace_tools.read('test_calc.py'),self.test)
  self.assertNotIn('text',result['files'][0]);self.assertEqual(self.worker.begin_chat.call_args.kwargs['max_tokens'],2048)
  self.checks.repair_restore(result['id']);self.assertEqual(self.worker.workspace_tools.read('calc.py'),self.original)
 def test_two_files_two_rounds(self):
  self.worker.workspace_tools.write('other.py','def other(): return 0\n')
  self.worker.workspace_tools.write('test_other.py','import unittest\nfrom other import other\nclass T(unittest.TestCase):\n def test_other(self): self.assertEqual(other(),2)\n')
  self.worker.poll_chat.side_effect=[{'state':'completed','text':s} for s in ['def value(): return 0\n','def other(): return 2\n','def value(): return 1\n','def other(): return 2\n']]
  self.checks.repair_start(['calc.py','other.py'],True,2);result=self.wait()
  self.assertEqual(result['state'],'passed',result);self.assertEqual(result['round'],2);self.assertEqual(result['checks'][-1]['tests'],2)
 def test_restore_preflights_all_conflicts(self):
  self.checks.repair_start(['calc.py'],True);result=self.wait()
  current=self.worker.workspace_tools.read('calc.py');self.worker.workspace_tools.write('calc.py','USER CHANGE',current['sha256'])
  with self.assertRaisesRegex(ValueError,'изменён пользователем'):self.checks.repair_restore(result['id'])
  self.assertEqual(self.worker.workspace_tools.read('calc.py')['text'],'USER CHANGE')
 def test_invalid_code_no_write_and_missing_permission(self):
  with self.assertRaises(ValueError):self.checks.repair_start(['calc.py'])
  for paths in [['test_calc.py'],['../calc.py'],['calc.py','calc.py'],['setup.py']]:
   with self.assertRaises(ValueError):self.checks.repair_start(paths,True)
  self.worker.poll_chat.return_value={'state':'completed','text':'def broken('}
  self.checks.repair_start(['calc.py'],True);result=self.wait()
  self.assertEqual(result['state'],'failed');self.assertEqual(self.worker.workspace_tools.read('calc.py'),self.original)
 def test_cancel_generation_and_lock(self):
  self.worker.poll_chat.return_value={'state':'running','text':''}
  self.checks.repair_start(['calc.py'],True)
  deadline=time.monotonic()+10
  while self.checks.chat is None and time.monotonic()<deadline:time.sleep(.05)
  self.assertIsNotNone(self.checks.chat)
  with self.assertRaises(RuntimeError):self.checks.run(True)
  with self.assertRaises(RuntimeError):self.checks.repair_start(['calc.py'],True)
  self.checks.repair_cancel();result=self.wait();self.assertEqual(result['state'],'cancelled')
  self.worker.cancel_chat.assert_called_once();self.assertEqual(self.worker.workspace_tools.read('calc.py'),self.original)
 def test_no_tests_does_not_generate(self):
  with patch.object(self.checks,'_run',return_value={'tests':0,'ok':False}):
   self.checks.repair_start(['calc.py'],True);self.assertEqual(self.wait()['state'],'failed')
  self.worker.begin_chat.assert_not_called()
 def test_recovery_pending_write_and_restore(self):
  self.checks.repair_start(['calc.py'],True);result=self.wait()
  file=self.checks.repair_root/(result['id']+'.json');record=json.loads(file.read_text())
  record['state']='running';record['pending']={'path':'calc.py','sha256':record['current']['calc.py']};record['current']['calc.py']=self.original['sha256']
  file.write_text(json.dumps(record),encoding='utf-8');recovered=ProjectChecks(self.worker,self.data)
  self.assertEqual(recovered.repair_status()['state'],'interrupted')
  recovered.repair_restore(result['id']);self.assertEqual(self.worker.workspace_tools.read('calc.py'),self.original)
 def test_conflict_during_generation_preserves_user_edit(self):
  def answer(_):
   self.worker.workspace_tools.write('calc.py','def value(): return 99\n',self.original['sha256'])
   return {'state':'completed','text':'def value(): return 1\n'}
  self.worker.poll_chat.side_effect=answer
  self.checks.repair_start(['calc.py'],True);result=self.wait()
  self.assertEqual(result['state'],'failed');self.assertIn('99',self.worker.workspace_tools.read('calc.py')['text'])
 def test_syntax_failure_is_repaired_in_next_round(self):
  self.worker.poll_chat.side_effect=[{'state':'completed','text':'def broken('},{'state':'completed','text':'def value(): return 1\n'}]
  self.checks.repair_start(['calc.py'],True,2);result=self.wait()
  self.assertEqual(result['state'],'passed',result);self.assertEqual(result['round'],2)
  self.assertIn('PREVIOUS REJECTED CANDIDATE',self.worker.begin_chat.call_args.args[0])
 def test_changed_tests_cannot_produce_pass(self):
  def tamper(*_,**kwargs):
   self.worker.workspace_tools.write('test_calc.py','',self.test['sha256']);return {'tests':1,'ok':True}
  with patch.object(self.checks,'_run',side_effect=tamper):
   self.checks.repair_start(['calc.py'],True);result=self.wait()
  self.assertEqual(result['state'],'failed');self.assertIn('Тестовые файлы изменились',result['message'])
 def test_nested_project_excludes_unrelated_failing_tests(self):
  self.worker.workspace_tools.write('project/value.py','def value(): return 1\n')
  self.worker.workspace_tools.write('project/test_value.py','import unittest\nfrom value import value\nclass T(unittest.TestCase):\n def test_value(self): self.assertEqual(value(),1)\n')
  with self.assertRaises(ValueError):self.checks.repair_start(['calc.py'],True,directory='project')
  for directory in ['../','/','project/../','missing']:
   with self.assertRaises(ValueError):self.checks.run(True,directory=directory)
  self.checks.repair_start(['project/value.py'],True,directory='project');result=self.wait()
  self.assertEqual(result['state'],'passed',result);self.assertEqual(result['checks'][0]['tests'],1)
  self.worker.begin_chat.assert_not_called()

if __name__=='__main__':unittest.main()
