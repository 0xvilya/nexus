import base64,copy,json,struct,sys,tempfile,threading,time,unittest,zlib
from pathlib import Path
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_worker.pets import Pets,DEFAULTS,image_info
from rainforge_worker.rpc import Worker
from rainforge_worker.actions import ActionEngine
from rainforge_worker.workspace import WorkspaceTools

def png(width=2,height=1):
 def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\x70\x90\xff\xff'*width)*height))+chunk(b'IEND',b'')

def manifest():return {'name':'Original test cat','author':'Fixture author','license':'CC0-1.0','frame_width':1,'frame_height':1,'states':{'idle':{'file':'cat.png','frames':[0,1],'fps':8}}}

class PetsTests(unittest.TestCase):
 def test_preferences_persist_and_reject_invalid_values(self):
  with tempfile.TemporaryDirectory() as tmp:
   pets=Pets(Path(tmp));self.assertEqual(pets.get()['settings'],DEFAULTS)
   for settings in ({'enabled':1},{'size':0},{'fps':999},{'speed':float('nan')},{'opacity':float('inf')},{'x':1.2},{'character':'missing'},{'secret':'APIKEY'}):
    with self.assertRaises(ValueError):pets.update(settings)
   pets.update({'enabled':True,'character':'pixel-cat','x':-1024,'y':300,'size':192,'start_with_windows':False})
   restarted=Pets(Path(tmp));self.assertTrue(restarted.settings['enabled']);self.assertEqual(restarted.settings['x'],-1024)
   restarted.update({'x':None,'y':None});self.assertIsNone(Pets(Path(tmp)).settings['x'])
   (Path(tmp)/'pets/settings.json').write_text('{broken',encoding='utf-8')
   recovered=Pets(Path(tmp));self.assertFalse(recovered.settings['enabled']);self.assertTrue(recovered.get()['settings_warning'])
   self.assertEqual((Path(tmp)/'pets/settings.json').read_text(),'{broken')
 def test_event_subscription_is_bounded_observable_and_has_no_prompts(self):
  with tempfile.TemporaryDirectory() as tmp:
   pets=Pets(Path(tmp));result=[];started=threading.Event()
   def waiting():started.set();result.append(pets.wait(0,2))
   thread=threading.Thread(target=waiting);thread.start();started.wait(1);pets.track('action',{'id':'a'*32,'state':'planning','goal':'Bearer very-private-token','error':'password=secret'})
   thread.join(2);self.assertEqual(result[0]['events'][0]['type'],'agent.thinking')
   self.assertNotIn('private',json.dumps(result));self.assertNotIn('password',json.dumps(result))
   sequence=pets.sequence;pets.track('action',{'id':'a'*32,'state':'planning'})
   self.assertEqual(pets.sequence,sequence);self.assertEqual(pets.wait(sequence,0)['events'],[])
   for i in range(150):pets.track('action',{'id':str(i),'state':'failed'})
   snapshot=pets.get();self.assertEqual(len(snapshot['events']),128);self.assertEqual(len(snapshot['activity']),64)
   snapshot['activity'][0]['title']='mutated';self.assertNotEqual(pets.get()['activity'][0]['title'],'mutated')
   for state in ('answered','observed','launch_requested','partial'):
    pets.track('action',{'id':'neutral','state':state});self.assertEqual(pets.get()['events'][-1]['type'],'agent.stopped')
   pets.track('action',{'id':'permission','state':'awaiting_permission'});self.assertEqual(pets.get()['events'][-1]['type'],'agent.waiting_approval')
   pets.track('action',{'id':'proof','state':'checks_passed'});self.assertEqual(pets.get()['events'][-1]['type'],'agent.completed')
   pets.host_request('settings');self.assertEqual(pets.get()['events'][-1]['action'],'settings')
   with self.assertRaises(ValueError):pets.host_request('run_shell')
   for kwargs in ({'after':True},{'after':-1},{'timeout':21},{'timeout':float('nan')}):
    with self.assertRaises(ValueError):pets.wait(**kwargs)
   pets.close();self.assertEqual(pets.wait(pets.sequence,20)['events'],[])
 def test_import_sprite_frames_asset_and_remove_preserve_builtin(self):
  with tempfile.TemporaryDirectory() as tmp:
   pets=Pets(Path(tmp));data=png();item=pets.import_package(manifest(),{'cat.png':base64.b64encode(data).decode()})
   self.assertEqual(item['manifest']['states']['idle']['frames'],[0,1]);self.assertEqual(base64.b64decode(pets.asset(item['id'],'cat.png')['data']),data)
   pets.update({'character':'imported:'+item['id']});self.assertEqual(Pets(Path(tmp)).settings['character'],'imported:'+item['id'])
   pets.remove(item['id']);self.assertEqual(pets.settings['character'],'nexus-bot');self.assertEqual(pets.packages(),[])
   for key,path in (('../escape','cat.png'),('a'*32,'../secret')):
    with self.assertRaises(ValueError):pets.asset(key,path)
 def test_media_structure_and_executable_package_rejections(self):
  with tempfile.TemporaryDirectory() as tmp:
   pets=Pets(Path(tmp));data=png();encoded=base64.b64encode(data).decode()
   for bad in (data[:20],data+b'trailer',b'<svg onload="run()"/>',b'MZ'+data, data[:29]+bytes([data[29]^1])+data[30:]):
    with self.assertRaises(ValueError):image_info(bad)
   self.assertEqual(image_info(data)['width'],2)
   gif=base64.b64decode('R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==');self.assertEqual(image_info(gif)['format'],'gif')
   for change in ({'script':'evil.py'},{'frame_width':0},{'states':{'idle':{'file':'cat.png','frames':[2],'fps':8}}},{'states':{'idle':{'file':'../cat.png','frames':[0],'fps':8}}},{'states':{'idle':{'file':'cat.png','frames':[0],'fps':1000}}}):
    spec={**manifest(),**change}
    with self.assertRaises(ValueError):pets.import_package(spec,{'cat.png':encoded})
   for files in ({'../cat.png':encoded},{'cat.gif':encoded},{'cat.png':'not base64'},{'cat.png':encoded,'CAT.PNG':encoded},{'CON.png':encoded},{'LPT1.png':encoded}):
    with self.assertRaises(ValueError):pets.import_package(manifest(),files)
   self.assertEqual(pets.packages(),[])
 def test_chunked_import_offset_limits_cancel_and_finish(self):
  with tempfile.TemporaryDirectory() as tmp:
   pets=Pets(Path(tmp));data=png();request={'manifest':manifest(),'files':[{'name':'cat.png','size':len(data)}]};key=pets.upload_begin(**request)['upload_id']
   with self.assertRaises(ValueError):pets.upload_finish(key)
   with self.assertRaises(ValueError):pets.upload_chunk(key,'cat.png',1,base64.b64encode(data).decode())
   self.assertEqual(pets.upload_chunk(key,'cat.png',0,base64.b64encode(data[:30]).decode())['offset'],30)
   self.assertEqual(pets.upload_chunk(key,'cat.png',30,base64.b64encode(data[30:]).decode())['offset'],len(data))
   item=pets.upload_finish(key);self.assertEqual(item['name'],manifest()['name'])
   with self.assertRaises(ValueError):pets.upload_finish(key)
   key=pets.upload_begin(**request)['upload_id'];self.assertTrue(pets.upload_cancel(key)['cancelled']);self.assertIsNone(pets.upload)
   with self.assertRaises(ValueError):pets.upload_begin(manifest(),[{'name':'cat.png','size':9*1024*1024}])
 def test_decoded_animation_metadata_budgets_and_corrupt_library_are_bounded(self):
  def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
  with tempfile.TemporaryDirectory() as tmp:
   pets=Pets(Path(tmp));data=png()
   # A valid tiny compressed stream may not be used as an enormous animation.
   header=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',4096,1024,8,6,0,0,0))
   with self.assertRaisesRegex(ValueError,'decoded animation'):image_info(header+chunk(b'acTL',struct.pack('>II',128,0))+chunk(b'IEND',b''))
   for kind in (b'iCCP',b'zTXt',b'iTXt',b'eXIf'):
    with self.assertRaisesRegex(ValueError,'metadata'):image_info(data[:-12]+chunk(kind,b'unneeded')+data[-12:])
   with self.assertRaisesRegex(ValueError,'scanlines'):
    image_info(data[:33]+chunk(b'IDAT',zlib.compress(b''))+chunk(b'IEND',b''))
   item=pets.import_package(manifest(),{'cat.png':base64.b64encode(data).decode()})
   (pets.packages_root/item['id']/'manifest.json').write_text('{broken',encoding='utf-8')
   snapshot=pets.get();self.assertEqual(snapshot['packages'],[]);self.assertTrue(snapshot['package_warnings'])
   self.assertIn('settings',snapshot);pets.update({'size':96})
 def test_actual_worker_chat_model_action_and_agent_hooks(self):
  with tempfile.TemporaryDirectory() as tmp:
   worker=Worker(Path(tmp),bootstrap.ROOT/'runtime')
   try:
    self.assertIn('settings',worker.dispatch('pets.get',{}))
    with self.assertRaises(ValueError):worker.dispatch('pets.track',{'event':'agent.completed'})
    with patch.object(worker.runtime,'start',return_value={'state':'ready'}):worker.start_model('fixture.gguf')
    self.assertEqual([e['type'] for e in worker.pets.get()['events'][-2:]],['model.loading','model.ready'])
    with patch.object(worker.runtime,'status',return_value={'state':'ready'}),patch.object(worker.runtime,'generate',side_effect=lambda prompt,token,*args,**kwargs:token('Observed reply')):
     turn=worker.begin_user_chat('Fixture question',max_tokens=20)
     for _ in range(100):
      if worker.poll_chat(turn['chat_id'])['state']!='running':break
      time.sleep(.01)
    self.assertEqual(worker.poll_chat(turn['chat_id'])['state'],'completed')
    worker.actions.save({'id':'b'*32,'state':'awaiting_permission','goal':'do not retain this'})
    self.assertEqual(worker.pets.get()['events'][-1]['type'],'agent.waiting_approval')
    worker.agents.put({'id':'c'*32,'state':'running','steps':[]})
    self.assertEqual(worker.pets.get()['events'][-1]['kind'],'agent')
    worker.browser_agent.save({'id':'d'*32,'state':'planning'})
    self.assertEqual(worker.pets.get()['events'][-1]['kind'],'browser')
    self.assertNotIn('Fixture question',json.dumps(worker.pets.get()))
   finally:worker.close()

if __name__=='__main__':unittest.main()
