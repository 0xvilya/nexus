"""Exercise actual frozen-worker RPC without Python/Node on its PATH."""
import json
import os
import queue
import subprocess
import threading
import time
from uuid import uuid4

import bootstrap
from rainforge_recovery import atomic_json

root = bootstrap.ROOT
data = root / 'work/packaged-datasets' / uuid4().hex
data.mkdir(parents=True)
env = {**os.environ, 'PATH': os.environ['SystemRoot'] + '\\System32;' + os.environ['SystemRoot']}
process = subprocess.Popen([str(root / 'apps/desktop/src-tauri/binaries/rainforge-worker-x86_64-pc-windows-msvc.exe'),
    '--data-dir', str(data), '--runtime-dir', str(root / 'runtime')], stdin=subprocess.PIPE,
    stdout=subprocess.PIPE, stderr=(data / 'stderr.txt').open('wb'), text=True, encoding='utf-8', env=env)
responses = queue.Queue()
def receive():
    for line in process.stdout:
        responses.put(json.loads(line))
threading.Thread(target=receive, daemon=True).start()
def rpc(method, **params):
    key = uuid4().hex
    process.stdin.write(json.dumps({'jsonrpc':'2.0','id':key,'method':method,'params':params}) + '\n')
    process.stdin.flush()
    result = responses.get(timeout=40)
    assert result.get('id') == key, result
    if result.get('error'):
        raise RuntimeError(result['error'])
    return result['result']
try:
    info = rpc('dataset.hf.describe', repo='scikit-learn/iris')
    rpc('dataset.hf.start', repo=info['repo'], revision=info['revision'], filename='Iris.csv', accept_license=True)
    deadline = time.monotonic() + 80
    while True:
        downloaded = rpc('dataset.hf.status')
        if downloaded['status'] not in ('RESOLVING','DOWNLOADING','VERIFYING'):
            break
        assert time.monotonic() < deadline, 'Download timed out'
        time.sleep(.2)
    assert downloaded['status'] == 'COMPLETED', downloaded
    key = rpc('dataset.start', source=downloaded['source'], license=downloaded['license'], provenance=downloaded['provenance'])['id']
    while True:
        run = next(r for r in rpc('dataset.list')['runs'] if r['id'] == key)
        if run['status'] not in ('RUNNING','VERIFYING'):
            break
        assert time.monotonic() < deadline, 'Pipeline timed out'
        time.sleep(.2)
    assert run['status'] == 'COMPLETED' and run['stats']['kept'] == 150, run
    exported = rpc('dataset.read', run_id=key, name='train.jsonl')
    assert exported['verified'] and len(exported['text']) > 100
    atomic_json(root / 'work/packaged-dataset-results.json', {'status':'PASS', 'packaged_worker':True,
                'python_node_on_path':False,'revision':info['revision'],'source_sha256':downloaded['sha256'],
                'stats':run['stats'],'files':run['files']})
    print('PASS: frozen worker pinned HF download, pipeline, artifact read; 150 records')
finally:
    process.stdin.close()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
    process.stdout.close()
