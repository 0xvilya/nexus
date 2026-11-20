"""Real 1.5B model + controlled local HTML + actual CDP actions, no planner mocks."""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import bootstrap
from rainforge_recovery import atomic_json
from rainforge_worker.rpc import Worker

parser = argparse.ArgumentParser()
parser.add_argument('--models-dir', type=Path, required=True)
args = parser.parse_args()
root = bootstrap.ROOT
data = root / 'work/browser-agent-model' / uuid4().hex
data.mkdir(parents=True)
fixture = (root / 'tests/fixtures/browser-agent.html').read_bytes()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(fixture)

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
server_thread = threading.Thread(target=server.serve_forever, daemon=True)
server_thread.start()
worker = Worker(data, root / 'runtime', args.models_dir)
try:
    model = next(m for m in worker.snapshot()['models'] if m['downloaded'] and 'coder' in m['filename'].lower() and 1_000_000_000 < m['bytes'] < 1_500_000_000)
    worker.start_model(model['filename'], mode='auto', context=4096)
    # Only the test harness permits its own loopback fixture. Product URL policy is unchanged.
    with patch('rainforge_worker.browser.web_url', side_effect=lambda url: url):
        worker.browser.navigate(f'http://127.0.0.1:{server.server_port}/')
    started = time.monotonic()
    key = worker.browser_agent.start('Type RainForge in Name, click Continue, then click Confirm.', 'Hello RainForge', '#result', True, 8)['id']
    worker.browser_agent.thread.join(timeout=190)
    run = worker.browser_agent.status(key)['run']
    report = {'status': 'PASS' if run['state'] == 'checks_passed' else 'FAIL', 'model': model['filename'],
              'real_model': True, 'real_browser': True, 'fixture': 'local controlled HTML with dynamic second-step button',
              'elapsed_seconds': round(time.monotonic() - started, 3), 'run': run}
    atomic_json(root / 'work/browser-agent-model-results.json', report)
    print(json.dumps(report, ensure_ascii=True))
    assert run['state'] == 'checks_passed', run.get('error')
    assert len([event for event in run['events'] if event['state'] == 'applied']) >= 3
finally:
    worker.close()
    server.shutdown()
    server.server_close()
    server_thread.join()
