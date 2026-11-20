"""Opt-in native OAuth code/PKCE with user-registered clients and loopback callback."""
import base64
import hashlib
import hmac
import http.server
import json
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

GOOGLE=('https://accounts.google.com/o/oauth2/v2/auth','https://oauth2.googleapis.com/token')
MS=('https://login.microsoftonline.com/common/oauth2/v2.0/authorize','https://login.microsoftonline.com/common/oauth2/v2.0/token')

class OAuth:
    def __init__(self,connections):
        self.connections=connections;self.lock=threading.RLock();self.sessions={}
        self.config={
            'google_drive':(*GOOGLE,'https://www.googleapis.com/auth/drive.metadata.readonly'),
            'google_docs':(*GOOGLE,'https://www.googleapis.com/auth/drive.metadata.readonly'),
            'gmail':(*GOOGLE,'https://www.googleapis.com/auth/gmail.metadata'),
            'google_calendar':(*GOOGLE,'https://www.googleapis.com/auth/calendar.calendarlist.readonly'),
            'outlook':(*MS,'offline_access User.Read Mail.ReadBasic'),
            'onedrive':(*MS,'offline_access User.Read Files.Read'),
            'gitlab':('https://gitlab.com/oauth/authorize','https://gitlab.com/oauth/token','read_api'),
            'dropbox':('https://www.dropbox.com/oauth2/authorize','https://api.dropboxapi.com/oauth2/token','account_info.read files.metadata.read'),
        }
    def begin(self,provider,client_id,client_secret='',redirect_port=0):
        if provider not in self.config:raise ValueError('Use a user token for this provider; built-in OAuth is not supported')
        if not isinstance(client_id,str) or not re.fullmatch(r'[A-Za-z0-9._-]{3,300}',client_id):raise ValueError('Enter your registered desktop/public OAuth client ID')
        if not isinstance(client_secret,str) or len(client_secret)>1000 or re.search(r'[\x00-\x20]',client_secret):raise ValueError('Invalid optional client secret')
        if type(redirect_port)!=int or (redirect_port!=0 and not 1024<=redirect_port<=65535):raise ValueError('Callback port must be 0 or 1024 to 65535')
        with self.lock:
            for key,s in list(self.sessions.items()):
                if s['expires']<time.time() and s['status']=='waiting':s['status']='expired'
                if s['expires']<time.time() and s['status']!='exchanging':self.sessions.pop(key,None)
            if any(s['status'] in ('waiting','exchanging') for s in self.sessions.values()):raise ValueError('Finish or cancel the current account authorization first')
            if len(self.sessions)>=10:self.sessions.pop(next(iter(self.sessions)))
            sid=secrets.token_urlsafe(24);state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
            owner=self
            class CallbackServer(http.server.HTTPServer):
                def get_request(self):
                    connection,address=super().get_request();connection.settimeout(3);return connection,address
                def handle_error(self,*args):pass # Never log callbacks or one-time codes.
            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_GET(self):
                    if len(self.path)>10000:
                        self.send_response(414);self.end_headers();return
                    parsed=urlsplit(self.path);q=parse_qs(parsed.query)
                    expected='127.0.0.1:'+str(self.server.server_port)
                    valid=(self.headers.get('Host')==expected and parsed.path=='/oauth/callback' and len(q.get('state',[]))==1 and hmac.compare_digest(q['state'][0],state))
                    if not valid:
                        self.send_response(400);self.end_headers();self.wfile.write(b'Invalid authorization callback.');return
                    try:
                        self.send_response(200);self.send_header('Content-Type','text/plain; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.end_headers()
                        self.wfile.write(b'Authorization received. Return to Nexus to see the connection result.')
                    except OSError:pass
                    owner._complete(sid,q)
            try:server=CallbackServer(('127.0.0.1',redirect_port),Handler)
            except OSError:raise ValueError('Loopback callback port unavailable; select another registered port') from None
            server.timeout=0.5
            redirect='http://127.0.0.1:'+str(server.server_port)+'/oauth/callback'
            s=dict(provider=provider,client_id=client_id,client_secret=client_secret,verifier=verifier,redirect_uri=redirect,status='waiting',expires=time.time()+600,server=server,state=state)
            self.sessions[sid]=s
            def listen():
                try:
                    while s['status']=='waiting' and time.time()<s['expires']:server.handle_request()
                    if s['status']=='waiting':s['status']='expired'
                finally:
                    server.server_close()
                    for k in ('client_secret','verifier','state'):s.pop(k,None)
            threading.Thread(target=listen,name='nexus-account-oauth',daemon=True).start()
            authorize,_,scope=self.config[provider]
            params=dict(client_id=client_id,response_type='code',redirect_uri=redirect,scope=scope,state=state,code_challenge_method='S256',code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode())
            if provider.startswith('google') or provider=='gmail':params.update(access_type='offline',prompt='consent')
            if provider=='dropbox':params['token_access_type']='offline'
            return dict(session_id=sid,authorization_url=authorize+'?'+urlencode(params),redirect_uri=redirect,expires_in=600,scope=scope,message='Authorize in your system browser. The exact callback URI must be registered for your own client.')
    def _exchange(self,provider,values):
        import urllib.request
        from .connections import NoRedirect
        req=urllib.request.Request(self.config[provider][1],data=urlencode(values).encode(),headers={'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json','User-Agent':'RainForge-NEXUS'})
        try:
            with urllib.request.build_opener(NoRedirect()).open(req,timeout=15) as response:
                raw=response.read(65537)
                if len(raw)>65536:raise ValueError('oversize')
                data=json.loads(raw)
            if not isinstance(data,dict) or not isinstance(data.get('access_token'),str) or not 5<=len(data['access_token'])<=8000 or re.search(r'[\r\n\x00]',data['access_token']):raise ValueError('invalid response')
            if data.get('token_type','Bearer').lower()!='bearer':raise ValueError('unsupported token type')
            return data
        except Exception:raise ValueError('OAuth token exchange failed. Check client registration, callback and required scopes; provider details were not logged.') from None
    def _complete(self,sid,query):
        with self.lock:
            s=self.sessions.get(sid)
            if not s or s['status']!='waiting' or time.time()>s['expires']:return
            s['status']='exchanging' # state is single-use, including errors.
            if 'error' in query or len(query.get('code',[]))!=1 or not 1<=len(query['code'][0])<=8000:s['status']='denied';return
            values=dict(grant_type='authorization_code',code=query['code'][0],redirect_uri=s['redirect_uri'],client_id=s['client_id'],code_verifier=s['verifier'])
            if s.get('client_secret'):values['client_secret']=s['client_secret']
        try:
            data=self._exchange(s['provider'],values)
            saved=self._token_record(s,data)
            with self.lock:
                if s['status']!='exchanging':return
                self.connections._write_secret(s['provider'],saved);s['status']='connected'
        except Exception:s['status']='failed'
    def _token_record(self,s,data):
        result={k:data[k] for k in ('access_token','refresh_token') if isinstance(data.get(k),str)}
        result.update(client_id=s['client_id'],client_secret=s.get('client_secret',''))
        try:ttl=max(30,min(int(data.get('expires_in',3600)),86400*30))
        except (ValueError,TypeError):ttl=3600
        result['expires_at']=time.time()+ttl
        return result
    def refresh(self,provider,record,expected_hash):
        if provider not in self.config or not record.get('refresh_token'):raise ValueError('Access token expired; authorize the account again')
        with self.lock:
            values=dict(grant_type='refresh_token',refresh_token=record['refresh_token'],client_id=record['client_id'])
            if record.get('client_secret'):values['client_secret']=record['client_secret']
            data=self._exchange(provider,values);data.setdefault('refresh_token',record['refresh_token'])
            updated=self._token_record(record,data);self.connections._write_secret(provider,updated,expected_hash=expected_hash);return updated
    def status(self,session_id):
        with self.lock:
            s=self.sessions.get(session_id)
            if not s:raise ValueError('Unknown authorization session')
            if s['status']=='waiting' and time.time()>s['expires']:s['status']='expired'
            return dict(session_id=session_id,provider=s['provider'],status=s['status'],message={'connected':'Account authorized and encrypted locally.','waiting':'Waiting for your authorization in the browser.','exchanging':'Exchanging the one-time code.','failed':'Authorization failed; check your registered client and scopes.','denied':'Authorization denied.','cancelled':'Authorization cancelled.','expired':'Authorization expired.'}[s['status']])
    def cancel(self,session_id):
        with self.lock:
            s=self.sessions.get(session_id)
            if not s:raise ValueError('Unknown authorization session')
            if s['status'] in ('waiting','exchanging'):s['status']='cancelled'
        return self.status(session_id)
    def cancel_provider(self,provider):
        with self.lock:
            for s in self.sessions.values():
                if s['provider']==provider and s['status'] in ('waiting','exchanging'):s['status']='cancelled'
    def close(self):
        # Listener threads have a 0.5 second accept timeout and bounded client reads.
        # Do not call HTTPServer.shutdown(): these listeners use handle_request(), not serve_forever().
        with self.lock:
            for s in self.sessions.values():
                if s['status'] in ('waiting','exchanging'):s['status']='cancelled'
