"""A narrow real 1.5B model-to-browser assertion, not a general-agent benchmark."""
import argparse
import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

import bootstrap
from rainforge_recovery import atomic_json

parser = argparse.ArgumentParser()
parser.add_argument('--models-dir', type=Path, required=True)
args = parser.parse_args()
root = bootstrap.ROOT
data = root / 'work/model-browser-check' / uuid4().hex
data.mkdir(parents=True)
process = subprocess.Popen([str(root/'apps/desktop/src-tauri/binaries/rainforge-worker-x86_64-pc-windows-msvc.exe'),
    '--data-dir',str(data),'--runtime-dir',str(root/'runtime'),'--models-dir',str(args.models_dir)],
    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=(data/'stderr.txt').open('wb'),text=True,encoding='utf-8')
responses = queue.Queue()
def receive():
    for line in process.stdout:
        responses.put(json.loads(line))
threading.Thread(target=receive,daemon=True).start()
def rpc(method,**params):
    key = uuid4().hex
    process.stdin.write(json.dumps({'jsonrpc':'2.0','id':key,'method':method,'params':params})+'\n')
    process.stdin.flush()
    response=responses.get(timeout=110)
    assert response.get('id')==key,response
    if 'error' in response:raise RuntimeError(response['error'])
    return response['result']
try:
    model=next(m for m in rpc('system.snapshot')['models'] if m['downloaded'] and 'coder' in m['filename'].lower() and 1000000000<m['bytes']<1500000000)
    rpc('model.start',filename=model['filename'],mode='auto')
    rpc('browser.navigate',url='https://example.org')
    started=time.monotonic()
    result=rpc('task.execute',goal='Check that the visible page body contains the exact text Learn more.')
    assert result['state']=='checks_passed',result
    proof=next(r for r in result['results'] if r.get('verified') and r.get('condition',{}).get('contains_text')=='Learn more')
    report={'status':'PASS','model':model['filename'],'task':result['goal'],'plan':result['plan'],
            'condition':proof['condition'],'observed_url':proof['observation']['url'],
            'elapsed_seconds':round(time.monotonic()-started,3),'scope':'One real generated browser assertion; not general autonomy'}
    atomic_json(root/'work/model-browser-check.json',report)
    print(json.dumps(report))
finally:
    process.stdin.close()
    try:process.wait(timeout=12)
    except subprocess.TimeoutExpired:subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
    process.stdout.close()
