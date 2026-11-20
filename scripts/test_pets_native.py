"""Owned-workspace Windows host/Pets lifecycle, import and resource acceptance.

Uses an isolated profile, a bundled model, and optional synthetic audio.
It does not access the user's desktop capture, microphone, or startup settings.
Run after building the worker, frontend and native bundle.
"""
import ast
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import shutil
import subprocess
import time
import urllib.request
import uuid
import zlib
import wave
import base64
import bootstrap

root=bootstrap.ROOT;bundle=root/'work/windows-release/bundle'
mini_executable=Path(os.environ.get('RF_PETS_TEST_MINI_EXE',str(bundle/'NexusPets.exe')))
data=root/'work'/('pets-native-'+uuid.uuid4().hex[:10]);data.mkdir(parents=True)
http=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def wait(predicate,timeout=30):
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  try:
   result=predicate()
   if result:return result
  except (OSError,ValueError,KeyError):pass
  time.sleep(.15)
 raise TimeoutError('Owned Pets acceptance condition did not become true')

kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32];kernel.OpenProcess.restype=ctypes.c_void_p
kernel.CloseHandle.argtypes=[ctypes.c_void_p];kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
kernel.GetProcessTimes.argtypes=[ctypes.c_void_p,*([ctypes.POINTER(ctypes.c_uint64)]*4)]
class Counters(ctypes.Structure):
 _fields_=[('cb',ctypes.c_uint32),('faults',ctypes.c_uint32),*[(n,ctypes.c_size_t) for n in ('peak_working','working','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile')]]
psapi=ctypes.WinDLL('psapi');psapi.GetProcessMemoryInfo.argtypes=[ctypes.c_void_p,ctypes.POINTER(Counters),ctypes.c_uint32]
def alive(pid):
 h=kernel.OpenProcess(0x1000,False,pid)
 if not h:return False
 try:
  code=ctypes.c_uint32();kernel.GetExitCodeProcess(h,ctypes.byref(code));return code.value==259
 finally:kernel.CloseHandle(h)
def sample(pid):
 h=kernel.OpenProcess(0x410,False,pid);assert h,'Owned pet unavailable'
 try:
  values=[ctypes.c_uint64() for _ in range(4)];assert kernel.GetProcessTimes(h,*[ctypes.byref(v) for v in values])
  mem=Counters();mem.cb=ctypes.sizeof(mem);assert psapi.GetProcessMemoryInfo(h,ctypes.byref(mem),mem.cb)
  return {'cpu_seconds':(values[2].value+values[3].value)/10_000_000,'working_set_bytes':mem.working,'at':time.monotonic()}
 finally:kernel.CloseHandle(h)

user=ctypes.WinDLL('user32');enum_type=ctypes.WINFUNCTYPE(ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p)
user.EnumWindows.argtypes=[enum_type,ctypes.c_void_p];user.GetWindowThreadProcessId.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
user.GetWindowLongW.argtypes=[ctypes.c_void_p,ctypes.c_int];user.GetWindowLongW.restype=ctypes.c_long
user.IsWindowVisible.argtypes=[ctypes.c_void_p]
user.GetWindowTextW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_int]
def windows(pid):
 found=[]
 @enum_type
 def collect(hwnd,_):
  owner=ctypes.c_uint32();user.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
  title=ctypes.create_unicode_buffer(160);user.GetWindowTextW(hwnd,title,160)
  if owner.value==pid and user.IsWindowVisible(hwnd) and title.value.startswith('Nexus Pet '):found.append(hwnd)
  return 1
 user.EnumWindows(collect,None);return found

p=subprocess.Popen([str(bundle/'RainForge.exe'),'--bundle-root',str(bundle),'--data-dir',str(data),'--models-dir',str(data/'models'),'--pets-test','true'])
proof={'test_workspace':str(data),'status':'FAILED'};mini_test=None;staged_model=None
try:
 meta=wait(lambda:read(data/'native-session.json'));address=meta['address'];token=json.load(http.open(address+'/api/bootstrap'))['token']
 def call(method,params=None):
  req=urllib.request.Request(address+'/api/rpc',json.dumps({'jsonrpc':'2.0','id':'pets-native','method':method,'params':params or {}}).encode(),headers={'Origin':address,'X-RainForge-Token':token,'Content-Type':'application/json'})
  result=json.load(http.open(req,timeout=30));assert 'error' not in result,result;return result['result']
 initial=call('pets.get');assert initial['settings']['enabled'] is False;assert initial['settings']['start_with_windows'] is False
 call('pets.update',{'settings':{'enabled':True,'character':'nexus-bot','hide_fullscreen':False,'x':100,'y':100}})
 host=wait(lambda:read(data/'pets/native-host.json'));pet_pid=host['pet_pid'];assert pet_pid>0
 hwnd=wait(lambda:next((w for w in windows(pet_pid) if user.GetWindowLongW(w,-20)&0x80000),None))
 assert user.GetWindowLongW(hwnd,-20)&0x8,'Always-on-top style missing'
 assert not user.GetWindowLongW(hwnd,-16)&0x00C00000,'Pet has a caption frame'
 first=sample(pet_pid);time.sleep(4);second=sample(pet_pid)
 proof['idle_resources']={'working_set_bytes':second['working_set_bytes'],'cpu_percent_one_logical_core':round((second['cpu_seconds']-first['cpu_seconds'])/(second['at']-first['at'])*100,3),'sample_seconds':round(second['at']-first['at'],2),'gpu':'NOT MEASURED: native GPU counters not collected'}
 snapshot=call('system.snapshot')
 ready_models=[item for item in snapshot['models'] if item['downloaded']]
 assert snapshot['runtime']['state']=='stopped' and [item['id'] for item in ready_models]==['smollm2-135m-smoke']
 assert not list((data/'models').glob('*.gguf'))
 started=call('chat.begin',{'prompt':'Say hello in one short sentence.','max_tokens':24})
 response=wait(lambda:(lambda chat:chat if chat['state']!='running' else None)(call('chat.poll',{'chat_id':started['chat_id']})),60)
 assert response['state']=='completed' and response['text'].strip(),response
 call('model.stop')
 proof['first_chat_from_empty_models_directory']=True

 voice_wav=os.environ.get('RF_PETS_TEST_VOICE_WAV')
 if voice_wav:
  voice_report=data/'voice-native-proof.json'
  mini_test=subprocess.Popen([str(mini_executable),'--address',address,'--data-dir',str(data),'--owner-pid',str(p.pid),'--self-test-report',str(voice_report),'--self-test-voice-wav',voice_wav,'--self-test-voice-language','ru'])
  voice=wait(lambda:read(voice_report),100)
  assert voice['status']=='PASS' and voice['voice']['passed'] and not voice['voice']['sent'],voice
  assert 'распознавание речи' in voice['voice']['draft'].lower(),voice
  assert call('system.snapshot')['runtime']['state']=='stopped'
  proof['native_voice_draft']=voice['voice']
  mini_test.terminate();mini_test.wait(timeout=10);mini_test=None
 voice_command_wav=os.environ.get('RF_PETS_TEST_VOICE_COMMAND_WAV')
 if voice_command_wav:
  voice_reply_report=data/'voice-reply-native-proof.json'
  mini_test=subprocess.Popen([str(mini_executable),'--address',address,'--data-dir',str(data),'--owner-pid',str(p.pid),'--self-test-report',str(voice_reply_report),'--self-test-voice-wav',voice_command_wav,'--self-test-voice-language','ru','--self-test-voice-submit','true'])
  voice_reply=wait(lambda:read(voice_reply_report),120)
  assert voice_reply['status']=='PASS',voice_reply
  reply=voice_reply['voice']
  assert reply['direct'] and reply['speech_endpoint'] and reply['voice']=='dmitri' and not reply['microphone_rearmed'] and reply['draft']=='' and reply['sent'] and reply['route']=='chat' and reply['response_state']=='answered' and reply['speech_wave_bytes']>44,reply
  stored=call('conversation.read',{'conversation_id':reply['conversation_id']})
  expected='Я Nexus — ИИ-ассистент. Могу отвечать на вопросы, помогать с программированием и выполнять задачи с помощью доступных инструментов'
  assert stored['messages'][-1]['text'].rstrip('.')==expected,stored
  matching=[read(path) for path in (data/'actions').glob('*.json')]
  routed=next(run for run in matching if run.get('conversation_id')==reply['conversation_id'])
  assert routed['planner_calls']==routed['tool_calls']==0,routed
  assert call('system.snapshot')['runtime']['state']=='stopped','Identity reply should not load a model'
  proof['native_voice_reply']=reply
  mini_test.terminate();mini_test.wait(timeout=10);mini_test=None
 coder=os.environ.get('RF_PETS_TEST_CODER')
 if coder:
  # Verify actual recognition -> voice task route -> model planning and source
  # generation -> verified workspace file -> offline synthesis. No code runs.
  source=Path(coder).resolve(strict=True)
  assert source.name=='qwen2.5-coder-1.5b-instruct-q4_k_m.gguf',source.name
  (data/'models').mkdir(exist_ok=True)
  target=data/'models'/source.name
  # A running Nexus may hold the installed model open. Use an independent copy for cleanup.
  shutil.copyfile(source,target)
  staged_model=target
  call('pets.update',{'settings':{'voice':True}})
  fixed_fixture=os.environ.get('RF_PETS_TEST_ACTION_WAV')
  if fixed_fixture:
   fixture=Path(fixed_fixture).resolve(strict=True)
  else:
   command=call('voice.synthesize',{'text':'Создай скрипт на питоне. Напиши функцию, которая складывает два числа.'})
   # The fixture uses the same 16 kHz mono PCM format as microphone capture.
   with wave.open(io.BytesIO(base64.b64decode(command['wav'])),'rb') as native_wave:
    assert native_wave.getsampwidth()==2 and native_wave.getnchannels()==1
    frames=struct.unpack('<'+'h'*native_wave.getnframes(),native_wave.readframes(native_wave.getnframes()))
    rate=native_wave.getframerate()
   converted=bytearray()
   for index in range(len(frames)*16000//rate):
    position=index*rate/16000
    left=int(position)
    sample=frames[left]+(frames[min(left+1,len(frames)-1)]-frames[left])*(position-left)
    converted.extend(struct.pack('<h',round(sample)))
   fixture=data/'voice-create-fixture.wav'
   with wave.open(str(fixture),'wb') as out:
    out.setparams((1,2,16000,0,'NONE',''))
    out.writeframes(converted)
  before=set((data/'actions').glob('*.json'))
  voice_action_report=data/'voice-action-native-proof.json'
  mini_test=subprocess.Popen([str(mini_executable),'--address',address,'--data-dir',str(data),'--owner-pid',str(p.pid),'--self-test-report',str(voice_action_report),'--self-test-voice-wav',str(fixture),'--self-test-voice-submit','true'])
  voice_action=wait(lambda:read(voice_action_report),150)
  assert voice_action['status']=='PASS',voice_action
  reply=voice_action['voice']
  assert reply['route']=='task' and reply['response_state']=='file_created' and not reply['microphone_rearmed'],reply
  new_runs=set((data/'actions').glob('*.json'))-before
  assert len(new_runs)==1,new_runs
  run=read(next(iter(new_runs)))
  assert run['conversation_id']==reply['conversation_id'] and run['tool_calls']==1 and run['plan'][0]['tool']=='create_python',run
  verified=run['results'][0]
  created=(data/'coding-workspace'/verified['path']).resolve()
  assert created.is_relative_to(data.resolve()/'coding-workspace')
  raw=created.read_bytes()
  assert hashlib.sha256(raw).hexdigest()==verified['sha256']
  tree=ast.parse(raw.decode('utf-8'),filename=created.name)
  compile(tree,created.name,'exec')
  assert any(isinstance(node,ast.FunctionDef) for node in ast.walk(tree))
  assert any(isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add) for node in ast.walk(tree))
  proof['native_voice_create']={'result':reply,'source':verified['path'],'sha256':verified['sha256'],'recognized':run['goal'],'planner_calls':run['planner_calls'],'tool_calls':run['tool_calls'],'model':call('system.snapshot')['runtime']['model']}
  mini_test.terminate();mini_test.wait(timeout=10);mini_test=None
  call('model.stop')
 # A separate test-only companion uses the SAME real native Mini Send method,
 # same worker/runtime, model selector and persisted conversation APIs.
 if os.environ.get('RF_PETS_SKIP_MINI')=='1':proof['native_mini']='NOT TESTED: isolated lifecycle-only debug invocation'
 else:
  mini_report=data/'mini-native-proof.json'
  mini_test=subprocess.Popen([str(mini_executable),'--address',address,'--data-dir',str(data),'--owner-pid',str(p.pid),'--self-test-report',str(mini_report),'--self-test-mini-prompt','Reply with one short greeting.'])
  mini=wait(lambda:read(mini_report),120);assert mini['status']=='PASS',mini
  assert mini['mini']['passed'] and mini['mini']['shared_worker'] and mini['mini']['stored_messages']>=2,mini
  stored=call('conversation.read',{'conversation_id':mini['mini']['conversation_id']});assert any(m['role']=='assistant' and m['state']=='completed' and m['text'] for m in stored['messages'])
  proof['native_mini']=mini;mini_test.terminate();mini_test.wait(timeout=10);mini_test=None
 # Test a real PNG package through the same import and live selection path.
 def chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff)
 pixels=b''.join(b'\0'+bytes([90,220,170,255])*16 for _ in range(16))
 image=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',16,16,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')
 manifest={'name':'Owned test pet','author':'Nexus test fixture','license':'CC0-1.0','frame_width':16,'frame_height':16,'states':{'idle':{'file':'owned.png','frames':[0],'fps':15}}}
 package=call('pets.import',{'manifest':manifest,'files':{'owned.png':base64.b64encode(image).decode()}})
 package_id=package.get('id') or package.get('package_id') or package['package']['id']
 call('pets.update',{'settings':{'character':'imported:'+package_id,'size':96,'always_on_top':False,'x':120,'y':140}})
 wait(lambda:not user.GetWindowLongW(hwnd,-20)&0x8)
 saved=call('pets.get')['settings'];assert saved['character']=='imported:'+package_id and saved['x']==120 and saved['y']==140
 call('pets.update',{'settings':{'enabled':False}});wait(lambda:not alive(pet_pid));assert alive(p.pid) and alive(meta['worker_pid'])
 proof.update(transparent_layered=True,frameless=True,topmost_toggle=True,import_and_select=True,settings_saved=True,pet_close_keeps_nexus=True)
 call('pets.update',{'settings':{'enabled':True,'character':'pixel-cat'}})
 second_pid=wait(lambda:(lambda h:h['pet_pid'] if h['pet_pid']>0 and h['pet_pid']!=pet_pid else None)(read(data/'pets/native-host.json')))
 wait(lambda:bool(windows(second_pid)))
 p.kill();p.wait(timeout=10);wait(lambda:not alive(second_pid) and not alive(meta['worker_pid']))
 p=subprocess.Popen([str(bundle/'RainForge.exe'),'--bundle-root',str(bundle),'--data-dir',str(data),'--models-dir',str(data/'models'),'--pets-test','true'])
 meta=wait(lambda:(lambda m:m if m['host_pid']==p.pid else None)(read(data/'native-session.json')))
 address=meta['address'];token=json.load(http.open(address+'/api/bootstrap'))['token']
 restored=call('pets.get')['settings'];assert restored['enabled'] and restored['character']=='pixel-cat' and restored['size']==96
 restored_pid=wait(lambda:(lambda h:h['pet_pid'] if h['owner_pid']==p.pid and h['pet_pid']>0 else None)(read(data/'pets/native-host.json')))
 wait(lambda:bool(windows(restored_pid)))
 p.kill();p.wait(timeout=10);wait(lambda:not alive(restored_pid) and not alive(meta['worker_pid']))
 proof['restart_restores_preferences_and_pet']=True
 proof.update(status='PASS',host_crash_cleanup=True,worker_sha256=hashlib.sha256((bundle/'rainforge-worker.exe').read_bytes()).hexdigest(),pet_sha256=hashlib.sha256(mini_executable.read_bytes()).hexdigest(),multiple_monitors_and_dpi='NOT TESTED on multiple physical monitors/scales; geometry code/unit paths only',drag='NOT TESTED through pointer here; native selftest/CUA separately')
finally:
 if mini_test is not None and mini_test.poll() is None:mini_test.terminate();mini_test.wait(timeout=10)
 if p.poll() is None:p.kill();p.wait(timeout=10)
 if staged_model is not None:staged_model.unlink(missing_ok=True)
 (root/'work/pets-native-proof.json').write_text(json.dumps(proof,indent=2),encoding='utf-8');print(json.dumps(proof,indent=2))
