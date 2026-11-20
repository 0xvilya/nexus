import json,sys,tempfile,unittest,time,os,subprocess,base64,shutil
from pathlib import Path
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_worker.actions import ActionEngine
from rainforge_worker.workspace import WorkspaceTools
from rainforge_worker.desktop import DesktopTools

class ProjectGenerationTests(unittest.TestCase):
 def setup_engine(self,tmp):
  w=MagicMock();w.workspace_tools=WorkspaceTools(Path(tmp));w.desktop.current=None;w.browser.target=None
  w.runtime.status.return_value={'state':'ready','model':'coder.gguf'}
  return w,ActionEngine(w,Path(tmp))
 def test_project_files_readback_and_explicit_new_directory_export(self):
  with tempfile.TemporaryDirectory() as tmp,tempfile.TemporaryDirectory() as desktop:
   w,e=self.setup_engine(tmp)
   manifest={'files':[{'path':'main.py','purpose':'Entry'},{'path':'config.json','purpose':'Settings'}]}
   with patch.object(e,'model_plan',return_value={'actions':[{'tool':'create_project','path':'example'}]}),patch.object(e,'generate',side_effect=[json.dumps(manifest),'def add(a,b):\n return a+b\n','{"name":"example"}']):
    r=e.execute('Create a Python project',use_model=True)
   self.assertEqual(r['state'],'project_created');self.assertFalse(r['results'][0]['behavior_verified']);self.assertEqual(len(r['project_files']),2)
   with patch('rainforge_worker.workspace.desktop_directory',return_value=Path(desktop)):
    exported=e.export_desktop(r['id']);self.assertEqual(exported['files'],2)
    self.assertTrue((Path(exported['path'])/'main.py').is_file())
    with self.assertRaises(FileExistsError):e.export_desktop(r['id'])
   w.browser.navigate.assert_not_called()
 def test_bad_manifest_cannot_escape_overwrite_or_use_reserved_paths(self):
  for paths in (['../outside.py'],['CON.py'],['same.py','SAME.py'],['secret.exe']):
   with self.subTest(paths=paths),tempfile.TemporaryDirectory() as tmp:
    w,e=self.setup_engine(tmp);record={'id':'a'*32}
    with patch.object(e,'generate',return_value=json.dumps({'files':[{'path':p,'purpose':'test'} for p in paths]})):
     with self.assertRaises(ValueError):e.create_project('Create a project','test',record)
    self.assertEqual(w.workspace_tools.listing()['files'],[])
 def test_partial_drafts_are_kept_without_false_project_success(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,e=self.setup_engine(tmp);record={'id':'b'*32}
   responses=[json.dumps({'files':[{'path':'README.md','purpose':'Help'},{'path':'main.py','purpose':'Code'}]}),'# Example\n','def incomplete(','def incomplete(','def incomplete(']
   with patch.object(e,'generate',side_effect=responses):
    with self.assertRaisesRegex(ValueError,'ранее созданные файлы сохранены'):e.create_project('Create a project','test',record)
   self.assertEqual(len(record['project_files']),1);self.assertEqual(len(w.workspace_tools.listing()['files']),1)
 def test_calculator_eval_with_globals_and_locals_is_replaced_not_rejected(self):
  import ast
  with tempfile.TemporaryDirectory() as tmp:
   w,e=self.setup_engine(tmp)
   with patch.object(e,'generate',return_value='def calculate(text):\n    return eval(text, {"__builtins__": None}, {})\n'):
    result=e.create_python('напиши калькулятор на питоне','calc.py',{'id':'c'*32})
   self.assertNotIn('eval(',result['text']);scope={'__name__':'reviewed_test'};exec(result['text'],scope)
   self.assertEqual(scope['calculate']('2+3*4'),14)
   self.assertTrue(str(scope['calculate']("__import__('os')")).startswith('Error'))
 def test_reported_typo_and_explicit_desktop_copy_with_one_real_component_test(self):
  from rainforge_worker.actions import calculator_request
  self.assertTrue(calculator_request('напиши еалькулятор на пайтон'))
  self.assertFalse(calculator_request('напиши валидатор JSON на Python'))
  with tempfile.TemporaryDirectory() as tmp,tempfile.TemporaryDirectory() as desktop:
   w,e=self.setup_engine(tmp)
   goal='напиши еалькулятор на пайтон на моем пк с интерфейсом красивым и создай на рабочем столе его и запусти 1 тест'
   with patch.object(e,'model_plan',return_value={'actions':[{'tool':'create_python','path':'calc.py'}]}),patch.object(e,'generate',return_value='def calculate(text):\n    return eval(text, {}, {})\n'),patch('rainforge_worker.workspace.desktop_directory',return_value=Path(desktop)):
    r=e.execute(goal,use_model=True)
   self.assertEqual(r['state'],'file_created');self.assertTrue(Path(r['desktop_export']['path']).is_file())
   check=r['results'][0]['component_tests'][0];self.assertTrue(check['passed']);self.assertEqual(check['actual'],14)
   self.assertIn('GUI',r['events'][-1]['message']);self.assertEqual(len(list(Path(desktop).iterdir())),1)
 def test_separate_grid_containers_do_not_reject_valid_calculator_layout(self):
  source='import tkinter as tk\ndef main():\n root=tk.Tk()\n display=tk.Entry(root)\n display.grid(row=0,column=0)\n frame=tk.Frame(root)\n frame.grid(row=1,column=0)\n buttons=[("1",0,0),("=",0,1)]\n for label,row,column in buttons:\n  tk.Button(frame,text=label).grid(row=row,column=column)\n root.mainloop()\nif __name__ == "__main__":\n main()\n'
  with tempfile.TemporaryDirectory() as tmp:
   w,e=self.setup_engine(tmp)
   with patch.object(e,'generate',return_value=source) as model:
    r=e.create_python('Create a Python calculator','calc.py',{'id':'9'*32})
   self.assertTrue(r['syntax_verified']);self.assertEqual(model.call_count,1)
 def test_cli_repair_does_not_require_a_calculator_gui(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,e=self.setup_engine(tmp)
   with patch.object(e,'generate',side_effect=['def broken(','def count(text):\n    return len(text.split())\n']) as model:
    result=e.create_python('Write Python word counter CLI','counter.py',{'id':'d'*32})
   self.assertNotIn('Use tkinter',model.call_args.args[0]);self.assertTrue(result['syntax_verified'])
 def test_exact_file_extraction_and_metadata_repair(self):
  from rainforge_worker.actions import extract_file_source
  self.assertEqual(extract_file_source('```html\n<p>other file</p>\n```\n```javascript\nconsole.log(1);\n```','app.js'),'console.log(1);\n')
  self.assertEqual(extract_file_source('const sample = "```";','app.js'),'const sample = "```";\n')
  with self.assertRaises(ValueError):extract_file_source('{"path":"index.html","purpose":"echo","interface_excerpt":"data"}','app.js')
  with tempfile.TemporaryDirectory() as tmp:
   w,e=self.setup_engine(tmp)
   with patch.object(e,'generate',side_effect=[json.dumps({'files':[{'path':'app.js','purpose':'Script'}]}),'{"path":"other.html","interface_excerpt":"echo"}','console.log("repaired");']):
    result=e.create_project('Create JavaScript project','test',{'id':'f'*32})
   self.assertEqual(w.workspace_tools.read(result['files'][0]['path'])['text'].strip(),'console.log("repaired");')
 @unittest.skipUnless(shutil.which('node'),'Optional Node.js syntax parser unavailable')
 def test_js_syntax_parser_never_executes_source(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,e=self.setup_engine(tmp);marker=Path(tmp)/'must_not_exist.txt'
   source="import fs from 'node:fs'; fs.writeFileSync("+json.dumps(str(marker)) +", 'executed');"
   self.assertTrue(w.workspace_tools.check_source('app.js',source)['ok']);self.assertFalse(marker.exists())
   self.assertFalse(w.workspace_tools.check_source('app.js','const = broken;')['ok'])
 def test_additional_language_source_is_saved_but_not_claimed_compiled(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,e=self.setup_engine(tmp)
   with patch.object(e,'generate',side_effect=[json.dumps({'files':[{'path':'src/main.rs','purpose':'Rust program'}]}),'fn main() { println!("Hello"); }']):
    r=e.create_project('Create a Rust project','rust_app',{'id':'e'*32})
   self.assertEqual(r['files'][0]['syntax']['kind'],'unsupported');self.assertFalse(r['behavior_verified'])

class WindowWatchTests(unittest.TestCase):
 def selected(self):
  d=DesktopTools();d.current={'token':'a'*32,'handle':42,'pid':7,'title':'Fixture','observed_at':time.time()};return d
 @unittest.skipUnless(os.environ.get('RF_DESKTOP_TEST')=='1','Opt-in native UIA label-race fixture')
 def test_sensitive_label_is_rechecked_at_native_invoke_time(self):
  encoded=base64.b64encode((bootstrap.ROOT/'tests/fixtures/desktop-fixture.ps1').read_text(encoding='utf-8-sig').encode('utf-16-le')).decode()
  process=subprocess.Popen(['powershell.exe','-NoProfile','-STA','-EncodedCommand',encoded],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  try:
   d=DesktopTools();until=time.monotonic()+20;window=None
   while time.monotonic()<until:
    window=next((w for w in d.windows()['windows'] if w['pid']==process.pid),None)
    if window:break
    time.sleep(.2)
   self.assertIsNotNone(window);selected=d.inspect(window['handle']);edit=next(e for e in selected['elements'] if e.get('automation_id')=='nameBox')
   d.call({'operation':'type','handle':selected['handle'],'pid':selected['pid'],'element':edit['id'],'text':'RENAME_TO_SEND'})
   with self.assertRaises(PermissionError):d.act(selected['token'],'Apply','invoke',require_confirmation=True)
   actual=d.inspect(window['handle']);self.assertTrue(any(e.get('name')=='Waiting' for e in actual['elements']))
  finally:process.terminate();process.wait(timeout=5)
 def test_permission_expiry_and_no_grant_reuse(self):
  d=self.selected()
  with patch.object(d,'call') as capture:
   with self.assertRaises(ValueError):d.watch_start('a'*32)
   d.current['observed_at']=0
   with self.assertRaises(ValueError):d.watch_start('a'*32,True)
   capture.assert_not_called()
 def test_one_frame_buffer_target_binding_and_revocation(self):
  d=self.selected();count=[0]
  def capture(config):
   self.assertEqual(config['handle'],42);self.assertEqual(config['pid'],7);count[0]+=1
   return {'image':'data:image/jpeg;base64,FRAME'+str(count[0])}
  with patch.object(d,'call',side_effect=capture),patch('rainforge_worker.desktop.WATCH_INTERVAL',.01):
   w=d.watch_start('a'*32,True);d.current={'handle':99,'pid':88,'token':'other'}
   until=time.monotonic()+2
   while d.watch_status(w['id'])['frames']<2 and time.monotonic()<until:time.sleep(.01)
   status=d.watch_status(w['id']);self.assertGreaterEqual(status['frames'],2);self.assertNotIn('frame',status);self.assertNotIn('image',status)
   self.assertTrue(d.watch_frame(w['id'])['image'].startswith('data:image/jpeg'))
   d.watch_stop(w['id']);d.watch['thread'].join(1)
   self.assertIsNone(d.watch_frame(w['id']));self.assertFalse(d.watch_status(w['id'])['active']);self.assertFalse(d.watch['thread'].is_alive())
 def test_capture_failure_stops_and_clears_frame(self):
  d=self.selected()
  with patch.object(d,'call',side_effect=ValueError('Window identity changed')):
   w=d.watch_start('a'*32,True);d.watch['thread'].join(1)
  self.assertFalse(d.watch_status(w['id'])['active']);self.assertIsNone(d.watch_frame(w['id']));self.assertIn('identity changed',d.watch_status(w['id'])['error'])
 def test_native_outcome_is_checked_from_fresh_uia_not_model_claim(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=MagicMock();w.workspace_tools=WorkspaceTools(Path(tmp));w.browser.target=None
   w.desktop.current={'token':'selected','handle':42,'pid':7,'elements':[]}
   w.desktop.act.return_value={'observation':{'token':'fresh','handle':42,'pid':7,'elements':[{'name':'Hello RainForge'}]}}
   e=ActionEngine(w,Path(tmp))
   with patch.object(e,'model_plan',return_value={'actions':[{'tool':'desktop_invoke','element':'Apply'}]}) as planner:
    r=e.execute('Click Apply until the window contains Hello RainForge.',use_model=True,allow_interaction=True)
   self.assertEqual(r['state'],'checks_passed');self.assertEqual(planner.call_count,1);self.assertEqual(r['proof']['source'],'Windows UI Automation')
 def test_completed_native_input_is_masked_from_next_decision(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=MagicMock();w.workspace_tools=WorkspaceTools(Path(tmp));w.browser.target=None;w.runtime.status.return_value={'state':'ready','model':'coder.gguf'}
   w.begin_chat.return_value={'chat_id':'test'};w.poll_chat.return_value={'state':'completed','text':'{"actions":[{"tool":"desktop_invoke","element":"Apply"}]}'}
   e=ActionEngine(w,Path(tmp));snapshot={'elements':[{'name':'nameBox','type_text':True},{'name':'Apply','invoke':True}]}
   e.model_plan('Type RainForge then click Apply',snapshot,allow_interaction=True,history=[{'action':{'tool':'desktop_type','element':'nameBox','text':'RainForge'},'result':{'acted':True}}])
   tools=w.begin_chat.call_args.kwargs['schema']['properties']['actions']['items']['oneOf']
   self.assertNotIn('desktop_type',[v['properties']['tool']['enum'][0] for v in tools])
 def test_task_watch_uses_local_vision_and_leaves_no_frame_in_journal(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=MagicMock();w.workspace_tools=WorkspaceTools(Path(tmp));w.desktop.current=None;w.browser.target='unrelated'
   w.runtime.status.return_value={'state':'ready','model':'coder.gguf'}
   w.snapshot.return_value={'models':[{'filename':'vision.gguf','downloaded':True,'projector':'projector.gguf'}]}
   watch={'id':'b'*32,'active':True,'frames':1,'title':'Selected'}
   w.desktop.watch_start.return_value=watch;w.desktop.watch_status.return_value=watch
   w.desktop.watch_frame.return_value={'sequence':1,'image':'data:image/jpeg;base64,PRIVATEFRAME','at':time.time()}
   w.desktop.watch_stop.return_value={**watch,'active':False}
   w.begin_chat.return_value={'chat_id':'test'}
   w.poll_chat.side_effect=[{'state':'completed','text':'{"actions":[{"tool":"finish","summary":"Observed window"}]}','vision_description':'A selected fixture'},{'state':'completed','text':'Selected fixture description'}]
   e=ActionEngine(w,Path(tmp));run=e.start('Describe selected window',watch_token='a'*32,vision_filename='vision.gguf');e.thread.join(3)
   r=e.status(run['id']);self.assertEqual(r['state'],'answered');self.assertFalse(r['screen_watch']['active'])
   self.assertTrue(w.begin_chat.call_args_list[0].kwargs['image'].startswith('data:image/jpeg'))
   self.assertNotIn('PRIVATEFRAME',(e.root/(run['id']+'.json')).read_text(encoding='utf-8'));w.browser.observe.assert_not_called();w.desktop.watch_stop.assert_called()

if __name__=='__main__':unittest.main()
