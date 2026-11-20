import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_worker.actions import ActionEngine,web_url
class ActionTests(unittest.TestCase):
 def test_plan_validation_before_execution(self):
  with tempfile.TemporaryDirectory() as tmp:
   engine=ActionEngine(None,Path(tmp));self.assertEqual(len(engine.validate({'actions':[{'tool':'open_url','url':'https://www.apple.com/'}]})),1)
   for plan in [{'actions':[{'tool':'shell','command':'x'}]},{'actions':[{'tool':'open_app','app':'powershell'}]},{'actions':[{'tool':'open_url','url':'file:///C:/secret'}]},{'actions':[]}]:
    with self.assertRaises(ValueError):engine.validate(plan)
 def test_urls(self):
  for url in ['https://user:secret@example.com','http://127.0.0.1','http://localhost','javascript:alert(1)','https://example.com\nflag']:
   with self.assertRaises(ValueError):web_url(url)

 def test_browser_assert_checks_and_persists_condition_proof(self):
  from unittest.mock import MagicMock,patch
  import json
  with tempfile.TemporaryDirectory() as tmp:
   worker=MagicMock();engine=ActionEngine(worker,Path(tmp))
   worker.browser.act.return_value={'acted':True,'observation':{'url':'https://example.org'}}
   worker.browser.wait_for.return_value={'verified':True,'condition':{'selector':'#result','contains_text':'Done'},'observation':{'text':'Done'}}
   plan={'actions':[{'tool':'browser_click','selector':'#go'},{'tool':'browser_assert','selector':'#result','text':'Done'}]}
   with patch.object(engine,'model_plan',return_value=plan):result=engine.execute('Click Go and check Done')
   self.assertEqual(result['state'],'checks_passed');worker.browser.wait_for.assert_called_once_with('#result','Done')
   saved=json.loads((engine.root/(result['id']+'.json')).read_text(encoding='utf-8'))
   self.assertEqual(saved['results'][-1]['condition']['contains_text'],'Done')
   worker.browser.wait_for.side_effect=ValueError('condition timed out')
   with patch.object(engine,'model_plan',return_value=plan),self.assertRaises(ValueError):engine.execute('Click Go and check missing result')

 def test_verification_request_rejects_model_mutations_before_execution(self):
  from unittest.mock import MagicMock,patch
  with tempfile.TemporaryDirectory() as tmp:
   worker=MagicMock();engine=ActionEngine(worker,Path(tmp))
   with patch.object(engine,'model_plan',return_value={'actions':[{'tool':'browser_click','selector':'a'}]}):
    with self.assertRaisesRegex(ValueError,'план отклонён'):engine.execute('Check that page contains Learn more')
   worker.browser.act.assert_not_called()

 def test_known_sites_domains_without_model(self):
  from rainforge_worker.actions import direct_web_plan
  from unittest.mock import MagicMock
  expected={'открой гмеил':'https://mail.google.com/','открой Gmail':'https://mail.google.com/','открой в гугле сайт эпл':'https://www.apple.com/','открой example.org':'https://example.org','open https://example.org/CaseSensitive?A=B':'https://example.org/CaseSensitive?A=B'}
  for goal,url in expected.items():
   self.assertEqual(direct_web_plan(goal)['actions'][0]['url'],url)
  with tempfile.TemporaryDirectory() as tmp:
   worker=MagicMock();engine=ActionEngine(worker,Path(tmp));worker.browser.navigate.return_value={'url':'https://mail.google.com/','title':'test','text':'test','elements':[]}
   engine.execute('открой гмеил');worker.runtime.status.assert_not_called();worker.browser.navigate.assert_called_once_with('https://mail.google.com/')

 def test_default_browser_fallback(self):
  from unittest.mock import patch
  from rainforge_worker.workspace import WorkspaceTools
  with tempfile.TemporaryDirectory() as tmp:
   w=WorkspaceTools(Path(tmp))
   with patch('pathlib.Path.is_file',return_value=False),patch('os.startfile',create=True) as start:
    result=w.launch('browser','https://mail.google.com/');start.assert_called_once_with('https://mail.google.com/');self.assertEqual(result['executable'],'Windows default browser')

class AppRoutingRegressionTests(unittest.TestCase):
 def test_steam_request_never_invokes_model_or_browser(self):
  from unittest.mock import MagicMock,patch
  with tempfile.TemporaryDirectory() as tmp:
   worker=MagicMock();engine=ActionEngine(worker,Path(tmp))
   worker.workspace_tools.launch.return_value={'app':'steam','state':'launch_requested','verified_window':False}
   worker.desktop.windows.return_value={'windows':[{'title':'Steam','pid':123,'handle':456}]}
   with patch.object(engine,'model_plan') as planner:
    result=engine.execute('открой стим на моем пк он скачан')
   planner.assert_not_called();worker.workspace_tools.launch.assert_called_once_with('steam');worker.browser.navigate.assert_not_called()
   self.assertTrue(result['results'][0]['verified_window']);self.assertIn('обнаружено',result['message'])
 def test_wrong_model_app_rejected_before_any_launch(self):
  from unittest.mock import MagicMock,patch
  with tempfile.TemporaryDirectory() as tmp:
   worker=MagicMock();engine=ActionEngine(worker,Path(tmp))
   with patch.object(engine,'model_plan',return_value={'actions':[{'tool':'open_app','app':'chrome'}]}):
    with self.assertRaises(ValueError):engine.execute('Пожалуйста запусти установленный Spotify')
   worker.workspace_tools.launch.assert_not_called();worker.browser.navigate.assert_not_called()
 def test_requested_browser_uses_managed_session(self):
  from unittest.mock import MagicMock
  with tempfile.TemporaryDirectory() as tmp:
   worker=MagicMock();engine=ActionEngine(worker,Path(tmp));worker.browser.navigate.return_value={'url':'https://duckduckgo.com/','title':'Search','text':'Page data'}
   result=engine.execute('открой браузер')
   worker.workspace_tools.launch.assert_not_called();worker.browser.navigate.assert_called_once_with('https://duckduckgo.com/')
   self.assertIn('Page data',result['message']);self.assertNotIn('Действия:',result['message'])
