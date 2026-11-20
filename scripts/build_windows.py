import argparse,hashlib,json,shutil,subprocess,sys,zipfile
from pathlib import Path
import bootstrap
from rainforge_native.download import download, sha256
from packaged_assets import include_offline_assets, include_worker_notices
ROOT=bootstrap.ROOT
parser=argparse.ArgumentParser(description="Build the full or Core Windows preview installer.")
parser.add_argument('--core',action='store_true',help="Build Core without optional neural speech or GPU runtime.")
options=parser.parse_args()
variant='core' if options.core else 'full'
SDK={'version':'1.0.4314.35','url':'https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/1.0.4314.35/microsoft.web.webview2.1.0.4314.35.nupkg','sha256':'90778deb1b77e36292828b4b5833c9255c27533f40e0ddd55891ae718944edc4','bytes':9818548}
work=ROOT/('work/windows-core-release' if options.core else 'work/windows-release');work.mkdir(parents=True,exist_ok=True)
pkg=download(SDK['url'],work/'webview2.nupkg',SDK['sha256'],SDK['bytes'])
sdk=work/'sdk';sdk.mkdir(exist_ok=True)
needed=['lib/net462/Microsoft.Web.WebView2.Core.dll','lib/net462/Microsoft.Web.WebView2.WinForms.dll','runtimes/win-x64/native/WebView2Loader.dll','LICENSE.txt']
with zipfile.ZipFile(pkg) as z:
 for name in needed:
  dest=sdk/Path(name).name;dest.write_bytes(z.read(name))
bundle=work/'bundle';bundle.mkdir(exist_ok=True)
if options.core and (bundle/'runtime/speech').exists():
 raise ValueError('Core staging contains optional speech assets. Use a clean Core staging directory.')
if options.core and (bundle/'runtime/vulkan').exists():
 raise ValueError('Core staging contains a GPU runtime. Use a clean Core staging directory.')
csc=Path(__import__('os').environ['SystemRoot'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
def compile(source,out,references,extra=(),additional_sources=()):
 args=['/nologo','/target:winexe','/platform:x64','/optimize','/out:"'+str(out)+'"']+['/r:"'+str(r)+'"' for r in references]+list(extra)+['"'+str(path)+'"' for path in (source,*additional_sources)]
 rsp=work/(out.stem+'.rsp');rsp.write_text('\n'.join(args),encoding='utf-8-sig')
 result=subprocess.run([str(csc),'@'+str(rsp)],capture_output=True)
 if result.returncode:raise RuntimeError(result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace'))
compile(ROOT/'apps/windows-native/Program.cs',bundle/'RainForge.exe',['System.Windows.Forms.dll','System.Drawing.dll','System.Net.Http.dll','System.Web.Extensions.dll',sdk/'Microsoft.Web.WebView2.Core.dll',sdk/'Microsoft.Web.WebView2.WinForms.dll'])
speech=Path(__import__('os').environ['SystemRoot'])/'Microsoft.NET/assembly/GAC_MSIL/System.Speech/v4.0_4.0.0.0__31bf3856ad364e35/System.Speech.dll'
compile(ROOT/'apps/windows-native/Pets.cs',bundle/'NexusPets.exe',['System.Windows.Forms.dll','System.Drawing.dll','System.Net.Http.dll','System.Web.Extensions.dll',speech],additional_sources=(ROOT/'apps/windows-native/LocalVoice.cs',))
shutil.copy2(ROOT/'apps/windows-native/Pets.config',bundle/'NexusPets.exe.config')
for name in ['Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll']:shutil.copy2(sdk/name,bundle/name)
shutil.copy2(sdk/'LICENSE.txt',bundle/'LICENSE-WebView2.txt')
shutil.copy2(ROOT/'apps/desktop/src-tauri/binaries/rainforge-worker-x86_64-pc-windows-msvc.exe',bundle/'rainforge-worker.exe')
runtime_sources=[(ROOT/'apps/desktop/dist',bundle/'ui'),(ROOT/'runtime/bin',bundle/'runtime/bin')]
if not options.core:runtime_sources.append((ROOT/'runtime/vulkan',bundle/'runtime/vulkan'))
for source,dest in runtime_sources:
 if source.exists():shutil.copytree(source,dest,dirs_exist_ok=True)
shutil.copy2(ROOT/'runtime/catalog.json',bundle/'runtime/catalog.json')
shutil.copy2(ROOT/'LICENSE',bundle/'LICENSE')
shutil.copy2(ROOT/'docs/WEBSOCKET_CLIENT_LICENSE.txt',bundle/'LICENSE-websocket-client.txt')
include_offline_assets(ROOT,bundle,include_neural_speech=not options.core)
include_worker_notices(ROOT,bundle)
(bundle/'BUILD_VARIANT.json').write_text(json.dumps({
 'variant':variant,'platform':'windows-x64','neural_speech_bundled':not options.core,
 'speech_fallback':'Installed Windows SAPI voices','signed':False,
},indent=2)+'\n',encoding='utf-8')
if options.core:shutil.copy2(ROOT/'docs/WINDOWS_CORE.md',bundle/'WINDOWS_CORE.txt')
manifest={p.relative_to(bundle).as_posix():sha256(p) for p in bundle.rglob('*') if p.is_file()}
payload=work/'payload.zip'
with zipfile.ZipFile(payload,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
 for name in manifest:z.write(bundle/name,name)
 z.writestr('payload-manifest.json',json.dumps(manifest,sort_keys=True))
out=ROOT/('outputs/windows-core' if options.core else 'outputs/windows');out.mkdir(parents=True,exist_ok=True)
compile(ROOT/'apps/windows-native/Setup.cs',out/'RainForgeSetup.exe',['System.Windows.Forms.dll','System.Drawing.dll','System.Web.Extensions.dll','System.IO.Compression.dll','System.IO.Compression.FileSystem.dll'],['/resource:"'+str(payload)+'",payload.zip'])
report={'variant':variant,'sdk':SDK,'payload_files':len(manifest),'payload_bytes':sum((bundle/f).stat().st_size for f in manifest),'installer_bytes':(out/'RainForgeSetup.exe').stat().st_size,'installer_sha256':hashlib.sha256((out/'RainForgeSetup.exe').read_bytes()).hexdigest()}
(out/'build.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
