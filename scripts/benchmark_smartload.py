"""Opt-in local SmolLM2 baseline. Never downloads or changes installed models."""
import argparse,ctypes,json,os,platform,threading,time,urllib.request
from pathlib import Path
import bootstrap
from rainforge_native.runtime import NativeRuntime

parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,required=True);parser.add_argument('--models',type=Path,required=True);args=parser.parse_args()
runtime=NativeRuntime(args.runtime,args.models)
model='SmolLM2-135M-Instruct-Q4_K_M.gguf';prompt='Explain in one sentence what a computer program is.'
class Counters(ctypes.Structure):
 _fields_=[('cb',ctypes.c_ulong),('faults',ctypes.c_ulong)]+[(n,ctypes.c_size_t) for n in ('peak_working','working','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile','private')]
k=ctypes.WinDLL('kernel32',use_last_error=True);p=ctypes.WinDLL('psapi',use_last_error=True)
k.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong];k.OpenProcess.restype=ctypes.c_void_p
p.GetProcessMemoryInfo.argtypes=[ctypes.c_void_p,ctypes.POINTER(Counters),ctypes.c_ulong];k.CloseHandle.argtypes=[ctypes.c_void_p]
stop=threading.Event();samples=[];first=[];chunks=[]
try:
 start=time.monotonic();runtime.start(model,context=2048,mode='cpu');load=time.monotonic()-start
 def monitor():
  handle=k.OpenProcess(0x1000|0x10,False,runtime.process.pid)
  if not handle:return
  try:
   while not stop.wait(.05):
    v=Counters();v.cb=ctypes.sizeof(v)
    if p.GetProcessMemoryInfo(handle,ctypes.byref(v),v.cb):samples.append((v.working,v.private))
  finally:k.CloseHandle(handle)
 watcher=threading.Thread(target=monitor);watcher.start();begin=time.monotonic()
 def consume(t):
  if not first:first.append(time.monotonic()-begin)
  chunks.append(t)
 runtime.generate(prompt,consume,threading.Event(),max_tokens=64)
 elapsed=time.monotonic()-begin;stop.set();watcher.join();text=''.join(chunks)
 request=urllib.request.Request(f'http://127.0.0.1:{runtime.port}/tokenize',data=json.dumps({'content':text,'add_special':False}).encode(),headers={'Authorization':'Bearer '+runtime.key,'Content-Type':'application/json'})
 with runtime.http.open(request,timeout=10) as response:tokens=len(json.load(response)['tokens'])
 data={'model':model,'model_bytes':runtime.model_path(model).stat().st_size,'backend':'llama.cpp b12032 CPU','platform':platform.platform(),'cpu':platform.processor(),'logical_cpus':os.cpu_count(),'context':2048,'prompt_characters':len(prompt),'output_characters':len(text),'output_tokens_retokenized':tokens,'load_seconds':round(load,3),'first_chunk_seconds':round(first[0],3) if first else None,'generation_seconds':round(elapsed,3),'output_tokens_per_second_including_prefill':round(tokens/elapsed,2),'sampled_peak_working_set_bytes':max((v[0] for v in samples),default=None),'sampled_peak_private_bytes':max((v[1] for v in samples),default=None),'samples':len(samples),'note':'Output tokens retokenized; timing includes prompt processing; sampled peaks are not system-wide. Disk and GPU statistics not collected.'}
 assert text.strip() and tokens>0
 (bootstrap.ROOT/'work/smartload-benchmark.json').write_text(json.dumps(data,indent=2),encoding='utf-8');print(json.dumps(data,ensure_ascii=False))
finally:
 stop.set();runtime.stop()
