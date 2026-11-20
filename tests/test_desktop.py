import sys,os,time,subprocess,unittest,base64
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_worker.desktop import DesktopTools
class DesktopTests(unittest.TestCase):
 def test_pattern_operations_are_bound_to_observed_capability_and_sensitive_labels(self):
  tool=DesktopTools();state={'token':'ok','observed_at':time.time(),'handle':1,'pid':2,'elements':[{'id':'box','name':'Enable fixture','toggle':True,'toggle_state':'Off','enabled':True}]};tool.current=state
  with patch.object(tool,'call',return_value={'acted':True}) as call,patch.object(tool,'inspect',return_value=state):
   tool.act('ok','box','toggle',require_confirmation=True)
   self.assertEqual(call.call_args.args[0]['expected_state'],'Off');self.assertEqual(call.call_args.args[0]['expected_name'],'Enable fixture')
   with self.assertRaises(ValueError):tool.act('ok','box','select')
   state['elements'][0]['name']='Delete';call.reset_mock()
   with self.assertRaises(PermissionError):tool.act('ok','box','toggle',require_confirmation=True)
   call.assert_not_called()
 @unittest.skipUnless(os.environ.get('RF_DESKTOP_TEST')=='1','Opt-in real Windows GUI test')
 def test_real_selection_toggle_tree_and_repeated_scrolling(self):
  encoded=base64.b64encode((bootstrap.ROOT/'tests/fixtures/desktop-patterns.ps1').read_text(encoding='utf-8-sig').encode('utf-16-le')).decode()
  proc=subprocess.Popen(['powershell.exe','-NoProfile','-STA','-EncodedCommand',encoded],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  try:
   tool=DesktopTools();deadline=time.monotonic()+20;window=None
   while time.monotonic()<deadline:
    window=next((w for w in tool.windows()['windows'] if w['pid']==proc.pid),None)
    if window:break
    time.sleep(.2)
   self.assertIsNotNone(window);state=tool.inspect(window['handle'])
   def act(name,op):
    nonlocal state
    item=next(e for e in state['elements'] if (e.get('name')==name or e.get('automation_id')==name) and e.get(op))
    state=tool.act(state['token'],item['id'],op,require_confirmation=True)['observation']
   act('Enable fixture','toggle');self.assertTrue(any(e['name']=='PATTERN READY' for e in state['elements']))
   act('Beta','select');self.assertTrue(next(e for e in state['elements'] if e['name']=='Beta' and e.get('selected')))
   act('Branch','expand');self.assertTrue(any(e['name']=='Leaf' for e in state['elements']))
   act('Branch','collapse');self.assertTrue(any(e['name']=='Branch' and e.get('expand') for e in state['elements']))
   act('scrollBox','scroll_down');first=next(e['scroll_percent'] for e in state['elements'] if e.get('automation_id')=='scrollBox')
   act('scrollBox','scroll_down');second=next(e['scroll_percent'] for e in state['elements'] if e.get('automation_id')=='scrollBox');self.assertGreater(second,first)
   act('scrollBox','scroll_up');self.assertLess(next(e['scroll_percent'] for e in state['elements'] if e.get('automation_id')=='scrollBox'),second)
  finally:proc.terminate();proc.wait(timeout=5)
 def test_ai_sensitive_invoke_requires_manual_action(self):
  tool=DesktopTools();tool.current={'token':'ok','observed_at':time.time(),'handle':1,'pid':2,'elements':[{'id':'send','name':'Send','invoke':True}]}
  with patch.object(tool,'call') as call:
   with self.assertRaises(PermissionError):tool.act('ok','send','invoke',require_confirmation=True)
   call.assert_not_called()
 def test_fresh_sensitive_label_error_is_handed_back(self):
  tool=DesktopTools();tool.current={'token':'ok','observed_at':time.time(),'handle':1,'pid':2,'elements':[{'id':'apply','name':'Apply','invoke':True}]}
  with patch.object(tool,'call',side_effect=ValueError('manual_confirmation_required')) as call:
   with self.assertRaises(PermissionError):tool.act('ok','Apply','invoke',require_confirmation=True)
   self.assertTrue(call.call_args.args[0]['require_confirmation'])
 def test_capture_requires_one_time_permission_and_current_snapshot(self):
  tool=DesktopTools()
  with self.assertRaises(ValueError):tool.capture('missing')
  with self.assertRaises(ValueError):tool.capture('missing',True)
  tool.current={'token':'selected','handle':1,'pid':2,'observed_at':time.time()}
  with patch.object(tool,'call',return_value={'image':'preview'}) as call:
   self.assertEqual(tool.capture('selected',True)['image'],'preview')
   self.assertEqual(call.call_args.args[0],{'operation':'capture','handle':1,'pid':2})
 def test_snapshot_gate(self):
  tool=DesktopTools()
  with self.assertRaises(ValueError):tool.act('missing','1','invoke')
  tool.current={'token':'ok','observed_at':0,'elements':[]}
  with self.assertRaises(ValueError):tool.act('ok','1','type','text')
 def test_name_match_ignores_non_invokable_text_child(self):
  tool=DesktopTools();state={'token':'ok','observed_at':time.time(),'handle':1,'pid':2,'elements':[{'id':'button','name':'Apply','invoke':True},{'id':'caption','name':'Apply','invoke':False}]};tool.current=state
  with patch.object(tool,'call',return_value={'acted':True}) as call,patch.object(tool,'inspect',return_value=state):
   tool.act('ok','Apply','invoke');self.assertEqual(call.call_args.args[0]['element'],'button')
 @unittest.skipUnless(os.environ.get('RF_DESKTOP_TEST')=='1','Opt-in real Windows GUI test')
 def test_real_windows_input_button_and_changed_label(self):
  encoded=base64.b64encode((bootstrap.ROOT/'tests/fixtures/desktop-fixture.ps1').read_text(encoding='utf-8-sig').encode('utf-16-le')).decode()
  proc=subprocess.Popen(['powershell.exe','-NoProfile','-STA','-EncodedCommand',encoded],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  try:
   tool=DesktopTools();deadline=time.monotonic()+20;window=None
   while time.monotonic()<deadline:
    window=next((w for w in tool.windows()['windows'] if w['pid']==proc.pid),None)
    if window:break
    time.sleep(.2)
   self.assertIsNotNone(window)
   state=tool.inspect(window['handle']);edit=next(e for e in state['elements'] if e['type']=='ControlType.Edit');state=tool.act(state['token'],edit['id'],'type','RainForge')['observation']
   image=tool.capture(state['token'],True)
   raw=base64.b64decode(image['image'].split(',',1)[1]);self.assertTrue(raw.startswith(b'\xff\xd8'));self.assertLessEqual(len(raw),65000)
   self.assertLessEqual(max(image['width'],image['height']),640)
   button=next(e for e in state['elements'] if e['name']=='Apply');state=tool.act(state['token'],button['id'],'invoke')['observation']
   self.assertTrue(any(e['name']=='Hello RainForge' for e in state['elements']),state)
  finally:proc.terminate();proc.wait(timeout=5)

