import os,sys,time,subprocess,base64,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_worker.desktop import DesktopTools,LiveVision
from rainforge_worker.actions import native_model_controls,native_model_reading

class ScreenReadingTests(unittest.TestCase):
 def test_goal_relevance_exposes_control_beyond_first24_and_schema_stays_bounded(self):
  state={'elements':[{'id':str(i),'name':'Button '+str(i),'invoke':True} for i in range(80)]}
  state['elements'][-1]['name']='Translate English exercise'
  controls=native_model_controls(state,'Translate the English exercise')
  self.assertEqual(controls[0]['name'],'Translate English exercise');self.assertLessEqual(len(controls),24)
  state['elements'][0]['name']='x'*10000
  self.assertNotIn('x'*10000,str(native_model_controls(state,'Button')))
 def test_accessible_text_is_bounded_includes_values_and_excludes_passwords(self):
  state={'elements':[{'name':'Password','password':True,'value':'never-copy'},{'name':'Answer','value':'London'}]+[{'name':'Paragraph '+str(i)+'x'*500} for i in range(60)]}
  text=native_model_reading(state)
  self.assertIn('Answer: London',text);self.assertNotIn('never-copy',text);self.assertLessEqual(len(text),2400)
 def test_detail_capture_preserves_grant_and_routes_without_widening_other_sources(self):
  tool=DesktopTools();tool.current={'token':'selected','handle':1,'pid':2,'observed_at':time.time()}
  with patch.object(tool,'call',return_value={'image':'preview'}) as call:
   with self.assertRaises(ValueError):tool.capture('selected',False,'text')
   with self.assertRaises(ValueError):tool.capture('selected',True,'unbounded')
   call.assert_not_called();tool.capture('selected',True,'text')
   self.assertEqual(call.call_args.args[0],{'operation':'capture','handle':1,'pid':2,'detail':'text'})
 def test_unchanged_capture_confirms_old_description_but_changed_frame_does_not(self):
  tool=MagicMock();runtime=MagicMock();vision=LiveVision(tool,runtime)
  vision.state={'watch_id':'owned'};vision.latest={'description':'Hello','sequence':1,'captured_at':100}
  tool.watch_frame.return_value={'sequence':1,'at':200}
  self.assertEqual(vision.observation('owned')['confirmed_at'],200)
  tool.watch_frame.return_value={'sequence':2,'at':300}
  self.assertNotIn('confirmed_at',vision.observation('owned'))
  tool.watch_frame.return_value=None;self.assertIsNone(vision.observation('owned'))
 @unittest.skipUnless(os.environ.get('RF_DESKTOP_TEST')=='1','Opt-in native controlled UI fixture')
 def test_actual_selected_window_text_values_password_exclusion_and_text_capture(self):
  fixture=bootstrap.ROOT/'tests/fixtures/screen-reading.ps1'
  encoded=base64.b64encode(fixture.read_text(encoding='utf-8').encode('utf-16-le')).decode()
  p=subprocess.Popen(['powershell.exe','-NoProfile','-STA','-EncodedCommand',encoded],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));tool=DesktopTools()
  try:
   end=time.monotonic()+25;window=None
   while time.monotonic()<end:
    window=next((w for w in tool.windows()['windows'] if w['pid']==p.pid),None)
    if window:break
    time.sleep(.25)
   self.assertIsNotNone(window);state=tool.inspect(window['handle']);reading=native_model_reading(state)
   self.assertIn('English answer: Cambridge',reading);self.assertNotIn('EXCLUDED_SECRET_246',str(state))
   self.assertFalse(any(e['type']=='ControlType.Edit' and e.get('automation_id')=='privateInput' for e in state['elements']))
   image=tool.capture(state['token'],True,'text');raw=base64.b64decode(image['image'].split(',',1)[1])
   self.assertTrue(raw.startswith(b'\xff\xd8'));self.assertLessEqual(len(raw),65000)
   self.assertGreater(max(image['width'],image['height']),640);self.assertLessEqual(max(image['width'],image['height']),1280)
  finally:tool.close();p.terminate();p.wait(timeout=10)

if __name__=='__main__':unittest.main()
