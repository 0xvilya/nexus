import json,os,sys,tempfile,threading,unittest,subprocess
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_worker.browser import ManagedBrowser
class BrowserTests(unittest.TestCase):
 @unittest.skipUnless(os.environ.get('RF_BROWSER_TEST')=='1','Real browser check requires RF_BROWSER_TEST=1')
 def test_observed_drag_moves_item_and_rejects_stale_targets(self):
  with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
   browser=ManagedBrowser(Path(tmp))
   try:
    html="""<div id=source draggable=true aria-label='Card' style='width:120px;height:100px;background:red'>Card</div><div id=target data-dropzone aria-label='Board' style='margin-top:30px;width:150px;height:150px;background:blue'>Board</div><script>source.ondragstart=e=>e.dataTransfer.setData('text/plain','card');target.ondragover=e=>e.preventDefault();target.ondrop=e=>{e.preventDefault();target.textContent='Moved '+e.dataTransfer.getData('text/plain')};</script>"""
    browser.evaluate('document.body.innerHTML='+json.dumps(html))
    browser.evaluate("source.ondragstart=e=>e.dataTransfer.setData('text/plain','card');target.ondragover=e=>e.preventDefault();target.ondrop=e=>{e.preventDefault();target.textContent='Moved '+e.dataTransfer.getData('text/plain')}")
    state=browser.observe(); controls={e['label']:e for e in state['elements']}
    self.assertTrue(controls['Card']['can_drag']);self.assertTrue(controls['Board']['can_drop'])
    browser.evaluate("target.setAttribute('aria-dropeffect','move')")
    self.assertEqual(browser.drag_observed(state['token'],controls['Card']['ref'],controls['Board']['ref'])['reason'],'stale_observation')
    state=browser.observe();controls={e['label']:e for e in state['elements']}
    self.assertTrue(browser.drag_observed(state['token'],controls['Card']['ref'],controls['Board']['ref'])['acted'])
    self.assertEqual(browser.evaluate('target.textContent'),'Moved card')
    self.assertEqual(browser.drag_observed('bad',controls['Card']['ref'],controls['Board']['ref'])['reason'],'stale_observation')
   finally:browser.close()
 @unittest.skipUnless(os.environ.get('RF_BROWSER_TEST')=='1','Real browser check requires RF_BROWSER_TEST=1')
 def test_bound_agent_does_not_submit_sensitive_button(self):
  with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
   browser=ManagedBrowser(Path(tmp))
   try:
    browser.evaluate("document.body.innerHTML='<button id=send onclick=\"+chr(34)+\"document.body.dataset.sent=1\"+chr(34)+\">Send</button>'")
    state=browser.observe();result=browser.act_observed(state['token'],state['elements'][0]['ref'],'click')
    self.assertFalse(result['acted']);self.assertEqual(result['reason'],'manual_confirmation_required')
    self.assertIsNone(browser.evaluate('document.body.dataset.sent||null'))
   finally:browser.close()
 @unittest.skipUnless(os.environ.get('RF_BROWSER_TEST')=='1','Real browser check requires RF_BROWSER_TEST=1')
 def test_bound_controls_use_labels_live_capabilities_and_form_overrides(self):
  html=('''<span id="title">Customer name</span><input id="customer" aria-labelledby="title">
    <label><input id="selected" type="checkbox" checked>Enabled option</label>
    <fieldset disabled><button>Disabled</button></fieldset>
    <div inert><button>Hidden action</button></div>
    <form method="get"><button formmethod="post" aria-label="Continue">Next</button></form>
    <button aria-label="Оплатить"><span>Продолжить</span></button>''')
  with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
   browser=ManagedBrowser(Path(tmp))
   try:
    browser.evaluate('document.body.innerHTML='+json.dumps(html))
    state=browser.observe()
    controls={element['label']:element for element in state['elements']}
    self.assertIn('Customer name',controls);self.assertTrue(controls['Enabled option']['checked'])
    self.assertFalse(controls['Disabled']['can_click']);self.assertNotIn('Hidden action',controls)
    for label in ('Continue','Оплатить'):
     result=browser.act_observed(state['token'],controls[label]['ref'],'click')
     self.assertEqual(result['reason'],'manual_confirmation_required')
    browser.evaluate("document.body.prepend(document.createElement('input'))")
    fresh=browser.observe()
    changed=next(element for element in fresh['elements'] if element['label']=='Customer name')
    self.assertNotEqual(changed['ref'],controls['Customer name']['ref'])
    self.assertEqual(changed['fingerprint'],controls['Customer name']['fingerprint'])
    browser.evaluate('document.querySelector("#customer").readOnly=true')
    result=browser.act_observed(fresh['token'],changed['ref'],'type','Unexpected')
    self.assertEqual(result['reason'],'stale_observation')
    self.assertEqual(browser.evaluate('document.querySelector("#customer").value'),'')
   finally:browser.close()
 @unittest.skipUnless(os.name=='nt' and os.environ.get('RF_BROWSER_TEST')=='1','Owned browser crash check requires Windows and RF_BROWSER_TEST=1')
 def test_controller_crash_terminates_owned_browser(self):
  import ctypes
  from ctypes import wintypes
  code="""import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import bootstrap
from rainforge_worker.browser import ManagedBrowser
b=ManagedBrowser(Path(sys.argv[2]));b.start()
print(b.process.pid,flush=True)
input()
"""
  with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
   child=subprocess.Popen([sys.executable,'-c',code,str(bootstrap.ROOT/'scripts'),tmp],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   api=ctypes.WinDLL('kernel32',use_last_error=True)
   api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];api.OpenProcess.restype=wintypes.HANDLE
   api.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];api.CloseHandle.argtypes=[wintypes.HANDLE]
   handle=None
   try:
    line=child.stdout.readline().strip();self.assertTrue(line.isdigit(),line)
    handle=api.OpenProcess(0x00100000,False,int(line));self.assertTrue(handle)
    child.kill();child.wait(timeout=5)
    self.assertEqual(api.WaitForSingleObject(handle,5000),0)
   finally:
    if child.poll() is None:child.kill();child.wait(timeout=5)
    if handle:api.CloseHandle(handle)
    child.stdin.close();child.stdout.close();child.stderr.close()
 def test_wait_validation_does_not_launch_browser(self):
  with tempfile.TemporaryDirectory() as tmp:
   browser=ManagedBrowser(Path(tmp))
   for arguments in [('',),('x',''),('x','text',True),('x','text',20)]:
    with self.assertRaises(ValueError):browser.wait_for(*arguments)
   with self.assertRaisesRegex(ValueError,'Open a managed'):browser.wait_for('#result','Ready')
   self.assertIsNone(browser.process)
 @unittest.skipUnless(os.environ.get('RF_BROWSER_TEST')=='1','Real browser check requires RF_BROWSER_TEST=1')
 def test_real_navigation_input_click_and_observation(self):
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):
    self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(b'<title>RainForge fixture</title><input id="name"><button id="go" onclick="setTimeout(()=>document.querySelector(\'#result\').innerText=\'Hello \'+document.querySelector(\'#name\').value,350)">Go</button><p id="result">Waiting</p><input type="password" id="secret" value="do-not-read"><span hidden id="hidden">Hidden</span>')
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
   browser=ManagedBrowser(Path(tmp))
   try:
    with patch('rainforge_worker.browser.web_url',side_effect=lambda u:u):state=browser.navigate(f'http://127.0.0.1:{server.server_port}/')
    self.assertEqual(state['title'],'RainForge fixture');self.assertIn('Waiting',state['text'])
    # An on-demand page read must not create a visible desktop browser window.
    import ctypes
    from ctypes import wintypes
    ui=ctypes.WinDLL('user32');visible=[]
    callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    ui.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    ui.IsWindowVisible.argtypes=[wintypes.HWND]
    @callback
    def collect(handle,unused):
     pid=wintypes.DWORD();ui.GetWindowThreadProcessId(handle,ctypes.byref(pid))
     if pid.value==browser.process.pid and ui.IsWindowVisible(handle):visible.append(int(handle))
     return True
    ui.EnumWindows(collect,0);self.assertEqual(visible,[])

    browser.act('#name','RainForge');browser.act('#go');result=browser.wait_for('#result','Hello RainForge',2);self.assertTrue(result['verified']);self.assertIn('Hello RainForge',result['observation']['text'])
    for selector,text in [('#result','Never appears'),('#hidden','Hidden'),('#secret','do-not-read'),('input',None)]:
     with self.assertRaisesRegex(ValueError,'condition timed out'):browser.wait_for(selector,text,.2)
    # Placeholder-like untrusted text remains literal data in fixed JavaScript.
    browser.act('#name','EXPECTED SELECTOR " ; literal')
    self.assertTrue(browser.wait_for('#name','EXPECTED SELECTOR " ; literal',1)['verified'])
    with self.assertRaises(ValueError):browser.act('missing')
   finally:browser.close();server.shutdown();server.server_close();thread.join()
