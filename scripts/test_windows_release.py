"""Real per-user installer lifecycle test in a uniquely owned temporary target."""
import argparse,ctypes,json,os,subprocess,tempfile,time,uuid
from pathlib import Path
import bootstrap
ROOT=bootstrap.ROOT
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--core',action='store_true')
options=parser.parse_args()
work=ROOT/('work/windows-core-release-tests' if options.core else 'work/windows-release-tests');work.mkdir(parents=True,exist_ok=True)
target=Path(tempfile.gettempdir())/('RainForgeInstallTest-'+uuid.uuid4().hex)
setup=ROOT/('outputs/windows-core/RainForgeSetup.exe' if options.core else 'outputs/windows/RainForgeSetup.exe')
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def run(args,report):
 p=subprocess.run(args,timeout=90)
 data=read(report)
 assert p.returncode==0 and data['status']=='PASS',data
 return data
installed=run([str(setup),'--test-install',str(target),'--report',str(work/'install.json')],work/'install.json')
assert (target/'RainForge.exe').is_file()
assert (target/'LICENSE-Python.txt').is_file()
assert (target/'LICENSE-PyInstaller.txt').is_file()
assert read(target/'WORKER_NOTICES.json')['pyinstaller_version']
assert not (target/'runtime/models').exists() # User models are separate.
assert (target/'runtime/starter/SmolLM2-135M-Instruct-Q4_K_M.gguf').stat().st_size==105454432
assert (target/'runtime/voice/ggml-base-q5_1.bin').stat().st_size==59707625
assert (target/'runtime/voice/whisper-cli.exe').is_file()
if options.core:
 assert not (target/'runtime/speech').exists()
 assert not (target/'runtime/vulkan').exists()
 for name in ('LICENSE-espeak-ng.txt','LICENSE-onnxruntime.txt','NOTICE-onnxruntime.txt','LICENSE-piper-phonemize.txt'):
  assert not (target/name).exists(),name
 assert read(target/'BUILD_VARIANT.json')['variant']=='core'
 assert (target/'WINDOWS_CORE.txt').is_file()
 assert 'not bundled' in (target/'OFFLINE_ASSETS_NOTICE.txt').read_text(encoding='utf-8')
else:
 assert (target/'runtime/speech/ru_RU-dmitri-medium.onnx').is_file()
 assert (target/'runtime/speech/sherpa-onnx-c-api.dll').is_file()
 assert (target/'LICENSE-espeak-ng.txt').is_file()
data=work/('data-'+uuid.uuid4().hex);data.mkdir();sentinel=data/'user-keep.txt';sentinel.write_text('retain user data')
report=work/'native.json'
if report.exists():report.unlink()
p=subprocess.Popen([str(target/'RainForge.exe'),'--bundle-root',str(target),'--data-dir',str(data),'--smoke-report',str(report),'--smoke-exit','true'])
p.wait(timeout=70);native=read(report);assert native['status']=='PASS',native
# Verify worker lifetime through the Win32 process exit status.
api=ctypes.WinDLL('kernel32',use_last_error=True);api.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32];api.OpenProcess.restype=ctypes.c_void_p;api.CloseHandle.argtypes=[ctypes.c_void_p];api.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
for _ in range(50):
 h=api.OpenProcess(0x1000,False,native['worker_pid'])
 alive=False
 if h:
  status=ctypes.c_uint32();api.GetExitCodeProcess(h,ctypes.byref(status));alive=status.value==259;api.CloseHandle(h)
 if not alive:break
 time.sleep(.1)
assert not alive,'Native close leaked the worker'
unknown=target/'user-owned-keep.txt';unknown.write_text('preserve unknown files')
uninstalled=run([str(setup),'--uninstall-root',str(target),'--report',str(work/'uninstall.json')],work/'uninstall.json')
assert sentinel.read_text()=='retain user data' and unknown.read_text()=='preserve unknown files'
assert not (target/'RainForge.exe').exists()
# Remove only the test-owned sentinel and empty target; no recursive deletion.
unknown.unlink();target.rmdir()
proof={'status':'PASS','variant':'core' if options.core else 'full','installed_payload':True,'offline_voice_and_starter_payload':True,'native_ui_verified_note':True,'worker_cleanup':True,'uninstall_preserves_data':True,'uninstall_preserves_unknown_files':True,'clean_machine':'SKIPPED: no clean Windows VM available','digital_signature':'unsigned development build'}
(work/'result.json').write_text(json.dumps(proof,indent=2),encoding='utf-8');print(json.dumps(proof,indent=2))
