import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_worker.app_catalog import AppCatalog,INVENTORY_SCRIPT
from rainforge_worker.actions import ActionEngine,direct_app_plan
from rainforge_worker.workspace import WorkspaceTools

ROWS=[{'name':'Telegram','app_id':'Telegram.TelegramDesktop','target':r'C:\Apps\Telegram.exe'},
      {'name':'Visual Studio Code','app_id':'VSCode','target':r'C:\Apps\Code.exe'},
      {'name':'Calculator','app_id':'Microsoft.WindowsCalculator_test!App','package':r'C:\Apps\Calculator'}]

class AppCatalogTests(unittest.TestCase):
 def test_conversational_possessives_preserve_native_compound_request(self):
  from rainforge_worker.actions import desktop_request
  catalog=AppCatalog()
  with patch.object(catalog,'inventory',return_value=ROWS):
   for name in ('мой тг','мою телеграм','my Telegram','the Telegram'):
    self.assertEqual(catalog.resolve(name)['name'],'Telegram')
   goal='открой мой тг и напиши сообщение контакту Error привет'
   plan,compound=desktop_request(goal,catalog)
   self.assertTrue(compound);self.assertEqual(catalog.get(plan['actions'][0]['app'])['name'],'Telegram')
   self.assertEqual(desktop_request('открой мой блокнот',catalog)[0]['actions'][0]['app'],'notepad')
   with self.assertRaisesRegex(ValueError,'не найдено'):catalog.resolve('мой UnknownMissingApp')
  named=AppCatalog()
  with patch.object(named,'inventory',return_value=ROWS+[{'name':'My Telegram','app_id':'MyTelegram.Exact'}]):
   self.assertEqual(named.resolve('My Telegram')['app_id'],'MyTelegram.Exact')
 def test_aliases_transliteration_and_unambiguous_names(self):
  catalog=AppCatalog()
  with patch.object(catalog,'inventory',return_value=ROWS) as inventory:
   for name in ('Telegram','телеграм','телеграмм','тг'):
    self.assertEqual(catalog.resolve(name)['name'],'Telegram')
   self.assertEqual(catalog.resolve('вс код')['name'],'Visual Studio Code')
   self.assertEqual(catalog.resolve('Studio Code')['name'],'Visual Studio Code')
   self.assertEqual(catalog.resolve('Калькулятор')['name'],'Calculator')
   self.assertEqual(inventory.call_count,1)
   catalog.listing(refresh=True);self.assertEqual(inventory.call_count,2)

 def test_ambiguity_missing_and_injection_never_launch(self):
  catalog=AppCatalog();rows=ROWS+[{'name':'Telegram Beta','app_id':'Telegram.Beta'}]
  with patch.object(catalog,'inventory',return_value=rows),patch('os.startfile',create=True) as launch:
   self.assertEqual(catalog.resolve('Telegram')['name'],'Telegram')
   # A shared token cannot silently select between installed applications.
   rows[0]={'name':'Telegram Desktop','app_id':'Telegram.TelegramDesktop'};catalog.load(True)
   with self.assertRaisesRegex(ValueError,'полное название'):catalog.resolve('Telegram')
   with self.assertRaisesRegex(ValueError,'не найдено'):catalog.resolve('Definitely Missing App')
   for name in ('Telegram & calc','C:\\Apps\\foo.exe','https://example.org',';cmd','!!!'):
    with self.assertRaises(ValueError):catalog.resolve(name)
   with self.assertRaises(ValueError):catalog.launch('installed:forged')
   launch.assert_not_called()

 def test_inventory_filters_urls_scripts_and_administrative_entries(self):
  catalog=AppCatalog()
  bad=[{'name':'Site','app_id':'https://example.org'}, {'name':'Bad','app_id':'bad\nvalue'},
       {'name':'Uninstall App','app_id':'uninstall.App'}, {'name':'PowerShell','app_id':'PowerShell'},
       {'name':'Safe-looking name','app_id':'Cmd','target':r'C:\Windows\System32\cmd.exe'}]
  with patch.object(catalog,'inventory',return_value=ROWS+bad):
   self.assertEqual(len(catalog.listing()['apps']),3)
   self.assertNotIn('target',catalog.listing()['apps'][0])
   with self.assertRaises(ValueError):catalog.listing(refresh='yes')

 def test_launch_only_uses_windows_catalog_identity(self):
  catalog=AppCatalog()
  with patch.object(catalog,'inventory',return_value=ROWS),patch('os.startfile',create=True) as launch:
   telegram=catalog.resolve('телеграм');result=catalog.launch(telegram['id'])
   launch.assert_called_once_with('shell:AppsFolder\\Telegram.TelegramDesktop')
   self.assertFalse(result['verified_window'])
   self.assertEqual(result['state'],'launch_requested')
   # Store apps use their own registered identity through the same API.
   catalog.launch(catalog.resolve('Calculator')['id'])
   self.assertEqual(launch.call_args.args[0],'shell:AppsFolder\\Microsoft.WindowsCalculator_test!App')

 def test_observation_requires_native_process_identity_not_page_title(self):
  catalog=AppCatalog()
  with patch.object(catalog,'inventory',return_value=ROWS),patch('os.startfile',create=True):
   result=catalog.launch(catalog.resolve('Telegram')['id'])
   window={'title':'Telegram','pid':123,'handle':456}
   with patch('rainforge_worker.app_catalog.process_image',return_value=r'C:\Apps\chrome.exe'):
    self.assertFalse(catalog.observe(result,[window])['verified_window'])
   with patch('rainforge_worker.app_catalog.process_image',return_value=r'C:\Apps\Telegram.exe'):
    observed=catalog.observe(result,[window]);self.assertTrue(observed['verified_window']);self.assertEqual(observed['window'],window)

 def test_inventory_failure_is_not_cached_as_empty_success(self):
  catalog=AppCatalog()
  with patch.object(catalog,'inventory',side_effect=[RuntimeError('unavailable'),ROWS]):
   with self.assertRaises(RuntimeError):catalog.listing()
   self.assertIsNone(catalog.loaded_at)
   self.assertEqual(len(catalog.listing()['apps']),3)

 def test_exact_screenshot_request_routes_without_model_or_browser(self):
  with tempfile.TemporaryDirectory() as tmp:
   worker=MagicMock();worker.workspace_tools=WorkspaceTools(Path(tmp));engine=ActionEngine(worker,Path(tmp))
   with patch.object(worker.workspace_tools.apps,'inventory',return_value=ROWS),patch('os.startfile',create=True) as launch,patch.object(engine,'model_plan') as planner:
    worker.desktop.windows.return_value={'windows':[]}
    result=engine.execute('открой телеграм на моем компе')
    planner.assert_not_called();worker.browser.navigate.assert_not_called();launch.assert_called_once_with('shell:AppsFolder\\Telegram.TelegramDesktop')
    self.assertIn('Telegram',result['message']);self.assertEqual(result['state'],'launch_requested')
    for goal in ('open Telegram on my pc','запусти приложение «Telegram» на моём компьютере','открой Telegram на пк'):
     self.assertEqual(direct_app_plan(goal,worker.workspace_tools.apps)['actions'][0]['app'],result['plan'][0]['app'])
    with self.assertRaisesRegex(ValueError,'не найдено'):engine.execute('открой Missing App на моем компе')
    self.assertEqual(launch.call_count,1);planner.assert_not_called()

 def test_model_cannot_substitute_another_installed_app(self):
  with tempfile.TemporaryDirectory() as tmp:
   worker=MagicMock();worker.workspace_tools=WorkspaceTools(Path(tmp));engine=ActionEngine(worker,Path(tmp))
   with patch.object(worker.workspace_tools.apps,'inventory',return_value=ROWS),patch('os.startfile',create=True) as launch:
    identity=worker.workspace_tools.apps.resolve('Telegram')['id']
    with patch.object(engine,'model_plan',return_value={'actions':[{'tool':'open_app','app':identity}]}):
     with self.assertRaisesRegex(ValueError,'не соответствует'):engine.execute('Покажи погоду')
    launch.assert_not_called()

 @unittest.skipUnless(os.name=='nt' and os.environ.get('RF_APPS_TEST')=='1','Opt-in real Windows app inventory')
 def test_native_windows_inventory(self):
  catalog=AppCatalog();apps=catalog.listing(refresh=True)['apps']
  self.assertGreater(len(apps),0)
  self.assertTrue(all(a['id'].startswith('installed:') and a['name'] for a in apps))
  self.assertEqual(len({a['id'] for a in apps}),len(apps))

if __name__=='__main__':unittest.main()
