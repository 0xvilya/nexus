"""Frozen-worker iterative model workflow; CDP only provisions the owned test fixture."""
import argparse
import json
import os
import queue
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

import websocket
import bootstrap
from rainforge_recovery import atomic_json

parser = argparse.ArgumentParser()
parser.add_argument('--models-dir', type=Path, required=True)
args = parser.parse_args()
root = bootstrap.ROOT
data = root / 'work/browser-agent-packaged' / uuid4().hex
data.mkdir(parents=True)
env = {**os.environ, 'PATH': os.environ['SystemRoot'] + '\\System32;' + os.environ['SystemRoot']}
child = subprocess.Popen([str(root/'apps/desktop/src-tauri/binaries/rainforge-worker-x86_64-pc-windows-msvc.exe'),
    '--data-dir',str(data),'--runtime-dir',str(root/'runtime'),'--models-dir',str(args.models_dir)],
    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=(data/'stderr.txt').open('wb'),text=True,encoding='utf-8',env=env)
replies = queue.Queue()
def receive():
    for line in child.stdout:
        replies.put(json.loads(line))
threading.Thread(target=receive,daemon=True).start()
def rpc(method, expect_error=False, **params):
    key=uuid4().hex
    child.stdin.write(json.dumps({'jsonrpc':'2.0','id':key,'method':method,'params':params})+'\n')
    child.stdin.flush()
    result=replies.get(timeout=80)
    assert result.get('id')==key,result
    if expect_error:
        assert 'error' in result,result
        return result['error']
    if 'error' in result:raise RuntimeError(result['error'])
    return result['result']
try:
    model=next(m for m in rpc('system.snapshot')['models'] if m['downloaded'] and 'coder' in m['filename'].lower() and 1_000_000_000<m['bytes']<1_500_000_000)
    rpc('model.start',filename=model['filename'],mode='auto',context=4096)
    rpc('browser.navigate',url='https://example.org')
    port=int(next(data.glob('browser-profile-*/DevToolsActivePort')).read_text().splitlines()[0])
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list',timeout=3) as response:
        target=next(item for item in json.load(response) if item.get('type')=='page')
    ws=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=10,suppress_origin=True,http_proxy_host=None)
    try:
        def cdp(key,method,params=None):
            ws.send(json.dumps({'id':key,'method':method,'params':params or {}}))
            for _ in range(100):
                result=json.loads(ws.recv())
                if result.get('id')==key:
                    assert 'error' not in result,result
                    return result.get('result',{})
            raise RuntimeError('Fixture CDP reply missing')
        frame=cdp(1,'Page.getFrameTree')['frameTree']['frame']['id']
        cdp(2,'Page.setDocumentContent',{'frameId':frame,'html':(root/'tests/fixtures/browser-agent.html').read_text(encoding='utf-8')})
    finally:
        ws.close()
    key=rpc('browser.agent.start',goal='Type PackageTest in Name, click Continue, then click Confirm.',
            expected_text='Hello PackageTest',selector='#result',allow_mutation=True,max_steps=8)['id']
    assert 'reserved' in rpc('browser.inspect',expect_error=True)['message']
    deadline=time.monotonic()+190
    while True:
        status=rpc('browser.agent.status',run_id=key)
        if not status['active']:
            break
        assert time.monotonic()<deadline,'Agent timed out'
        time.sleep(.3)
    run=status['run']
    report={'status':'PASS' if run['state']=='checks_passed' else 'FAIL','packaged_worker':True,
            'python_node_on_worker_path':False,'real_model':model['filename'],'fixture_provisioned_locally':True,
            'exclusive_browser_reservation_verified':True,'run':run}
    atomic_json(root/'work/browser-agent-packaged-results.json',report)
    print(json.dumps(report,ensure_ascii=True))
    assert run['state']=='checks_passed',run['error']
    rpc('browser.close')
finally:
    child.stdin.close()
    try:child.wait(timeout=12)
    except subprocess.TimeoutExpired:subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True)
    child.stdout.close()
