import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_worker.actions import ActionEngine,conversational_launch
from rainforge_worker.workspace import WorkspaceTools
from rainforge_worker.app_catalog import AppCatalog

class SemanticIntentTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.worker=MagicMock();self.worker.workspace_tools=WorkspaceTools(Path(self.tmp.name));self.worker.runtime.status.return_value={'state':'ready'}
  self.engine=ActionEngine(self.worker,Path(self.tmp.name));self.rows=[{'name':'Telegram','app_id':'Telegram.TelegramDesktop'},{'name':'Discord','app_id':'Discord.Desktop'}]
  self.inventory=patch.object(self.worker.workspace_tools.apps,'inventory',return_value=self.rows);self.inventory.start()
  self.telegram=self.worker.workspace_tools.apps.resolve('Telegram')['id']
  self.label='a'+str(next(i for i,e in enumerate(self.worker.workspace_tools.apps.listing()['apps']) if e['id']==self.telegram))
 def tearDown(self):self.engine.close();self.inventory.stop();self.tmp.cleanup()
 def test_model_chooses_catalog_identity_without_commands(self):
  with patch.object(self.engine,'generate',return_value=__import__('json').dumps({'app_name':'Telegram','question':''})) as model:
   r=self.engine.resolve_app_intent('Будь добр открой мне телегу на компьютере')
  self.assertEqual(r['name'],'Telegram');self.assertEqual(r['app'],self.telegram)
  self.assertIn('app_name',model.call_args.kwargs['schema']['properties'])
  self.worker.browser.navigate.assert_not_called()
 def test_ambiguous_missing_and_forged_do_not_launch(self):
  for answer in ['{"app_name":"","question":"Какой мессенджер открыть?"}','{"app_name":"MissingForgedApp","question":""}']:
   with patch.object(self.engine,'generate',return_value=answer):
    if 'Forged' in answer:
     self.assertIsNone(self.engine.resolve_app_intent('открой мессенджер')['app'])
    else:self.assertIsNone(self.engine.resolve_app_intent('открой мессенджер')['app'])
  self.worker.browser.navigate.assert_not_called()
 def test_full_compound_goal_survives_semantic_resolution(self):
  goal='пожалуйста открой мне телегу и напиши контакту Error привет'
  self.worker.desktop.windows.return_value={'windows':[]}
  with patch.object(self.engine,'resolve_app_intent',return_value={'app':self.telegram,'name':'Telegram','question':None}),patch.object(self.worker.workspace_tools,'launch',return_value={'app':self.telegram,'name':'Telegram','verified_window':False}) as launch,patch.object(self.engine,'model_plan',side_effect=[{'actions':[{'tool':'open_app','app':self.telegram}]},{'actions':[{'tool':'finish','summary':'Нужно разрешение на ввод'}]}]):
   self.worker.poll_chat.return_value={'state':'completed','text':'Приложение открыто, сообщение не отправлено.'}
   r=self.engine.execute(goal,use_model=True,allow_interaction=False)
  launch.assert_called_once_with(self.telegram);self.assertEqual(r['goal'],goal);self.assertEqual(r['interpreted_app']['name'],'Telegram')
  self.worker.desktop.act.assert_not_called();self.worker.browser.navigate.assert_not_called()
 def test_readonly_and_negated_requests_not_routed_to_resolver(self):
  for goal in ['Что такое Telegram?','do not open Telegram','не открой Telegram']:
   self.assertFalse(conversational_launch(goal))
  self.assertTrue(conversational_launch('можешь открыть мне телегу пожалуйста'))

if __name__=='__main__':unittest.main()
