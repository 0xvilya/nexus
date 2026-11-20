"""Owned Chromium session with fixed CDP operations, no arbitrary JavaScript RPC."""
from contextlib import closing
import json
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from uuid import uuid4
from .actions import web_url
from rainforge_native.windows_job import WindowsJob

class ManagedBrowser:
 def __init__(self,data):
  self.data=data;self.lock=threading.RLock();self.process=None;self.target=None;self.counter=0;self.job=None
 def start(self):
  if self.target and self.process and self.process.poll() is None:return
  self.close()
  candidates=[Path(os.environ[k])/sub for k in ('ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA') if os.environ.get(k) for sub in ('Google/Chrome/Application/chrome.exe','Microsoft/Edge/Application/msedge.exe')]
  exe=next((p for p in candidates if p.is_file()),None)
  if exe is None:raise ValueError('Chrome or Edge is required for browser control')
  profile=self.data/('browser-profile-'+uuid4().hex);profile.mkdir(parents=True)
  self.process=subprocess.Popen([str(exe),'--remote-debugging-port=0','--remote-debugging-address=127.0.0.1','--user-data-dir='+str(profile),'--no-first-run','--no-default-browser-check','--headless=new','--window-size=1280,800','about:blank'],shell=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  try:
   self.job=WindowsJob();self.job.assign(self.process)
  except Exception:
   self.close();raise
  deadline=time.monotonic()+15
  http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  while time.monotonic()<deadline:
   try:
    port=int((profile/'DevToolsActivePort').read_text().splitlines()[0])
    with http.open(f'http://127.0.0.1:{port}/json/list',timeout=1) as response:pages=json.load(response)
    page=next(p for p in pages if p.get('type')=='page' and p.get('url')=='about:blank')
    self.target=page['webSocketDebuggerUrl'];self.browser_port=port;return
   except (OSError,ValueError,StopIteration):time.sleep(.1)
  self.close();raise RuntimeError('Managed browser did not start')
 def command(self,method,params=None,timeout=15):
  try:import websocket
  except ImportError as exc:raise RuntimeError('Browser control requires websocket-client; use the packaged worker or prepared development venv') from exc
  self.start();self.counter+=1;key=self.counter
  try:
   deadline=time.monotonic()+timeout
   with closing(websocket.create_connection(self.target,timeout=timeout,suppress_origin=True,http_proxy_host=None)) as ws:
    ws.send(json.dumps({'id':key,'method':method,'params':params or {}}))
    for _ in range(200):
     remaining=deadline-time.monotonic()
     if remaining<=0:raise RuntimeError('Browser command timed out')
     ws.settimeout(remaining)
     raw=ws.recv()
     if len(raw)>4*1024*1024:raise RuntimeError('Browser response too large')
     reply=json.loads(raw)
     if reply.get('id')==key:
      if 'error' in reply:raise RuntimeError('Browser command failed: '+reply['error'].get('message','error'))
      return reply.get('result',{})
    raise RuntimeError('Browser command reply missing')
  except (OSError,websocket.WebSocketException) as exc:raise RuntimeError('Browser connection lost; close and restart browser session') from exc
 def evaluate(self,expression,timeout=15,context_id=None):
  params={'expression':expression,'returnByValue':True}
  if context_id is not None:params['contextId']=context_id
  r=self.command('Runtime.evaluate',params,timeout=timeout)
  if r.get('exceptionDetails'):
   detail=r['exceptionDetails'];description=detail.get('exception',{}).get('description') or detail.get('text','error')
   raise ValueError('Element action failed: '+description[:500])
  return r.get('result',{}).get('value')
 def inspect(self):
  with self.lock:
   return self.evaluate("""(()=>({url:location.href,title:document.title,text:(document.body?.innerText||'').slice(0,12000),elements:Array.from(document.querySelectorAll('a,button,input,textarea,select')).filter(e=>e.getClientRects().length&&!e.hidden).slice(0,80).map(e=>({tag:e.tagName.toLowerCase(),id:e.id,type:e.type||'',name:e.getAttribute('name')||'',label:(e.innerText||e.getAttribute('aria-label')||e.placeholder||'').slice(0,150)}))}))()""")
 def observe(self):
  """Private agent observation in an isolated JS world; refs bind actual DOM nodes."""
  with self.lock:
   frame=self.command('Page.getFrameTree')['frameTree']['frame']['id']
   context=self.command('Page.createIsolatedWorld',{'frameId':frame,'worldName':'rainforge-observer-v1'})['executionContextId']
   token=uuid4().hex
   script="(()=>{const token="+json.dumps(token)+";"+"""
    const visible=e=>e.isConnected&&e.getClientRects().length&&!e.closest('[hidden],[inert],[aria-hidden=true]')&&getComputedStyle(e).visibility==='visible';
    const label=e=>{
      const refs=(e.getAttribute('aria-labelledby')||'').split(/\\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' ').trim();
      const labels=Array.from(e.labels||[]).map(node=>node.textContent).join(' ').trim();
      return (refs||e.getAttribute('aria-label')||labels||e.innerText||e.placeholder||e.title||e.name||'').trim().slice(0,150);
    };
    const enabled=e=>!e.matches(':disabled')&&!e.closest('[aria-disabled=true]');
    const canClick=e=>enabled(e)&&(['A','BUTTON'].includes(e.tagName)||e.getAttribute('role')==='button'||e.tagName==='INPUT'&&['button','submit','checkbox','radio','reset'].includes(e.type));
    const editingHost=e=>e.isContentEditable&&!e.parentElement?.isContentEditable;
    const plainEditor=e=>editingHost(e)&&e.textContent.length<=2000&&!e.querySelector('[contenteditable=false]');
    const canType=e=>enabled(e)&&!e.readOnly&&e.getAttribute('aria-readonly')!=='true'&&(plainEditor(e)||e.tagName==='SELECT'&&!e.multiple||e.tagName==='TEXTAREA'||e.tagName==='INPUT'&&['text','search','email','url','tel','number','date','time','datetime-local'].includes(e.type));
    const canDrag=e=>enabled(e)&&e.getAttribute('draggable')==='true';
    const canDrop=e=>enabled(e)&&e.matches('[data-dropzone],[dropzone],[ondrop],[aria-dropeffect]:not([aria-dropeffect=none])');
    const ids=globalThis.__rfNodeIds ||= {nodes:new WeakMap(),next:0,document:token};
    const identity=e=>{
      if(!ids.nodes.has(e))ids.nodes.set(e,++ids.next);
      return ids.document+':'+ids.nodes.get(e);
    };
    const fingerprint=e=>JSON.stringify([identity(e),e.tagName,e.type||'',e.id,e.getAttribute('href'),label(e),e.checked??null,e.getAttribute('aria-pressed'),['INPUT','TEXTAREA','SELECT'].includes(e.tagName)?String(e.value).slice(0,2000):editingHost(e)?e.textContent.slice(0,2000):'',e.tagName==='SELECT'?Array.from(e.options).map(o=>[o.value,o.text,o.disabled]):null,e.getAttribute('draggable'),e.getAttribute('aria-dropeffect'),e.getAttribute('contenteditable'),e.getAttribute('aria-readonly')]);
    // Navigation-heavy sites can place the useful form after hundreds of links.
    // Keep the bound small while exposing editors and form controls first.
    const priority=e=>['INPUT','TEXTAREA','SELECT'].includes(e.tagName)||e.isContentEditable||canDrag(e)||canDrop(e)?0:canClick(e)&&e.tagName!=='A'?1:2;
    const nodes=Array.from(document.querySelectorAll('a,button,input,textarea,select,[contenteditable],[role=button],[draggable=true],[data-dropzone],[dropzone],[ondrop],[aria-dropeffect]:not([aria-dropeffect=none])'))
      .filter(e=>visible(e)&&!['password','hidden','file'].includes(e.type)).sort((a,b)=>priority(a)-priority(b)).slice(0,80);
    globalThis.__rfObservation={token,nodes,fingerprint,visible,label,canClick,canType,canDrag,canDrop};
    return {token,url:location.href,title:document.title,text:(document.body?.innerText||'').slice(0,6000),
      elements:nodes.map((e,i)=>({ref:'e'+i,tag:e.tagName.toLowerCase(),label:label(e),type:e.type||(editingHost(e)?'contenteditable':''),
        can_click:canClick(e),can_type:canType(e),can_drag:canDrag(e),can_drop:canDrop(e),
        checked:['checkbox','radio'].includes(e.type)?e.checked:undefined,pressed:e.getAttribute('aria-pressed')||undefined,
        options:e.tagName==='SELECT'?Array.from(e.options).slice(0,40).map(o=>({value:o.value.slice(0,120),label:o.text.slice(0,120),disabled:o.disabled||!!o.closest('optgroup:disabled')})):[],
        value:['INPUT','TEXTAREA','SELECT'].includes(e.tagName)?String(e.value).slice(0,150):editingHost(e)?e.textContent.slice(0,150):'',
        fingerprint:fingerprint(e)}))};})()"""
   result=self.evaluate(script,context_id=context)
   self.observed={'token':token,'context':context,'at':time.monotonic(),'elements':result['elements'],'url':result['url']}
   return result
 def act_observed(self,token,element,operation,text=''):
  """Private API: never resolve a stale ref by a newly matching CSS selector."""
  with self.lock:
   previous=getattr(self,'observed',None)
   if not previous or previous['token']!=token or time.monotonic()-previous['at']>45:
    return {'acted':False,'reason':'stale_observation'}
   item=next((e for e in previous['elements'] if e['ref']==element),None)
   if operation not in ('click','type') or not item or not item['can_'+('type' if operation=='type' else 'click')]:
    raise ValueError('Element does not support the requested observed action')
   if not isinstance(text,str) or len(text)>2000:raise ValueError('Input text too long')
   args=json.dumps({'token':token,'index':int(element[1:]),'fingerprint':item['fingerprint'],'url':previous['url'],'operation':operation,'text':text})
   script="(()=>{const a="+args+";"+"""
    const s=globalThis.__rfObservation;
    if(!s||s.token!==a.token||location.href!==a.url)return {acted:false,reason:'stale_observation'};
    const e=s.nodes[a.index];
    if(!e||!s.visible(e)||s.fingerprint(e)!==a.fingerprint||!(a.operation==='click'?s.canClick(e):s.canType(e)))return {acted:false,reason:'stale_observation'};
    const sensitiveLabel=[s.label(e),e.innerText,e.title,e.value].filter(Boolean).join(' ');
    if(a.operation==='click'&&(/(?:^|[^\\p{L}\\p{N}_])(send|publish|submit|pay|buy|purchase|delete|erase|uninstall|approve|allow|отправить|оплатить|купить|удалить|опубликовать|разрешить)(?:[^\\p{L}\\p{N}_]|$)/iu.test(sensitiveLabel)||(e.form&&e.type==='submit'&&(e.getAttribute('formmethod')||e.form.method).toLowerCase()!=='get')))return {acted:false,reason:'manual_confirmation_required'};
    const destination=e.tagName==='A'?e.href:((e.tagName==='BUTTON'||e.tagName==='INPUT')&&e.type==='submit'&&e.form?e.formAction||e.form.action:null);
    if(destination){const target=new URL(destination,location.href);if(target.origin!==location.origin||!['http:','https:'].includes(target.protocol))return {acted:false,reason:'outside_initial_origin'};}
    if(e.getAttribute('target')==='_blank'||e.formTarget==='_blank')return {acted:false,reason:'new_window_not_supported'};
    if(a.operation==='type'){
      if(e.tagName==='SELECT'){
        if(e.multiple)return {acted:false,reason:'multiple_select_not_supported'};
        const matches=Array.from(e.options).filter(o=>!o.disabled&&!o.closest('optgroup:disabled')&&(o.value===a.text||o.text===a.text));
        if(matches.length!==1)return {acted:false,reason:'option_missing_or_ambiguous'};
        if(e.value===matches[0].value)return {acted:false,reason:'value_already_matches'};
        Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e,matches[0].value);
        e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return {acted:true};
      }
      if(e.isContentEditable){
        if(e.textContent===a.text)return {acted:false,reason:'value_already_matches'};
        e.focus();
        if(!s.visible(e)||!s.canType(e)||s.fingerprint(e)!==a.fingerprint)return {acted:false,reason:'stale_observation'};
        const before=new InputEvent('beforeinput',{bubbles:true,composed:true,cancelable:true,inputType:'insertText',data:a.text});
        if(!e.dispatchEvent(before))return {acted:false,reason:'input_cancelled'};
        // A page's beforeinput handler may replace or disable its editor.
        if(!s.visible(e)||!s.canType(e)||s.fingerprint(e)!==a.fingerprint)return {acted:false,reason:'stale_observation'};
        e.textContent=a.text;
        e.dispatchEvent(new InputEvent('input',{bubbles:true,composed:true,inputType:'insertText',data:a.text}));
        return {acted:true};
      }
      if(e.readOnly||!['INPUT','TEXTAREA'].includes(e.tagName)||['password','file','hidden'].includes(e.type))return {acted:false,reason:'unsupported_input'};
      if(['date','time','datetime-local','number'].includes(e.type)){
        const probe=document.createElement('input');probe.type=e.type;probe.value=a.text;
        if(probe.value!==a.text)return {acted:false,reason:'invalid_input_value'};
      }
      if(String(e.value)===a.text)return {acted:false,reason:'value_already_matches'};
      const proto=e.tagName==='INPUT'?HTMLInputElement.prototype:HTMLTextAreaElement.prototype;
      Object.getOwnPropertyDescriptor(proto,'value').set.call(e,a.text);
      e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));
    }else HTMLElement.prototype.click.call(e);
    return {acted:true};})()"""
   return self.evaluate(script,context_id=previous['context'])
 def drag_observed(self,token,source,target):
  """Drag only between two currently observed, explicitly marked page elements."""
  with self.lock:
   previous=getattr(self,'observed',None)
   if not previous or previous['token']!=token or time.monotonic()-previous['at']>45:
    return {'acted':False,'reason':'stale_observation'}
   start=next((e for e in previous['elements'] if e['ref']==source),None)
   end=next((e for e in previous['elements'] if e['ref']==target),None)
   if not start or not end or source==target or not start.get('can_drag') or not end.get('can_drop'):
    raise ValueError('Choose an observed draggable item and a distinct drop target')
   args=json.dumps({'token':token,'url':previous['url'],'source':int(source[1:]),'target':int(target[1:]),'sf':start['fingerprint'],'tf':end['fingerprint']})
   script="(()=>{const a="+args+";"+r"""
    const s=globalThis.__rfObservation;
    if(!s||s.token!==a.token||location.href!==a.url)return {reason:'stale_observation'};
    const x=s.nodes[a.source],y=s.nodes[a.target];
    if(!x||!y||!s.visible(x)||!s.visible(y)||!s.canDrag(x)||!s.canDrop(y)||s.fingerprint(x)!==a.sf||s.fingerprint(y)!==a.tf)return {reason:'stale_observation'};
    const sensitive=/(?:^|[^\p{L}\p{N}_])(send|publish|submit|pay|buy|purchase|delete|erase|uninstall|approve|allow|отправить|оплатить|купить|удалить|опубликовать|разрешить)(?:[^\p{L}\p{N}_]|$)/iu;
    if(sensitive.test([s.label(x),s.label(y)].join(' ')))return {reason:'manual_confirmation_required'};
    const center=e=>{const r=e.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;const hit=document.elementFromPoint(x,y);return x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&hit&&(e===hit||e.contains(hit))?{x,y}:null};
    const from=center(x),to=center(y);if(!from||!to)return {reason:'target_not_in_view'};
    return {from,to};})()"""
   coords=self.evaluate(script,context_id=previous['context'])
   if 'reason' in coords:return {'acted':False,'reason':coords['reason']}
   a,b=coords['from'],coords['to']
   # Keep one CDP session alive throughout the gesture: disconnecting after
   # mousePressed causes Chromium to cancel native HTML drag-and-drop.
   import websocket
   with closing(websocket.create_connection(self.target,timeout=15,suppress_origin=True,http_proxy_host=None)) as ws:
    def mouse(params):
     self.counter+=1; key=self.counter
     ws.send(json.dumps({'id':key,'method':'Input.dispatchMouseEvent','params':params}))
     while True:
      reply=json.loads(ws.recv())
      if reply.get('id')==key:
       if 'error' in reply:raise RuntimeError('Browser mouse gesture failed: '+reply['error'].get('message','error'))
       return
    mouse({'type':'mouseMoved',**a})
    mouse({'type':'mousePressed',**a,'button':'left','buttons':1,'clickCount':1})
    try:
     for n in range(1,9):
      point={'x':a['x']+(b['x']-a['x'])*n/8,'y':a['y']+(b['y']-a['y'])*n/8}
      mouse({'type':'mouseMoved',**point,'button':'left','buttons':1})
      time.sleep(.025)
     mouse({'type':'mouseMoved',**b,'button':'left','buttons':1})
     time.sleep(.08)
    finally:
     mouse({'type':'mouseReleased',**b,'button':'left','buttons':0,'clickCount':1})
   return {'acted':True,'result':'drag_dispatched_verify_page'}
 def navigate(self,url):
  web_url(url)
  with self.lock:
   before=self.evaluate('location.href')
   r=self.command('Page.navigate',{'url':url})
   if r.get('errorText'):raise RuntimeError(r['errorText'])
   deadline=time.monotonic()+15
   while time.monotonic()<deadline:
    try:
     state=self.evaluate('({ready:document.readyState,url:location.href})')
     if (state['url']==url or state['url']!=before) and state['url']!='about:blank' and state['ready'] in ('interactive','complete'):break
    except (ValueError,RuntimeError):pass
    time.sleep(.1)
   else:raise RuntimeError('Navigation did not reach a readable document within 15 seconds')
   return self.inspect()
 def act(self,selector,text=None):
  if not isinstance(selector,str) or not 1<=len(selector)<=300:raise ValueError('Invalid selector')
  if text is not None and (not isinstance(text,str) or len(text)>10000):raise ValueError('Input text too long')
  with self.lock:
   q=json.dumps(selector)
   op="e.click();" if text is None else "if(!['INPUT','TEXTAREA'].includes(e.tagName)||e.type==='password')throw Error('Unsupported input');const d=Object.getOwnPropertyDescriptor(e.tagName==='INPUT'?HTMLInputElement.prototype:HTMLTextAreaElement.prototype,'value');d.set.call(e,"+json.dumps(text)+");e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));"
   result=self.evaluate("(()=>{const a=document.querySelectorAll("+q+");if(a.length!==1)throw Error('Selector must match one element');const e=a[0];if(!e.getClientRects().length||e.disabled)throw Error('Element unavailable');"+op+"return true})()")
   return {'acted':result,'observation':self.inspect()}
 def wait_for(self,selector,text=None,timeout=5,context_id=None):
  if not isinstance(selector,str) or not 1<=len(selector)<=300:raise ValueError('Invalid selector')
  if text is not None and (not isinstance(text,str) or not 1<=len(text)<=2000):raise ValueError('Expected text must contain 1-2000 characters')
  if type(timeout) not in (int,float) or not .1<=timeout<=10:raise ValueError('Wait timeout must be 0.1-10 seconds')
  with self.lock:
   if not self.target or not self.process or self.process.poll() is not None:raise ValueError('Open a managed browser page first')
   started=time.monotonic();deadline=started+timeout
   expression="(()=>{const selector="+json.dumps(selector)+";const expected="+json.dumps(text)+";"+"""const nodes=document.querySelectorAll(selector);if(nodes.length!==1)return {matched:false,reason:'Selector must match one element'};const e=nodes[0];const style=getComputedStyle(e);if(!e.getClientRects().length||e.hidden||style.visibility==='hidden'||style.display==='none')return {matched:false,reason:'Element is not visible'};if(e.type==='password')return {matched:false,reason:'Protected input'};const actual=String(['INPUT','TEXTAREA','SELECT'].includes(e.tagName)?e.value:e.innerText||'').slice(0,12000);return {matched:expected===null||actual.includes(expected),actual:actual.slice(0,500),reason:'Expected text not observed'};})()"""
   last={'reason':'Condition not observed'}
   while time.monotonic()<deadline:
    last=self.evaluate(expression,timeout=max(.1,min(2,deadline-time.monotonic())),context_id=context_id)
    if last and last.get('matched'):
     return {'verified':True,'condition':{'selector':selector,'contains_text':text},'actual':last.get('actual',''),
             'elapsed_seconds':round(time.monotonic()-started,3),'checked_at':time.time(),'observation':self.inspect()}
    time.sleep(min(.1,max(0,deadline-time.monotonic())))
   raise ValueError('Browser condition timed out: '+(last or {}).get('reason','Condition not observed'))
 def close(self):
  with self.lock:
   if self.target and self.process and self.process.poll() is None:
    try:self.command('Browser.close')
    except (RuntimeError,ValueError,OSError):pass
   if self.job:self.job.close();self.job=None
   if self.process:
    try:self.process.wait(timeout=3)
    except subprocess.TimeoutExpired:
     self.process.terminate()
     try:self.process.wait(timeout=2)
     except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=2)
   self.target=None;self.process=None
   return {'closed':True}
