"""Adapters to existing local tools; no generic process or filesystem connector."""
import json,os,shutil,subprocess,tempfile
from pathlib import Path
from .hub import connector_query

LOCAL_PROVIDERS={'local_filesystem','playwright','web_search','docker'}

class LocalConnections:
 def __init__(self,worker):self.worker=worker
 def _docker(self,listing):
  executable=shutil.which('docker')
  if not executable or (os.name=='nt' and Path(executable).suffix.lower()!='.exe'):raise ValueError('Docker CLI not installed. Install and authorize Docker Desktop separately.')
  args=['ps','--last','20','--format','{"id":{{json .ID}},"name":{{json .Names}},"image":{{json .Image}},"state":{{json .State}}}'] if listing else ['version','--format','{{.Server.Version}}']
  with tempfile.TemporaryFile() as output:
   try:result=subprocess.run([executable,*args],stdout=output,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,shell=False,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
   except (OSError,subprocess.TimeoutExpired):raise ValueError('Docker did not respond in time; check your current Docker context.') from None
   if result.returncode:raise ValueError('Docker unavailable or permission denied. Check Docker Desktop and your current context.')
   if output.tell()>65536:raise ValueError('Docker response exceeded limit')
   output.seek(0);raw=output.read().decode('utf-8',errors='replace')
  if not listing:return [{'id':'server','name':'Docker '+raw.strip()[:100],'description':'Server version verified in your current Docker context.'}]
  rows=[]
  for line in raw.splitlines()[:20]:
   data=json.loads(line);rows.append({'id':str(data.get('id',''))[:100],'name':str(data.get('name',''))[:160],'description':str(data.get('image',''))[:200]+' · '+str(data.get('state',''))[:80]})
  return rows
 def test(self,provider):
  if provider=='local_filesystem':self.worker.workspace_tools.listing();message='Scoped coding workspace is readable; other local folders are not granted.'
  elif provider=='playwright':self.worker.manual_browser('start');message='Existing managed browser started locally on about:blank. External pages were not opened.'
  elif provider=='web_search':message='Public encyclopedia search adapter is configured. Network availability has not been tested; use a query to test it.'
  elif provider=='docker':self._docker(False);message='Docker server answered through your installed CLI and current context.'
  else:raise ValueError('Unknown local connector')
  return {'ok':True,'verified':provider!='web_search','message':message}
 def read(self,provider,query=''):
  if not isinstance(query,str) or len(query)>200:raise ValueError('Query limit:200 characters')
  if provider=='local_filesystem':rows=[{'id':name,'name':name,'description':'File in explicitly scoped coding workspace; contents not read.'} for name in self.worker.workspace_tools.listing()['files'] if query.casefold() in name.casefold()][:20]
  elif provider=='playwright':
   if not self.worker.browser.target:raise ValueError('Open a page in Browser first, or use Test connection to start the managed browser.')
   state=self.worker.manual_browser('inspect');rows=[{'id':'page','name':str(state.get('title','Current page'))[:160],'description':str(state.get('text',''))[:3000],'url':state.get('url','')}]
  elif provider=='web_search':
   if not query.strip():raise ValueError('Enter a search query')
   rows=connector_query('wikipedia',query.strip())
  elif provider=='docker':rows=[r for r in self._docker(True) if query.casefold() in (r['name']+' '+r['description']).casefold()]
  else:raise ValueError('Unknown local connector')
  return {'provider':provider,'items':rows,'read_only':True,'limited':True}
