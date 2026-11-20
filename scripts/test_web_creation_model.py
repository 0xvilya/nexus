"""Real local-model acceptance for editable web content and generated static sites.

The HTTP fixture and generated site run in an isolated test profile. Only this
test harness allows its own loopback URL. Production URL policy is unchanged.
"""
import argparse
import functools
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from unittest.mock import patch
from uuid import uuid4

import bootstrap
from rainforge_recovery import atomic_json
from rainforge_worker.rpc import Worker


EDITOR_FIXTURE = b"""<!doctype html><meta charset=utf-8><title>Owned editor fixture</title>
<label>Title<input id=title aria-label=Title></label>
<div id=article contenteditable=true role=textbox aria-label="Article draft">Empty</div>
<button id=preview>Preview</button><p id=result>Waiting</p>
<script>
preview.onclick=()=>result.textContent='READY '+article.textContent;
</script>"""


class QuietFixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(EDITOR_FIXTURE)


class QuietFiles(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-dir', type=Path, required=True)
    args = parser.parse_args()
    root = bootstrap.ROOT
    data = root / 'work' / ('web-creation-acceptance-' + uuid4().hex[:10])
    data.mkdir(parents=True)
    worker = Worker(data, root / 'runtime', args.models_dir)
    servers = []
    proof = {'status': 'FAIL', 'profile': str(data), 'production_url_policy_changed': False}

    def serve(handler):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return f'http://127.0.0.1:{server.server_port}/'

    def navigate_owned(url):
        with patch('rainforge_worker.browser.web_url', side_effect=lambda value: value):
            return worker.browser.navigate(url)

    try:
        model = next(model for model in worker.snapshot()['models']
                     if model['downloaded'] and 'coder' in model['filename'].lower())
        proof['model'] = model['filename']
        worker.start_model(model['filename'], mode='auto', context=4096)
        navigate_owned(serve(QuietFixture))
        started = time.monotonic()
        run_id = worker.browser_agent.start(
            'Type Hello Nexus into Article draft, then click Preview.',
            'READY Hello Nexus', '#result', True, 6,
        )['id']
        worker.browser_agent.thread.join(timeout=180)
        assert not worker.browser_agent.thread.is_alive(), 'Browser model timed out'
        editor = worker.browser_agent.status(run_id)['run']
        proof['editor'] = {
            'state': editor['state'], 'elapsed_seconds': round(time.monotonic() - started, 3),
            'events': editor['events'], 'error': editor.get('error'),
        }
        assert editor['state'] == 'checks_passed', editor
        assert worker.browser.evaluate('article.textContent') == 'Hello Nexus'

        conversation = worker.dispatch('conversation.create', {})['id']
        goal = (
            'Create a tiny self-contained website project in exactly two files, index.html and README.md. '
            'The page has an h1 "Nexus Counter", a visible span id="count" initially showing 0, '
            'and a button id="increment" labeled "Increase". Clicking Increase increments count by one. '
            'Use plain HTML with CSS and JavaScript inline in index.html, no dependencies or network requests. '
            'Keep the complete HTML below 55 lines. README explains opening index.html. Save both files.'
        )
        started = time.monotonic()
        run_id = worker.dispatch('task.start', {
            'goal': goal, 'conversation_id': conversation, 'mode': 'auto',
            'model_filename': model['filename'],
        })['id']
        worker.actions.thread.join(timeout=240)
        assert not worker.actions.thread.is_alive(), 'Site generation timed out'
        run = worker.actions.status(run_id)
        proof['creation'] = {
            'state': run['state'], 'tool_calls': run.get('tool_calls'),
            'planner_calls': run.get('planner_calls'), 'error': run.get('error'),
            'elapsed_seconds': round(time.monotonic() - started, 3),
        }
        assert run['state'] == 'project_created', run
        project = next(result for result in run['results'] if result.get('project_verified'))
        folder = worker.workspace_tools.root / project['directory']
        assert (folder / 'index.html').is_file()
        proof['creation']['files'] = project['files']
        for file in project['files']:
            assert worker.workspace_tools.read(file['path'])['sha256'] == file['sha256']
        url = serve(functools.partial(QuietFiles, directory=str(folder)))
        navigate_owned(url)
        assert worker.browser.wait_for('#count', '0', timeout=2)['verified']
        for expected in (1, 2, 3):
            state = worker.browser.observe()
            button = next(row for row in state['elements']
                          if row['can_click'] and row['label'] == 'Increase')
            assert worker.browser.act_observed(state['token'], button['ref'], 'click')['acted']
            assert worker.browser.wait_for('#count', str(expected), timeout=2)['verified']
        proof['site_behavior'] = {'initial_count': 0, 'clicks': 3, 'final_count': 3, 'verified': True}
        proof['status'] = 'PASS'
    except BaseException as error:
        proof['error'] = str(error)[:3000]
        raise
    finally:
        worker.close()
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        atomic_json(root / 'work' / 'web-creation-acceptance.json', proof)
        print(json.dumps(proof, ensure_ascii=True))


if __name__ == '__main__':
    main()

