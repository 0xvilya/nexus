import sys,json,zipfile,pathlib
import bootstrap
from rainforge_native.download import download,sha256
from rainforge_recovery import atomic_json
root=bootstrap.ROOT/'runtime'; c=json.loads((root/'catalog.json').read_text(encoding='utf-8')); r=c['backends']['vulkan']; a=download(r['url'],root/'cache/vulkan.zip',r['sha256'],r['bytes']); d=root/'vulkan'; d.mkdir(exist_ok=True)
with zipfile.ZipFile(a) as z:
 for e in z.infolist():
  name=pathlib.Path(e.filename).name
  if name.endswith('.dll') or name=='llama-server.exe': (d/name).write_bytes(z.read(e))
atomic_json(d/'manifest.json',{'archive_sha256':r['sha256'],'files':{f.name:sha256(f) for f in d.iterdir() if f.suffix in ['.exe','.dll']}})
print('Vulkan runtime ready',flush=True)
