import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import bootstrap
from rainforge_recovery import atomic_json
from rainforge_worker.browser import ManagedBrowser
from rainforge_worker.browser_agent import BrowserAgent


class BrowserAgentTests(unittest.TestCase):
    def test_drag_schema_and_permissions_bind_observed_elements(self):
        from rainforge_worker.browser_agent import response_schema
        elements = [{'ref':'e0','can_click':False,'can_type':False,'can_drag':True,'can_drop':False},
                    {'ref':'e1','can_click':False,'can_type':False,'can_drag':False,'can_drop':True}]
        self.assertIn('drag', [v['properties']['tool']['enum'][0] for v in response_schema(elements,True)['oneOf']])
        self.assertNotIn('drag', [v['properties']['tool']['enum'][0] for v in response_schema(elements,False)['oneOf']])
        with tempfile.TemporaryDirectory() as temp:
            agent=BrowserAgent(SimpleNamespace(),Path(temp))
            decision={'tool':'drag','source':'e0','target':'e1'}
            self.assertEqual(agent.validate(decision,{'elements':elements},True),'drag')
            for invalid in ({'tool':'drag','source':'e1','target':'e0'}, {'tool':'drag','source':'e0','target':'e0'}):
                with self.assertRaises(ValueError):agent.validate(invalid,{'elements':elements},True)
            with self.assertRaises(ValueError):agent.validate(decision,{'elements':elements},False)

    @unittest.skipUnless(os.getenv('RF_BROWSER_TEST') == '1', 'Real browser fixture requires RF_BROWSER_TEST=1')
    def test_real_drag_in_agent_loop_proves_result(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            browser=ManagedBrowser(Path(temp));worker=SimpleNamespace(browser=browser,actions=SimpleNamespace(lock=threading.Lock()))
            agent=BrowserAgent(worker,Path(temp))
            try:
                browser.evaluate("document.body.innerHTML='<div id=a draggable=true aria-label=Card style=\"width:100px;height:100px;background:red\">Card</div><div id=b data-dropzone aria-label=Board style=\"width:150px;height:150px;margin-top:40px;background:blue\">Board</div><p id=result>Waiting</p>'")
                browser.evaluate("a.ondragstart=e=>e.dataTransfer.setData('text/plain','card');b.ondragover=e=>e.preventDefault();b.ondrop=e=>{e.preventDefault();document.querySelector('#result').textContent='Moved card'}")
                # The loop requires a HTTP origin. A local server supplies the prepared page.
                class Handler(BaseHTTPRequestHandler):
                    def log_message(self,*args):pass
                    def do_GET(self):
                        self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
                        self.wfile.write(b'<div id=a draggable=true aria-label=Card style="width:100px;height:100px;background:red">Card</div><div id=b data-dropzone aria-label=Board style="width:150px;height:150px;margin-top:40px;background:blue">Board</div><p id=result>Waiting</p><script>a.ondragstart=e=>e.dataTransfer.setData("text/plain","card");b.ondragover=e=>e.preventDefault();b.ondrop=e=>{e.preventDefault();document.querySelector("#result").textContent="Moved card"}</script>')
                server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    with patch('rainforge_worker.browser.web_url',side_effect=lambda u:u):browser.navigate(f'http://127.0.0.1:{server.server_port}/')
                    agent.decide=lambda run,obs,deadline:{'tool':'drag','source':next(e['ref'] for e in obs['elements'] if e.get('can_drag')),'target':next(e['ref'] for e in obs['elements'] if e.get('can_drop'))}
                    agent.start('Drag Card to Board','Moved card',allow_mutation=True,max_steps=3);agent.thread.join(15)
                    self.assertFalse(agent.thread.is_alive());run=agent.status()['run']
                    self.assertEqual(run['state'],'checks_passed');self.assertTrue(run['proof']['verified'])
                finally:server.shutdown();server.server_close();thread.join()
            finally:browser.close()

    def test_validation_and_recovery_never_replay_pending_actions(self):
        with tempfile.TemporaryDirectory() as temp:
            worker = SimpleNamespace(actions=SimpleNamespace(lock=threading.Lock()), browser=SimpleNamespace(target=None))
            agent = BrowserAgent(worker, Path(temp))
            with self.assertRaisesRegex(ValueError, 'Open the prepared'):
                agent.start('Click', 'Done')
            observation = {'elements': [{'ref': 'e0', 'can_click': True, 'can_type': False}]}
            for decision, allowed in [({'tool': 'click', 'element': 'e0'}, False),
                                      ({'tool': 'type', 'element': 'e0', 'text': 'x'}, True),
                                      ({'tool': 'click', 'element': 'missing'}, True),
                                      ({'tool': 'shell', 'command': 'anything'}, True),
                                      ({'tool': 'click', 'element': 'e0', 'extra': 'x'}, True)]:
                with self.assertRaises(ValueError):
                    agent.validate(decision, observation, allowed)
            run = {'id': 'a' * 32, 'state': 'acting', 'pending_action': {'tool': 'click'}, 'proof': None}
            atomic_json(agent.root / (run['id'] + '.json'), run)
            recovered = BrowserAgent(worker, Path(temp)).status()['run']
            self.assertEqual(recovered['state'], 'interrupted')
            self.assertEqual(recovered['pending_action'], {'tool': 'click'})
            self.assertIsNone(recovered['proof'])
            from rainforge_worker.rpc import Worker
            worker.actions.lock.acquire()
            try:
                with self.assertRaisesRegex(RuntimeError, 'reserved'):
                    Worker.manual_browser(worker, 'inspect')
            finally:
                worker.actions.lock.release()

    @unittest.skipUnless(os.getenv('RF_BROWSER_TEST') == '1', 'Real controlled browser fixture requires RF_BROWSER_TEST=1')
    def test_real_dom_step_loop_stale_refs_limits_and_cancel(self):
        html = (bootstrap.ROOT / 'tests/fixtures/browser-agent.html').read_bytes()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(html)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            browser = ManagedBrowser(Path(temp))
            worker = SimpleNamespace(browser=browser, actions=SimpleNamespace(lock=threading.Lock()))
            agent = BrowserAgent(worker, Path(temp))
            url = f'http://127.0.0.1:{server.server_port}/'
            def load():
                with patch('rainforge_worker.browser.web_url', side_effect=lambda u: u):
                    browser.navigate(url)
            def wait():
                agent.thread.join(timeout=15)
                self.assertFalse(agent.thread.is_alive())
                return agent.status()['run']
            def chooser(run, observation, deadline):
                for element in observation['elements']:
                    if element['label'] == 'Name' and element['value'] != 'RainForge':
                        return {'tool': 'type', 'element': element['ref'], 'text': 'RainForge'}
                for label in ('Continue', 'Confirm'):
                    element = next((e for e in observation['elements'] if e['label'] == label and e['can_click']), None)
                    if element:
                        return {'tool': 'click', 'element': element['ref']}
                return {'tool': 'wait'}
            try:
                load()
                observation = browser.observe()
                self.assertFalse(any(e['type'] in ('password', 'file') for e in observation['elements']))
                self.assertNotEqual(observation['token'], 'forged')
                button = next(e for e in observation['elements'] if e['label'] == 'Continue')
                browser.evaluate("document.querySelector('#next').outerHTML='<button id=next>Changed button</button>'")
                stale = browser.act_observed(observation['token'], button['ref'], 'click')
                self.assertFalse(stale['acted'])
                browser.evaluate("document.body.insertAdjacentHTML('beforeend','<a href=\"https://outside.invalid/\">External</a>')")
                external_observation = browser.observe()
                external = next(e for e in external_observation['elements'] if e['label'] == 'External')
                denied = browser.act_observed(external_observation['token'], external['ref'], 'click')
                self.assertFalse(denied['acted'])
                self.assertEqual(denied['reason'], 'outside_initial_origin')
                load()
                replaced = False
                def with_stale_node(run, observation, deadline):
                    nonlocal replaced
                    decision = chooser(run, observation, deadline)
                    if not replaced and decision['tool'] == 'type':
                        browser.evaluate("document.querySelector('#name').replaceWith(document.querySelector('#name').cloneNode(true))")
                        replaced = True
                    return decision
                with patch.object(agent, 'decide', side_effect=with_stale_node):
                    agent.start('Enter RainForge, Continue, then Confirm', 'Hello RainForge', '#result', True, 8)
                    run = wait()
                self.assertEqual(run['state'], 'checks_passed', run['error'])
                self.assertTrue(any(e['state'] == 'not_applied' for e in run['events']))
                self.assertEqual(run['proof']['condition']['contains_text'], 'Hello RainForge')
                self.assertFalse(worker.actions.lock.locked())
                load()
                fresh = browser.observe()
                name = next(e for e in fresh['elements'] if e['label'] == 'Name')
                browser.act_observed(fresh['token'], name['ref'], 'type', 'RainForge')
                fresh = browser.observe()
                name = next(e for e in fresh['elements'] if e['label'] == 'Name')
                repeated = browser.act_observed(fresh['token'], name['ref'], 'type', 'RainForge')
                self.assertFalse(repeated['acted'])
                self.assertEqual(repeated['reason'], 'value_already_matches')
                load()
                with patch.object(agent, 'decide', side_effect=chooser):
                    agent.start('Enter RainForge', 'Never present', '#result', False, 4)
                    denied_run = wait()
                self.assertEqual(denied_run['state'], 'failed')
                self.assertEqual(denied_run['errors'], 2)
                self.assertTrue(all(e['state'] == 'rejected' for e in denied_run['events']))
                self.assertEqual(browser.observe()['elements'][0]['value'], '')
                load()
                with patch.object(agent, 'decide', return_value={'tool': 'wait'}):
                    agent.start('Do not finish prematurely', 'Never present', '#result', False, 1)
                    self.assertEqual(wait()['state'], 'blocked')
                self.assertIsNone(agent.status()['run']['proof'])
                load()
                with patch.object(agent, 'decide', return_value={'tool': 'wait'}):
                    agent.start('Wait for missing text', 'Never present', '#result', False, 8)
                    agent.cancel()
                    self.assertEqual(wait()['state'], 'cancelled')
                self.assertFalse(worker.actions.lock.locked())
            finally:
                agent.close()
                browser.close()
                server.shutdown()
                server.server_close()
                server_thread.join()
