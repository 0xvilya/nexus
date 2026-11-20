import sys,time,threading,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_worker.desktop import LiveVision,DesktopTools

class LiveVisionTests(unittest.TestCase):
 def wait_for(self,fn):
  end=time.monotonic()+4
  while time.monotonic()<end:
   if fn():return
   time.sleep(.02)
  self.fail('Observation deadline')
 def test_independent_latest_description_updates_and_revocation_clears(self):
  desktop=MagicMock();desktop.watch_status.return_value={'active':True}
  frame={'image':'frame1','at':time.time(),'sequence':1};desktop.watch_frame.side_effect=lambda _:dict(frame)
  runtime=MagicMock();runtime.generate.side_effect=lambda prompt,emit,cancelled,tokens,**kwargs:emit('description '+kwargs['image'])
  vision=LiveVision(desktop,runtime);vision.start('owned','vision.gguf')
  try:
   self.wait_for(lambda:vision.status().get('descriptions')==1)
   self.assertEqual(vision.observation('owned')['description'],'description frame1');self.assertIsNone(vision.observation('other'))
   time.sleep(.35);self.assertEqual(runtime.generate.call_count,1)
   frame.update(image='frame2',sequence=2);self.wait_for(lambda:vision.status()['descriptions']==2)
   self.assertEqual(vision.observation('owned')['description'],'description frame2')
   self.assertNotIn('image',str(vision.status()))
  finally:vision.stop()
  self.assertIsNone(vision.observation('owned'));self.assertFalse(vision.status()['active'])
 def test_cancel_during_generation_never_publishes_late_result(self):
  desktop=MagicMock();desktop.watch_status.return_value={'active':True};desktop.watch_frame.return_value={'image':'private','at':time.time(),'sequence':1}
  runtime=MagicMock();entered=threading.Event()
  def generate(prompt,emit,cancelled,*args,**kwargs):entered.set();cancelled.wait(3);emit('late private text')
  runtime.generate.side_effect=generate;vision=LiveVision(desktop,runtime);vision.start('owned','vision.gguf')
  self.assertTrue(entered.wait(3));vision.stop();self.assertIsNone(vision.observation('owned'));self.assertEqual(vision.status()['descriptions'],0)
 def test_screen_selection_requires_fresh_consent_and_binds_monitor(self):
  desktop=DesktopTools();screen={'id':'display1','title':'Monitor','left':0,'top':0,'width':1920,'height':1080}
  with patch.object(desktop,'screens',return_value={'screens':[screen]}),patch.object(desktop,'call',return_value={'image':'frame'}) as capture:
   with self.assertRaises(ValueError):desktop.select_screen('other')
   selected=desktop.select_screen('display1');capture.assert_not_called()
   with self.assertRaises(ValueError):desktop.watch_start(selected['token'])
   desktop.capture(selected['token'],True);self.assertEqual(capture.call_args.args[0]['operation'],'capture_screen')
   state=desktop.watch_start(selected['token'],True);self.assertEqual(state['scope'],'screen')
   desktop.watch_stop(state['id']);self.assertIsNone(desktop.watch_frame(state['id']))
   desktop.screen_selection['observed_at']-=121
   with self.assertRaises(ValueError):desktop.watch_start(selected['token'],True)
  desktop.close()

if __name__=='__main__':unittest.main()
