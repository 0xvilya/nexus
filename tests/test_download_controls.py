import hashlib,sys,tempfile,threading,unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import bootstrap
from rainforge_native.download import download,DownloadCancelled
class DownloadControlTests(unittest.TestCase):
 def test_cancel_retains_partial_and_resume_verifies(self):
  payload=b'local test data'*100000;ranges=[]
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):
    offset=int(self.headers['Range'].split('=')[1].split('-')[0]);ranges.append(offset)
    self.send_response(206);self.send_header('Content-Range',f'bytes {offset}-{len(payload)-1}/{len(payload)}');self.end_headers()
    try:self.wfile.write(payload[offset:])
    except (ConnectionError,OSError):pass
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with tempfile.TemporaryDirectory() as tmp:
    target=Path(tmp)/'model.gguf';cancel=threading.Event();url=f'http://127.0.0.1:{server.server_port}/model';digest=hashlib.sha256(payload).hexdigest()
    def progress(count,total):
     if count>0:cancel.set()
    with self.assertRaises(DownloadCancelled):download(url,target,digest,len(payload),progress,cancel.is_set)
    self.assertFalse(target.exists());partial=target.with_suffix('.gguf.part');size=partial.stat().st_size;self.assertGreater(size,0)
    cancel.clear();download(url,target,digest,len(payload),cancelled=cancel.is_set)
    self.assertEqual(target.read_bytes(),payload);self.assertEqual(ranges,[0,size]);self.assertFalse(partial.exists())
  finally:server.shutdown();server.server_close();thread.join()
