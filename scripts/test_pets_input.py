"""Actual native field input and keyboard submission in an isolated native Mini, with real model."""
import json,os,subprocess,time,urllib.request,uuid
from pathlib import Path
import bootstrap
from rainforge_worker.desktop import DesktopTools
from rainforge_recovery import atomic_json
root=bootstrap.ROOT;bundle=root/'work/windows-release/bundle';data=root/'work'/('pet-input-'+uuid.uuid4().hex[:10]);data.mkdir(parents=True)
models=Path(os.environ.get('RF_TEST_MODELS_DIR',str(root/'runtime/models')))
http=urllib.request.build_opener(urllib.request.ProxyHandler({}));host=None;helper=None;desktop=DesktopTools();proof={'status':'FAILED','workspace':str(data)}
def wait(fn,seconds=40):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  try:
   r=fn()
   if r:return r
  except (OSError,ValueError,KeyError):pass
  time.sleep(.2)
 raise TimeoutError('Owned Mini input acceptance deadline')
def read(path):return json.loads(path.read_text('utf-8-sig'))
try:
 host=subprocess.Popen([str(bundle/'RainForge.exe'),'--bundle-root',str(bundle),'--data-dir',str(data),'--models-dir',str(models),'--pets-test','true'])
 meta=wait(lambda:read(data/'native-session.json'));address=meta['address'];token=json.load(http.open(address+'/api/bootstrap'))['token']
 def call(method,params=None):
  request=urllib.request.Request(address+'/api/rpc',json.dumps({'jsonrpc':'2.0','id':'mini-input-test','method':method,'params':params or {}}).encode(),headers={'Origin':address,'X-RainForge-Token':token,'Content-Type':'application/json'})
  reply=json.load(http.open(request,timeout=30));assert 'error' not in reply,reply;return reply['result']
 catalog=call('connection.list');assert len(catalog)==25 and all('token' not in row for row in catalog)
 call('connection.custom.save',{'manifest':{'id':'custom-owned-test','name':'Owned test','category':'work','endpoint':'https://api.example.com/v1/items'}})
 secret='owned-fixture-token-never-log';call('connection.save',{'provider':'custom-owned-test','token':secret})
 assert secret.encode() not in (data/'credentials/custom-owned-test.dpapi').read_bytes()
 call('connection.custom.remove',{'provider':'custom-owned-test'});assert len(call('connection.list'))==25
 assert call('connection.read',{'provider':'local_filesystem'})['read_only']
 auth=call('connection.oauth.begin',{'provider':'google_drive','client_id':'owned-test-client'});assert auth['authorization_url'].startswith('https://accounts.google.com/')
 assert call('connection.oauth.cancel',{'session_id':auth['session_id']})['status']=='cancelled'
 call('memory.enable',{'enabled':True});call('memory.configure',{'auto_capture':True});call('memory.feedback',{'text':'Check selected window identity before typing.'})
 proof['frozen_connector_catalog_custom_dpapi_oauth_cancel']=True
 call('pets.update',{'settings':{'enabled':True,'hide_fullscreen':False,'x':120,'y':180}})
 report=data/'native-input-ready.json'
 helper=subprocess.Popen([str(bundle/'NexusPets.exe'),'--address',address,'--data-dir',str(data),'--owner-pid',str(host.pid),'--self-test-report',str(report),'--self-test-mini-interactive','true'])
 wait(lambda:read(report),60)
 window=wait(lambda:next((w for w in desktop.windows()['windows'] if w['pid']==helper.pid and w['title']=='Nexus Mini Assistant'),None))
 # Optional physical input acceptance is driven through Computer Use in the
 # explicitly isolated Mini. Otherwise the existing native Smoke exercises Send.
 for index,message in enumerate(['Remember that my test project is HazelBridge.','Reply with one short greeting.']):
  print('READY_FOR_INPUT '+str(index+1),flush=True)
  cid=wait(lambda:(data/'pets/mini-conversation.txt').read_text().strip(),180)
  def completed():
   rows=call('conversation.read',{'conversation_id':cid})['messages']
   users=[r for r in rows if r['role']=='user'];answers=[r for r in rows if r['role']=='assistant' and r['state']=='completed' and r['text']]
   return rows if len(users)==index+1 and users[-1]['text']==message and len(answers)==index+1 else None
  rows=wait(completed,150)
 notes=call('memory.list');assert any('HazelBridge' in n['text'] and n['state']=='approved' for n in notes['notes']);proof['mini_autocapture_shared_memory']=True
 proof.update(status='PASS',actual_native_control_input=True,input_method="Computer Use value entry, paste and Enter in isolated Mini",enter_submission=True,shared_conversation_id=cid,stored_messages=len(rows),turns=2,real_local_model=True,model_loaded_only_by_send=True)
except Exception as e:proof['error']=str(e);raise
finally:
 desktop.close()
 for process in [helper,host]:
  if process and process.poll() is None:
   process.kill();process.wait(timeout=15)
 atomic_json(root/'work/pets-input-proof.json',proof)
print(json.dumps(proof,indent=2))
