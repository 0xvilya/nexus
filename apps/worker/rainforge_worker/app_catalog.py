"""On-demand Windows application inventory. User/model text is never a command."""
import base64
import ctypes
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import unicodedata
from pathlib import Path

# Enumerate registered desktop and Store applications, not arbitrary files.
# The fixed script receives no user text. Link targets are used for observation
# only; launching uses the exact identity returned by Windows AppsFolder.
INVENTORY_SCRIPT = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$shell=New-Object -ComObject Shell.Application
$folder=$shell.Namespace('shell:AppsFolder')
if($null -eq $folder){throw 'Windows application catalog unavailable'}
$items=$folder.Items()
$out=@(for($i=0;$i -lt [Math]::Min($items.Count,4096);$i++){
 $item=$items.Item($i)
 [pscustomobject]@{name=$item.Name;app_id=$item.Path;target=$item.ExtendedProperty('System.Link.TargetParsingPath');package=$item.ExtendedProperty('System.AppUserModel.PackageInstallPath')}
})
ConvertTo-Json -InputObject $out -Depth 3 -Compress
"""

ALIASES = {
 'телеграм':'telegram','телеграмм':'telegram','тг':'telegram',
 'телега':'telegram','телегу':'telegram','телеграмчик':'telegram','телеграмме':'telegram','дискордик':'discord',
 'стим':'steam','дискорд':'discord','спотифай':'spotify',
 'ватсап':'whatsapp','вотсап':'whatsapp','зум':'zoom',
 'блокнот':'notepad','калькулятор':'calculator','хром':'google chrome',
 'вс код':'visual studio code','вс коде':'visual studio code',
}
_RU = dict(zip('абвгдезийклмнопрстуфхц', ('a','b','v','g','d','e','z','i','y','k','l','m','n','o','p','r','s','t','u','f','h','ts')))
_RU.update({'ё':'e','ж':'zh','ч':'ch','ш':'sh','щ':'shch','ы':'y','э':'e','ю':'yu','я':'ya','ь':'','ъ':''})
_DANGEROUS = re.compile(r'(?i)(?:uninstall|unins\d*|setup|installer|удален|удаление|установщик|powershell|pwsh|command prompt|командная строка|windows terminal|regedit|wscript|cscript|mshta|rundll32)')

def normalize(value):
 return ' '.join(re.findall(r'[^\W_]+',unicodedata.normalize('NFKC',value).casefold().replace('ё','е')))

def variants(value):
 name=normalize(value)
 return {name,ALIASES.get(name,name),''.join(_RU.get(c,c) for c in name)}

def conversational_name(value):
 # Remove only leading conversational determiners, never words inside app names.
 return re.sub(r'^(?:(?:мой|мою|мо[её]|мои|my|the)\s+)+','',value.strip(),flags=re.I).strip().strip('«»"')

def process_image(pid):
 """Read-only identity check; never infer a launch from a browser tab title."""
 if os.name!='nt' or type(pid) is not int or pid<=0:return None
 kernel=ctypes.WinDLL('kernel32',use_last_error=True)
 kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
 kernel.OpenProcess.restype=ctypes.c_void_p
 kernel.QueryFullProcessImageNameW.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_uint32)]
 kernel.CloseHandle.argtypes=[ctypes.c_void_p]
 handle=kernel.OpenProcess(0x1000,False,pid)
 if not handle:return None
 try:
  size=ctypes.c_uint32(32768);buf=ctypes.create_unicode_buffer(size.value)
  return buf.value if kernel.QueryFullProcessImageNameW(handle,0,buf,ctypes.byref(size)) else None
 finally:kernel.CloseHandle(handle)

class AppCatalog:
 def __init__(self):
  self.lock=threading.RLock();self.entries=[];self.loaded_at=None

 def inventory(self):
  if os.name!='nt':raise ValueError('Каталог приложений доступен только в Windows')
  encoded=base64.b64encode(INVENTORY_SCRIPT.encode('utf-16-le')).decode('ascii')
  try:
   result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-STA','-EncodedCommand',encoded],capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  except (OSError,subprocess.TimeoutExpired) as exc:raise RuntimeError('Не удалось прочитать каталог приложений Windows. Повторите обновление.') from exc
  if result.returncode or len(result.stdout)>4_000_000:raise RuntimeError('Windows не вернула доступный каталог приложений')
  try:rows=json.loads(result.stdout.decode('utf-8-sig'))
  except (UnicodeError,ValueError) as exc:raise RuntimeError('Не удалось прочитать список приложений Windows') from exc
  if not isinstance(rows,list):raise RuntimeError('Некорректный каталог приложений Windows')
  return rows[:4096]

 def load(self,refresh=False):
  with self.lock:
   if refresh or self.loaded_at is None or time.monotonic()-self.loaded_at>60:
    entries={}
    for row in self.inventory():
     if not isinstance(row,dict):continue
     name=row.get('name');identity=row.get('app_id')
     if not isinstance(name,str) or not 1<=len(name)<=256 or not isinstance(identity,str) or not 1<=len(identity)<=1024:continue
     if any(ord(c)<32 for c in name+identity) or any(c in identity for c in ':"<>|?*'):continue
     target=row.get('target') if isinstance(row.get('target'),str) else ''
     package=row.get('package') if isinstance(row.get('package'),str) else ''
     if _DANGEROUS.search(name+' '+identity+' '+Path(target).stem) or Path(target).name.casefold() in {'cmd.exe','wt.exe'}:continue
     key='installed:'+hashlib.sha256(identity.casefold().encode()).hexdigest()[:24]
     entries[key]={'id':key,'name':name,'app_id':identity,'target':target,'package':package}
    self.entries=sorted(entries.values(),key=lambda e:e['name'].casefold());self.loaded_at=time.monotonic()
   return self.entries.copy()

 def listing(self,refresh=False):
  if type(refresh) is not bool:raise ValueError('refresh must be boolean')
  entries=self.load(refresh)
  return {'apps':[{'id':e['id'],'name':e['name']} for e in entries],
          'scope':'Windows AppsFolder: registered desktop and Store apps; unregistered portable apps are not indexed.'}

 def resolve(self,query,refresh_on_miss=True):
  if not isinstance(query,str) or not 1<=len(query)<=256 or any(c in query for c in '\\/:;\n\r&|<>'):raise ValueError('Укажите название установленного приложения, без команд и путей')
  entries=self.load()
  if query.startswith('installed:'):raise ValueError('Укажите название приложения')
  if not normalize(query):raise ValueError('Укажите название приложения')
  words=variants(query)
  exact=[e for e in entries if words & variants(e['name'])]
  matches=exact or [e for e in entries if any(' '+q+' ' in ' '+n+' ' for q in words for n in variants(e['name']))]
  if not matches and conversational_name(query)!=query:
   return self.resolve(conversational_name(query),refresh_on_miss)
  if not matches and refresh_on_miss:return self.resolve_after_refresh(query)
  if not matches:raise ValueError('Приложение «'+query+'» не найдено в каталоге Windows. Проверьте установку или название в разделе «Рабочий стол → Приложения». Браузер вместо него не запускался.')
  if len(matches)>1:raise ValueError('Найдено несколько приложений. Укажите полное название: '+', '.join(e['name'] for e in matches[:8]))
  return matches[0]

 def resolve_after_refresh(self,query):
  self.load(True)
  return self.resolve(query,False)

 def get(self,identity):
  if not isinstance(identity,str):raise ValueError('Invalid application identity')
  entry=next((e for e in self.load() if e['id']==identity),None)
  if not entry:raise ValueError('Приложение больше не доступно. Обновите каталог Windows.')
  return entry

 def launch(self,identity):
  entry=self.get(identity)
  # ShellExecute receives only a registered Windows identity from our inventory.
  # No shell command, user arguments, URLs or model-provided paths are accepted.
  os.startfile('shell:AppsFolder\\'+entry['app_id'])
  return {'app':identity,'name':entry['name'],'pid':None,'state':'launch_requested','verified_window':False}

 def observe(self,result,windows):
  entry=self.get(result['app'])
  target=entry['target'];package=entry['package']
  for window in windows:
   image=process_image(window.get('pid'))
   if not image:continue
   same=bool(target and os.path.normcase(image)==os.path.normcase(target))
   if package:
    try:same=same or Path(image).resolve().is_relative_to(Path(package).resolve())
    except OSError:pass
   if same:return {**result,'verified_window':True,'window':window,'state':'window_observed'}
  return result
