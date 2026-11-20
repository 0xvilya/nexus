"""Scoped text tools. Syntax checks do not execute user code."""
import ast
import hashlib
import json
import shutil
import os
import subprocess
import threading
from pathlib import Path
from rainforge_recovery import atomic_json
from .app_catalog import AppCatalog

def desktop_directory():
 if os.name!='nt':raise ValueError('Экспорт на рабочий стол поддерживается в Windows')
 import ctypes
 buffer=ctypes.create_unicode_buffer(32768)
 # CSIDL_DESKTOPDIRECTORY follows the actual per-user Desktop redirection.
 if ctypes.windll.shell32.SHGetFolderPathW(None,0x10,None,0,buffer)!=0:raise OSError('Не удалось определить рабочий стол пользователя')
 target=Path(buffer.value)
 if not target.is_absolute() or not target.is_dir():raise OSError('Рабочий стол недоступен')
 return target

class WorkspaceTools:
 def __init__(self,data):
  self.root=data/'coding-workspace';self.root.mkdir(parents=True,exist_ok=True)
  # Windows temp roots can use 8.3 aliases; use one canonical spelling for
  # containment checks, enumeration and paths returned to project tools.
  self.root=self.root.resolve();self.lock=threading.RLock();self.apps=AppCatalog()
 def path(self,path):
  if not isinstance(path,str) or not path or len(path)>240 or '\\' in path or ':' in path:raise ValueError('Use a relative path with forward slashes')
  p=Path(path)
  if p.is_absolute() or '..' in p.parts or any(x.startswith('.') for x in p.parts):raise ValueError('Path outside workspace')
  target=(self.root/p).resolve()
  if not target.is_relative_to(self.root.resolve()):raise ValueError('Path outside workspace')
  if target.suffix.lower() not in {'.py','.txt','.md','.json','.html','.css','.js','.mjs','.ts','.jsx','.tsx','.c','.cpp','.h','.hpp','.cs','.java','.rs','.go','.rb','.php','.sql','.yaml','.yml','.toml','.xml','.svg','.swift','.kt','.sh','.ps1'}:raise ValueError('Unsupported text format')
  return target
 def listing(self):
  return {'root':str(self.root),'files':[p.relative_to(self.root).as_posix() for p in self.root.rglob('*') if p.is_file() and p.resolve().is_relative_to(self.root.resolve())][:300]}
 def read(self,path):
  with self.lock:
   p=self.path(path)
   if p.stat().st_size>200000:raise ValueError('File exceeds 200 KB')
   raw=p.read_bytes();return {'path':path,'text':raw.decode('utf-8'),'sha256':hashlib.sha256(raw).hexdigest()}
 def write(self,path,text,expected_hash=None):
  if not isinstance(text,str) or len(text.encode('utf-8'))>200000:raise ValueError('Text exceeds 200 KB')
  with self.lock:
   p=self.path(path)
   if p.exists():
    current=self.read(path)
    if expected_hash!=current['sha256']:raise ValueError('File changed; read it before updating')
   elif expected_hash is not None:raise ValueError('File does not exist')
   p.parent.mkdir(parents=True,exist_ok=True)
   temporary=p.with_suffix(p.suffix+'.tmp');temporary.write_bytes(text.encode('utf-8'));os.replace(temporary,p)
   return self.read(path)
 def check(self,path):
  return self.check_source(path,self.read(path)['text'])
 def check_source(self,path,text):
  suffix=Path(path).suffix.lower()
  try:
   if suffix=='.py':ast.parse(text)
   elif suffix=='.json':json.loads(text)
   elif suffix in {'.js','.mjs'}:
    node=shutil.which('node')
    if not node or (os.name=='nt' and Path(node).suffix.lower()!='.exe'):return {'ok':False,'kind':'unsupported','message':'Node.js не найден; синтаксис JavaScript не проверен'}
    env={k:v for k,v in os.environ.items() if k.upper() not in {'NODE_OPTIONS','NODE_PATH'}}
    result=subprocess.run([node,'--input-type=module','--check','-'],input=text.encode('utf-8'),capture_output=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),env=env)
    if result.returncode:return {'ok':False,'kind':'syntax','message':result.stderr.decode('utf-8',errors='replace')[:800]}
   else:return {'ok':False,'kind':'unsupported','message':'Для этого языка проверка синтаксиса не подключена'}
   return {'ok':True,'kind':'syntax','message':'Синтаксис корректен. Выполнение и поведение не проверялись.'}
  except (SyntaxError,ValueError) as exc:return {'ok':False,'kind':'syntax','message':str(exc)}
  except (OSError,subprocess.TimeoutExpired) as exc:return {'ok':False,'kind':'unavailable','message':'Проверка синтаксиса не завершилась: '+str(exc)[:200]}
 def launch(self,app,url="https://duckduckgo.com/"):
  if os.name!='nt':raise RuntimeError('Windows required')
  if isinstance(app,str) and app.startswith('installed:'):return self.apps.launch(app)
  if app not in {'notepad','calculator','chrome','browser','steam'}:raise ValueError('Unsupported application')
  chrome=[Path(os.environ.get(k,''))/'Google/Chrome/Application/chrome.exe' for k in ('ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA') if os.environ.get(k)]
  edge=[Path(os.environ.get(k,''))/'Microsoft/Edge/Application/msedge.exe' for k in ('ProgramFiles(x86)','ProgramFiles','LOCALAPPDATA') if os.environ.get(k)]
  candidates=chrome if app=='chrome' else chrome+edge if app=='browser' else [Path(os.environ['SystemRoot'])/'System32'/('notepad.exe' if app=='notepad' else 'calc.exe')]
  if app=='steam':
   candidates=[Path(os.environ[k])/'Steam/steam.exe' for k in ('ProgramFiles(x86)','ProgramFiles','LOCALAPPDATA') if os.environ.get(k)]
   import winreg
   for hive,key,value,is_directory in [(winreg.HKEY_CURRENT_USER,r'Software\Valve\Steam','SteamExe',False),(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\WOW6432Node\Valve\Steam','InstallPath',True)]:
    try:
     with winreg.OpenKey(hive,key) as handle:installed=winreg.QueryValueEx(handle,value)[0]
     if isinstance(installed,str) and installed:
      candidate=Path(installed)/'steam.exe' if is_directory else Path(installed)
      if candidate.is_absolute() and candidate.name.lower()=='steam.exe':candidates.insert(0,candidate)
    except OSError:pass
  executable=next((p for p in candidates if p.is_file()),None)
  if executable is None:
   if app=='browser':
    from .actions import web_url
    os.startfile(web_url(url))
    return {'app':app,'executable':'Windows default browser','pid':None,'state':'launch_requested','verified_window':False}
   raise ValueError('Приложение не найдено. Автоматическая установка пока не подключена.')
  args=[str(executable)]
  if app in {'chrome','browser'}:args+=['--new-window',url]
  proc=subprocess.Popen(args,shell=False)
  return {'app':app,'executable':str(executable),'pid':proc.pid,'state':'launch_requested','verified_window':False}
 def execute(self,goal):
  import re
  if not isinstance(goal,str) or len(goal)>500:raise ValueError('Введите короткую команду')
  text=goal.strip().lower().rstrip('.! ')
  match=re.fullmatch(r'(?:открой|открыть|запусти|запустить|open|launch)\s+(гугл браузер|гугл|google chrome|google browser|хром|chrome|браузер|browser|блокнот|notepad|калькулятор|calculator)',text)
  if not match:raise ValueError('Команда пока не поддерживается. Можно: «открой гугл браузер», «открой блокнот», «открой калькулятор». Для заметки выберите «Создать заметку», для ответа модели — «Локальный чат».')
  name=match.group(1)
  app='chrome' if name in {'гугл браузер','гугл','google chrome','google browser','хром','chrome'} else 'browser' if name in {'браузер','browser'} else 'notepad' if name in {'блокнот','notepad'} else 'calculator'
  result=self.launch(app)
  return {**result,'message':'Отправлен запрос запуска: '+result['executable']+'. Открытие окна пока не подтверждено.'}
