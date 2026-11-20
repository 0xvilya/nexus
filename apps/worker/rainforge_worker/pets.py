"""Local pet preferences, safe media packages and bounded lifecycle notifications.

No pet package contains executable code. Events describe controller state, never
grant tool permissions and never retain prompts, generated text or credentials.
"""
from __future__ import annotations
import base64
from collections import deque, OrderedDict
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import threading
import time
from uuid import uuid4
import zlib

from rainforge_recovery import atomic_json

BUILTINS = ('nexus-bot', 'pixel-cat', 'cyber-fox', 'mini-dragon', 'nexus-ghost')
STATES = {'idle','thinking','coding','searching','testing','success','error','waiting','sleeping','talking'}
DEFAULTS = dict(enabled=False,character='nexus-bot',size=128,speed=1,opacity=1,
                always_on_top=True,hide_fullscreen=True,sounds=False,reactions=True,
                show_status=True,voice=True,fps=30,power_saving=True,
                start_with_nexus=True,start_with_windows=False,x=None,y=None,theme='dark')
MAX_BYTES=8*1024*1024
MAX_FILES=32
MAX_DECODED_PIXELS=16*1024*1024


def _dimensions(width,height):
    if not 1<=width<=4096 or not 1<=height<=4096 or width*height>4*1024*1024:
        raise ValueError('Pet image limit: 4096 pixels per side and 4 million pixels')
    return width,height


def image_info(data):
    """Validate container structure before passing bounded media to OS decoders."""
    if not isinstance(data,bytes) or not 12<=len(data)<=MAX_BYTES:
        raise ValueError('Invalid or oversized pet image')
    animated=False
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        offset=8;seen=[];compressed=bytearray();width=height=0;expected=None;apng_frames=0;frame_controls=0;row_bytes=None
        while offset<len(data):
            if len(data)-offset<12:raise ValueError('Truncated PNG chunk')
            length=struct.unpack('>I',data[offset:offset+4])[0];kind=data[offset+4:offset+8]
            end=offset+12+length
            if end>len(data) or length>MAX_BYTES:raise ValueError('Truncated PNG payload')
            body=data[offset+8:offset+8+length]
            if zlib.crc32(kind+body)&0xffffffff!=struct.unpack('>I',data[end-4:end])[0]:raise ValueError('PNG checksum mismatch')
            if not seen and kind!=b'IHDR':raise ValueError('PNG must start with IHDR')
            if kind==b'IHDR':
                if seen or length!=13:raise ValueError('Invalid PNG header')
                width,height,depth,color,compression,filtering,interlace=struct.unpack('>IIBBBBB',body)
                _dimensions(width,height)
                valid={0:{1,2,4,8,16},2:{8,16},3:{1,2,4,8},4:{8,16},6:{8,16}}
                if depth not in valid.get(color,set()) or compression or filtering or interlace not in (0,1):raise ValueError('Unsupported PNG format')
                # Adam7 has bounded extra row filters; this is a strict upper bound.
                row_bytes=(width*{0:1,2:3,3:1,4:2,6:4}[color]*depth+7)//8
                expected=row_bytes*height+height*7+1024
            elif kind==b'IDAT':compressed.extend(body)
            elif kind==b'acTL':
                if length!=8 or not 1<=struct.unpack('>I',body[:4])[0]<=128:raise ValueError('APNG frame limit: 128')
                if apng_frames or b'IDAT' in seen:raise ValueError('Invalid APNG frame declaration')
                apng_frames=struct.unpack('>I',body[:4])[0];animated=True
                if apng_frames*width*height>MAX_DECODED_PIXELS:raise ValueError('Pet decoded animation budget: 16 million frame pixels')
            elif kind==b'fcTL':
                if not apng_frames or length!=26:raise ValueError('Invalid APNG frame control')
                _,w,h,left,top,_,_,dispose,blend=struct.unpack('>IIIIIHHBB',body)
                if not w or not h or left+w>width or top+h>height or dispose>2 or blend>1:raise ValueError('APNG frame outside canvas')
                frame_controls+=1
            elif kind==b'fdAT':
                if not apng_frames or length<5 or not frame_controls:raise ValueError('Invalid APNG frame data')
            elif kind in {b'iCCP',b'zTXt',b'iTXt',b'eXIf'}:
                raise ValueError('Compressed/private image metadata is not used by pets; re-export this image without metadata')
            elif kind==b'IEND':
                if length or end!=len(data):raise ValueError('Invalid PNG ending')
            elif kind not in {b'PLTE',b'tRNS',b'cHRM',b'gAMA',b'iCCP',b'sBIT',b'sRGB',b'bKGD',b'hIST',b'pHYs',b'sPLT',b'tIME',b'iTXt',b'tEXt',b'zTXt',b'fcTL',b'fdAT',b'eXIf'} and not kind[0]&32:
                raise ValueError('Unknown critical PNG chunk')
            seen.append(kind);offset=end
        if not seen or seen[-1]!=b'IEND' or not compressed or (apng_frames and apng_frames!=frame_controls):raise ValueError('Incomplete PNG/APNG')
        try:
            decoder=zlib.decompressobj();decoded=decoder.decompress(bytes(compressed),expected+1)
            if len(decoded)>expected or decoder.unconsumed_tail or decoder.unused_data or not decoder.eof:raise ValueError('PNG pixel payload exceeds its dimensions')
            if not interlace and (len(decoded)!=(row_bytes+1)*height or any(decoded[i]>4 for i in range(0,len(decoded),row_bytes+1))):raise ValueError('Incomplete PNG scanlines')
        except zlib.error as exc:raise ValueError('Invalid PNG pixel data') from exc
        return {'width':width,'height':height,'format':'png','mime':'image/png','animated':animated,'frames':apng_frames or 1,'decoded_pixels':width*height*(apng_frames or 1)}
    if data[:6] in (b'GIF87a',b'GIF89a'):
        if len(data)<14:raise ValueError('Truncated GIF')
        width,height=struct.unpack('<HH',data[6:10]);_dimensions(width,height)
        flags=data[10];offset=13+(3*(2**((flags&7)+1)) if flags&128 else 0);frames=0
        def subblocks(pos):
            while True:
                if pos>=len(data):raise ValueError('Truncated GIF blocks')
                count=data[pos];pos+=1
                if not count:return pos
                pos+=count
                if pos>len(data):raise ValueError('Truncated GIF block')
        while offset<len(data):
            marker=data[offset];offset+=1
            if marker==0x3b:
                if offset!=len(data) or not frames:raise ValueError('Invalid GIF ending')
                break
            if marker==0x21:
                if offset>=len(data):raise ValueError('Truncated GIF extension')
                offset=subblocks(offset+1)
            elif marker==0x2c:
                if len(data)-offset<10:raise ValueError('Truncated GIF frame')
                left,top,w,h,flags=struct.unpack('<HHHHB',data[offset:offset+9]);offset+=9
                if not w or not h or left+w>width or top+h>height:raise ValueError('GIF frame outside canvas')
                if flags&128:offset+=3*2**((flags&7)+1)
                if offset>=len(data) or not 2<=data[offset]<=8:raise ValueError('Invalid GIF image code')
                offset=subblocks(offset+1);frames+=1
                if frames>128:raise ValueError('GIF frame limit: 128')
                if width*height*frames>MAX_DECODED_PIXELS:raise ValueError('Pet decoded animation budget: 16 million frame pixels')
            else:raise ValueError('Invalid GIF record')
        else:raise ValueError('Missing GIF trailer')
        return {'width':width,'height':height,'format':'gif','mime':'image/gif','animated':frames>1,'frames':frames,'decoded_pixels':width*height*frames}
    if data[:4]==b'RIFF' and data[8:12]==b'WEBP':
        if struct.unpack('<I',data[4:8])[0]+8!=len(data):raise ValueError('Invalid WebP length')
        offset=12;width=height=0;frames=0;pixel_chunk=False
        while offset<len(data):
            if len(data)-offset<8:raise ValueError('Truncated WebP chunk')
            kind=data[offset:offset+4];length=struct.unpack('<I',data[offset+4:offset+8])[0];end=offset+8+length;body=data[offset+8:end]
            if end>len(data):raise ValueError('Truncated WebP payload')
            if kind==b'VP8X':
                if length!=10:raise ValueError('Invalid WebP canvas')
                width=int.from_bytes(body[4:7],'little')+1;height=int.from_bytes(body[7:10],'little')+1;animated=bool(body[0]&2)
            elif kind==b'VP8 ':
                if length<10 or body[3:6]!=b'\x9d\x01\x2a':raise ValueError('Invalid WebP VP8 header')
                if not width:width,height=struct.unpack('<HH',body[6:10]);width&=0x3fff;height&=0x3fff
                pixel_chunk=True
            elif kind==b'VP8L':
                if length<5 or body[0]!=0x2f:raise ValueError('Invalid WebP lossless header')
                packed=int.from_bytes(body[1:5],'little')
                if not width:width=(packed&0x3fff)+1;height=((packed>>14)&0x3fff)+1
                pixel_chunk=True
            elif kind==b'ANMF':
                frames+=1
                if length<16 or frames>128:raise ValueError('WebP frame limit: 128')
            elif kind not in {b'ANIM',b'ALPH',b'ICCP',b'EXIF',b'XMP '}:
                raise ValueError('Unsupported WebP chunk')
            offset=end+(length&1)
        if offset!=len(data) or not (pixel_chunk or (animated and frames)):raise ValueError('Incomplete WebP')
        _dimensions(width,height)
        if width*height*max(1,frames)>MAX_DECODED_PIXELS:raise ValueError('Pet decoded animation budget: 16 million frame pixels')
        return {'width':width,'height':height,'format':'webp','mime':'image/webp','animated':animated,'frames':max(1,frames),'decoded_pixels':width*height*max(1,frames)}
    raise ValueError('Only PNG, GIF or WebP pet images are supported')


class Pets:
    def __init__(self,data):
        self.root=Path(data)/'pets';self.root.mkdir(parents=True,exist_ok=True)
        self.packages_root=self.root/'packages';self.packages_root.mkdir(exist_ok=True)
        self.condition=threading.Condition(threading.RLock());self.events=deque(maxlen=128);self.activity=OrderedDict();self.sequence=0;self.closed=False;self.upload=None
        self.settings=dict(DEFAULTS);self.settings_warning=None;self.package_warnings=[]
        path=self.root/'settings.json'
        if path.exists():
            try:self.settings.update(self._settings(json.loads(path.read_text(encoding='utf-8'))))
            except (ValueError,OSError):self.settings_warning='Pet preferences could not be read; defaults are active. The original file was preserved.'
    def _settings(self,settings):
        if not isinstance(settings,dict) or set(settings)-set(DEFAULTS):raise ValueError('Unknown pet settings')
        for key,value in settings.items():
            if type(DEFAULTS[key]) is bool:
                if type(value) is not bool:raise ValueError('Pet setting '+key+' must be boolean')
            elif key in ('size','fps'):
                if type(value) is not int or (key=='size' and not 64<=value<=256) or (key=='fps' and value not in (15,30,60)):raise ValueError('Invalid pet size or FPS')
            elif key in ('speed','opacity'):
                if type(value) not in (int,float) or not math.isfinite(value) or not (.25<=value<=(2 if key=='speed' else 1)):raise ValueError('Invalid animation speed or opacity')
            elif key in ('x','y'):
                if value is not None and (type(value) is not int or not -100000<=value<=100000):raise ValueError('Invalid pet position')
            elif key=='character':
                if not isinstance(value,str) or (value not in BUILTINS and not (value.startswith('imported:') and re.fullmatch('[0-9a-f]{32}',value[9:]) and (self.packages_root/value[9:]/'manifest.json').is_file())):raise ValueError('Unknown pet character')
            elif key=='theme':
                if value not in ('dark','light'):raise ValueError('Pet theme must match dark or light appearance')
        return dict(settings)
    def _emit(self,event):
        with self.condition:
            self.sequence+=1;entry={**event,'sequence':self.sequence,'updated_at':time.time()};self.events.append(entry);self.condition.notify_all();return entry
    def track(self,kind,record,event=None):
        state=str(record.get('state','idle'));run_id=str(record.get('id','main'))[:80]
        # Titles intentionally describe the operation, not user prompts/source.
        title={'action':'Task controller','chat':'Local model reply','agent':'Agent team','repair':'Project repair','tests':'Project tests','browser':'Browser agent','model':'Local model'}.get(kind,'Nexus activity')
        if event is None:
            event={'queued':'agent.started','routing':'agent.thinking','answering':'agent.thinking','planning':'agent.thinking','running':'agent.thinking','executing':'agent.thinking','generating':'agent.coding' if kind=='repair' else 'agent.thinking','verifying':'agent.testing','acting':'agent.searching','observing':'agent.searching','passed':'agent.completed','checks_passed':'agent.completed','file_created':'agent.completed','project_created':'agent.completed','completed':'agent.completed','failed':'agent.failed','cancelled':'agent.stopped','interrupted':'agent.stopped','partial':'agent.stopped','observed':'agent.stopped','launch_requested':'agent.stopped','answered':'agent.stopped','awaiting_permission':'agent.waiting_approval','awaiting_manual':'agent.waiting_approval','pending_review':'agent.waiting_approval','pending_review_confirmation':'agent.waiting_approval'}.get(state,'agent.stopped')
            if kind=='action':
                tool=record.get('in_flight',{}).get('tool')
                if state in {'executing','generating'} and tool in {'create_python','create_project'}:event='agent.coding'
                elif state=='executing' and tool in {'search_web','open_url','browser_read'}:event='agent.searching'
                if state=='pending_review':event='agent.waiting_approval'
            if kind=='repair' and state=='running':
                event='agent.testing' if 'тест' in str(record.get('message','')).lower() else 'agent.coding'
        if not event.startswith(('agent.','model.')):raise ValueError('Invalid internal pet event')
        with self.condition:
            key=kind+':'+run_id;old=self.activity.get(key)
            if old and old['state']==state and old['event']==event:return
            entry={'id':key,'run_id':run_id,'kind':kind,'title':title,'state':state,'event':event,'updated_at':time.time()}
            self.activity[key]=entry;self.activity.move_to_end(key)
            while len(self.activity)>64:self.activity.popitem(last=False)
            self._emit({**entry,'type':event})
    def packages(self):
        result=[];warnings=[]
        for path in sorted(self.packages_root.glob('*/manifest.json'))[:32]:
            if path.is_symlink() or path.parent.is_symlink() or not re.fullmatch('[0-9a-f]{32}',path.parent.name):continue
            try:
                if path.stat().st_size>65536:raise ValueError('Manifest limit exceeded')
                manifest=self._manifest(json.loads(path.read_text(encoding='utf-8')))
            except (ValueError,OSError):
                warnings.append('An invalid local pet package was skipped: '+path.parent.name);continue
            result.append({'id':path.parent.name,'name':manifest['name'],'author':manifest.get('author',''),'license':manifest.get('license',''),'manifest':manifest})
        self.package_warnings=warnings
        return result
    def _snapshot(self,after=0):
        packages=self.packages()
        return copy.deepcopy({'sequence':self.sequence,'events':[e for e in self.events if e['sequence']>after],'settings':self.settings,'settings_warning':self.settings_warning,'package_warnings':self.package_warnings,'activity':list(reversed(self.activity.values())),'packages':packages})
    def get(self):
        with self.condition:return self._snapshot()
    def wait(self,after=0,timeout=20):
        if type(after) is not int or after<0 or type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<=timeout<=20:raise ValueError('Invalid pet subscription cursor or timeout')
        with self.condition:
            if after==self.sequence and not self.closed:self.condition.wait_for(lambda:self.closed or self.sequence!=after,timeout)
            return self._snapshot(after)
    def update(self,settings):
        validated=self._settings(settings)
        with self.condition:
            next_settings={**self.settings,**validated}
            atomic_json(self.root/'settings.json',next_settings);self.settings=next_settings;self.settings_warning=None;self._emit({'type':'settings.changed'});return self._snapshot()
    def host_request(self,action):
        if action not in ('open','settings'):raise ValueError('Unknown Nexus window request')
        self._emit({'type':'host.request','action':action});return {'requested':True}
    def _manifest(self,manifest):
        if not isinstance(manifest,dict) or set(manifest)-{'name','author','license','frame_width','frame_height','states'}:raise ValueError('Unknown pet manifest fields; scripts are not supported')
        for key,limit in (('name',80),('author',120),('license',300)):
            value=manifest.get(key,'' if key!='name' else None)
            if not isinstance(value,str) or len(value)>limit or (key=='name' and not value.strip()) or any(ord(c)<32 for c in value):raise ValueError('Invalid pet '+key)
        for key in ('frame_width','frame_height'):
            if type(manifest.get(key)) is not int or not 1<=manifest[key]<=512:raise ValueError('Pet frame dimensions must be 1-512 pixels')
        states=manifest.get('states')
        if not isinstance(states,dict) or 'idle' not in states or set(states)-STATES:raise ValueError('Pet states require idle and known animation names')
        for state,animation in states.items():
            if not isinstance(animation,dict) or set(animation)!={'file','frames','fps'}:raise ValueError('Animation requires file, frames and fps')
            self._filename(animation['file'])
            frames=animation['frames']
            if not isinstance(frames,list) or not 1<=len(frames)<=256 or any(type(i) is not int or not 0<=i<=65535 for i in frames):raise ValueError('Invalid sprite frame sequence')
            if type(animation['fps']) not in (int,float) or not math.isfinite(animation['fps']) or not 1<=animation['fps']<=60:raise ValueError('Animation FPS must be 1-60')
        return copy.deepcopy(manifest)
    def _filename(self,name):
        if not isinstance(name,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\.(?:png|gif|webp)',name) or '..' in name:raise ValueError('Pet media requires safe PNG/GIF/WebP basenames')
        if re.fullmatch(r'(?:con|prn|aux|nul|com[1-9]|lpt[1-9])',name.split('.',1)[0],re.I):raise ValueError('Windows device names are not pet filenames')
    def import_package(self,manifest,files):
        manifest=self._manifest(manifest)
        if not isinstance(files,dict) or not 1<=len(files)<=MAX_FILES:raise ValueError('Pet package limit: 32 image files')
        decoded={};infos={};size=0;names=set()
        for name,value in files.items():
            self._filename(name)
            if name.casefold() in names:raise ValueError('Duplicate pet filename')
            names.add(name.casefold())
            if not isinstance(value,str) or len(value)>MAX_BYTES*4//3+4:raise ValueError('Oversized pet image')
            try:data=base64.b64decode(value,validate=True)
            except (ValueError,TypeError) as exc:raise ValueError('Invalid pet image encoding') from exc
            size+=len(data)
            if size>MAX_BYTES:raise ValueError('Pet package limit: 8 MiB')
            info=image_info(data)
            if Path(name).suffix[1:]!=info['format']:raise ValueError('Pet extension does not match file contents')
            decoded[name]=data;infos[name]=info
            if sum(i['decoded_pixels'] for i in infos.values())>MAX_DECODED_PIXELS:raise ValueError('Pet package decoded budget: 16 million frame pixels')
        for animation in manifest['states'].values():
            if animation['file'] not in infos:raise ValueError('Animation file is missing')
            info=infos[animation['file']];w,h=manifest['frame_width'],manifest['frame_height']
            if info['width']%w or info['height']%h:raise ValueError('Sprite dimensions must be multiples of the frame size')
            count=(info['width']//w)*(info['height']//h)
            if max(animation['frames'])>=count:raise ValueError('Sprite frame is outside the image')
        with self.condition:
            if len(list(self.packages_root.iterdir()))>=32:raise ValueError('Pet library limit: 32 imported packages, including invalid local packages')
            key=uuid4().hex;stage=self.root/('pending-'+key);stage.mkdir()
            try:
                for name,data in decoded.items():(stage/name).write_bytes(data)
                atomic_json(stage/'manifest.json',manifest);stage.rename(self.packages_root/key)
            finally:
                if stage.exists():shutil.rmtree(stage)
            self._emit({'type':'pets.packages'});return next(p for p in self.packages() if p['id']==key)
    def asset(self,package_id,path):
        self._filename(path)
        if not isinstance(package_id,str) or not re.fullmatch('[0-9a-f]{32}',package_id):raise ValueError('Invalid pet package')
        root=self.packages_root/package_id;target=root/path
        if root.is_symlink() or (root/'manifest.json').is_symlink() or target.is_symlink() or not target.is_file() or target.stat().st_size>MAX_BYTES:raise ValueError('Pet image unavailable')
        data=target.read_bytes();info=image_info(data)
        return {'data':base64.b64encode(data).decode(),**info}
    def remove(self,package_id):
        if not isinstance(package_id,str) or not re.fullmatch('[0-9a-f]{32}',package_id):raise ValueError('Invalid pet package')
        with self.condition:
            target=self.packages_root/package_id
            if target.is_symlink() or not target.is_dir():raise ValueError('Pet package unavailable')
            if self.settings['character']=='imported:'+package_id:self.update({'character':'nexus-bot'})
            shutil.rmtree(target);self._emit({'type':'pets.packages'});return {'removed':True}
    def upload_begin(self,manifest,files):
        manifest=self._manifest(manifest)
        if not isinstance(files,list) or not 1<=len(files)<=MAX_FILES:raise ValueError('Pet package limit: 32 files')
        sizes={}
        for item in files:
            if not isinstance(item,dict) or set(item)!={'name','size'}:raise ValueError('Invalid pet upload file')
            self._filename(item['name'])
            if type(item['size']) is not int or not 1<=item['size']<=MAX_BYTES or item['name'].casefold() in {n.casefold() for n in sizes}:raise ValueError('Invalid pet upload size or name')
            sizes[item['name']]=item['size']
        if sum(sizes.values())>MAX_BYTES:raise ValueError('Pet package limit: 8 MiB')
        with self.condition:
            if self.upload and time.monotonic()-self.upload['updated']<300:raise ValueError('Finish or cancel the current pet upload')
            self.upload={'id':uuid4().hex,'manifest':manifest,'sizes':sizes,'files':{name:bytearray() for name in sizes},'updated':time.monotonic()};return {'upload_id':self.upload['id']}
    def _upload(self,upload_id):
        if not self.upload or upload_id!=self.upload['id'] or time.monotonic()-self.upload['updated']>300:
            self.upload=None;raise ValueError('Pet upload unavailable or expired')
        return self.upload
    def upload_chunk(self,upload_id,name,offset,data):
        if not isinstance(data,str) or len(data)>65536:raise ValueError('Pet upload chunk limit: 48 KiB')
        try:chunk=base64.b64decode(data,validate=True)
        except (ValueError,TypeError) as exc:raise ValueError('Invalid pet upload chunk') from exc
        with self.condition:
            upload=self._upload(upload_id)
            if name not in upload['files'] or type(offset) is not int or offset!=len(upload['files'][name]) or not chunk or offset+len(chunk)>upload['sizes'][name]:raise ValueError('Pet upload offset/size mismatch')
            upload['files'][name].extend(chunk);upload['updated']=time.monotonic();return {'offset':len(upload['files'][name])}
    def upload_finish(self,upload_id):
        with self.condition:
            upload=self._upload(upload_id)
            if any(len(upload['files'][n])!=size for n,size in upload['sizes'].items()):raise ValueError('Pet upload is incomplete')
            result=self.import_package(upload['manifest'],{n:base64.b64encode(b).decode() for n,b in upload['files'].items()});self.upload=None;return result
    def upload_cancel(self,upload_id):
        with self.condition:
            self._upload(upload_id);self.upload=None;return {'cancelled':True}
    def close(self):
        with self.condition:self.closed=True;self.upload=None;self.condition.notify_all()


def notify(worker,kind,record,event=None):
    service=getattr(worker,'pets',None)
    if isinstance(service,Pets):service.track(kind,record,event)
