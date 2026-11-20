"""Crash/restart and local-inference acceptance for the native Windows host."""
import ctypes,json,subprocess,time,urllib.request,uuid
from pathlib import Path
import bootstrap
root=bootstrap.ROOT;bundle=root/'work/windows-release/bundle';work=root/'work/native-recovery';work.mkdir(parents=True,exist_ok=True)
data=work/('data-'+uuid.uuid4().hex);report=work/('smoke-'+uuid.uuid4().hex+'.json');session=data/'native-session.json'
http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def call(meta,method,params={}):
 token=json.load(http.open(meta['address']+'/api/bootstrap'))['token']
 req=urllib.request.Request(meta['address']+'/api/rpc',data=json.dumps({'jsonrpc':'2.0','id':'native-recovery','method':method,'params':params}).encode(),headers={'Origin':meta['address'],'X-RainForge-Token':token,'Content-Type':'application/json'})
 response=json.load(http.open(req,timeout=30));assert 'error' not in response,response;return response['result']
api=ctypes.WinDLL('kernel32');api.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32];api.OpenProcess.restype=ctypes.c_void_p;api.CloseHandle.argtypes=[ctypes.c_void_p];api.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
def alive(pid):
 h=api.OpenProcess(0x1000,False,pid)
 if not h:return False
 try:code=ctypes.c_uint32();api.GetExitCodeProcess(h,ctypes.byref(code));return code.value==259
 finally:api.CloseHandle(h)
p=subprocess.Popen([str(bundle/'RainForge.exe'),'--bundle-root',str(bundle),'--data-dir',str(data),'--models-dir',str(root/'runtime/models'),'--smoke-report',str(report)])
try:
 for _ in range(600):
  if report.exists():break
  if p.poll() is not None:raise RuntimeError('Native app exited')
  time.sleep(.1)
 proof=read(report);assert proof['status']=='PASS',proof;old=read(session);assert old['host_pid']==p.pid
 subprocess.run(['taskkill','/PID',str(old['worker_pid']),'/T','/F'],check=True,stdout=subprocess.DEVNULL)
 for _ in range(300):
  time.sleep(.1);meta=read(session)
  if meta['worker_pid']!=old['worker_pid']:break
 assert meta['worker_pid']!=old['worker_pid'] and alive(p.pid),'Worker did not recover'
 snapshot=call(meta,'system.snapshot');assert any(w['goal']=='/note '+proof['goal'] or proof['goal'] in w['goal'] for w in snapshot['workflows'])
 call(meta,'model.start',{'filename':'SmolLM2-135M-Instruct-Q4_K_M.gguf','mode':'cpu'})
 key=call(meta,'chat.begin',{'prompt':'Say hello in one short sentence.','max_tokens':24})['chat_id']
 for _ in range(200):
  answer=call(meta,'chat.poll',{'chat_id':key})
  if answer['state']!='running':break
  time.sleep(.1)
 assert answer['state']=='completed' and answer['text'].strip(),answer
 raw=subprocess.check_output(['powershell.exe','-NoProfile','-Command',"Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress"])
 servers=json.loads(raw.decode('utf-8-sig'));servers=servers if isinstance(servers,list) else [servers]
 expected=str(bundle/'runtime/bin/llama-server.exe').lower();server=next(s['ProcessId'] for s in servers if s['ExecutablePath'].lower()==expected)
 p.kill();p.wait(timeout=10)
 for _ in range(100):
  if not alive(meta['worker_pid']) and not alive(server):break
  time.sleep(.1)
 assert not alive(meta['worker_pid']) and not alive(server),'Owned process leaked after host crash'
 result={'status':'PASS','worker_restart':True,'notes_preserved':True,'native_local_inference':True,'generation_characters':len(answer['text']),'host_crash_kills_worker_and_model':True}
 (work/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
finally:
 if p.poll() is None:p.kill();p.wait(timeout=10)
