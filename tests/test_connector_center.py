import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit,urlencode
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import bootstrap
from rainforge_worker.connections import Connections,PROVIDERS,custom_endpoint,PublicHTTPS,safe_url

class ConnectorCenterTests(unittest.TestCase):
    def test_catalog_complete_no_network_and_precise_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp,patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('No background requests')):
            c=Connections(Path(tmp));items=c.catalog()
            self.assertEqual({i['category'] for i in items},{'system','development','work','communication'})
            self.assertEqual(len(items),25)
            for item in items:
                self.assertFalse(item['configured']);self.assertNotEqual(item['status'],'verified')
            self.assertTrue(next(i for i in items if i['id']=='outlook')['oauth_supported'])
            self.assertIn('bot',next(i for i in items if i['id']=='telegram')['hint'].lower())
    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_all_remote_read_contracts_and_encrypted_credentials(self):
        fixtures={
            'github':[{'id':1,'full_name':'owner/repo','html_url':'https://github.com/owner/repo'}],
            'huggingface':[{'id':'owner/model','pipeline_tag':'text-generation'}],
            'gitlab':[{'id':1,'name':'Project','web_url':'https://gitlab.com/owner/project'}],
            'gmail':{'messages':[{'id':'abc'}]},
            'google_drive':{'files':[{'id':'abc','name':'File','mimeType':'text/plain'}]},
            'google_docs':{'files':[{'id':'abc','name':'Sheet'}]},
            'google_calendar':{'items':[{'id':'abc','summary':'My calendar'}]},
            'outlook':{'value':[{'id':'abc','subject':'A message'}]},
            'onedrive':{'value':[{'id':'abc','name':'File'}]},
            'notion':{'results':[{'id':'abc','properties':{'Title':{'type':'title','title':[{'plain_text':'My page'}]}}}]},
            'dropbox':{'entries':[{'id':'abc','name':'File'}]},
            'telegram':{'ok':True,'result':{'id':1,'first_name':'Bot'}},
            'discord':[{'id':'abc','name':'Server'}],
            'slack':{'ok':True,'channels':[{'id':'abc','name':'general'}]},
            'todoist':{'results':[{'id':'abc','content':'Task'}]},
            'trello':[{'id':'abc','name':'Board'}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            c=Connections(Path(tmp))
            for provider,fixture in fixtures.items():
                token='12345:test-token' if provider=='telegram' else 'apiKey:tokenValue' if provider=='trello' else 'secret-token-example'
                c.save(provider,token)
                self.assertNotIn(token.encode(),c.path(provider).read_bytes())
                with patch.object(c,'_request',return_value=fixture) as req:
                    result=c.read(provider,'test')
                    self.assertEqual(len(result['items']),1,provider);self.assertTrue(result['read_only'])
                    args=req.call_args.args
                    self.assertTrue(args[0].startswith('https://'))
                    self.assertNotIn(token,str(result));self.assertNotIn(token,str(c.catalog()))
                if provider=='notion':self.assertEqual(result['items'][0]['name'],'My page')
            # Existing raw-DPAPI credentials continue to work after migration.
            from rainforge_worker.connections import protect
            c.path('github').write_bytes(protect(b'legacy-personal-token'))
            with patch.object(c,'_request',return_value=fixtures['github']):self.assertEqual(len(c.read('github')['items']),1)
    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_custom_manifest_auth_scope_endpoint_change_and_delete(self):
        manifest=dict(id='custom-example',name='Example',category='work',endpoint='https://api.example.com/list',items_path='data.items')
        with tempfile.TemporaryDirectory() as tmp:
            c=Connections(Path(tmp));c.custom_save(manifest);c.save(manifest['id'],'example-private-token')
            with patch('rainforge_worker.connections.socket.getaddrinfo',return_value=[(2,1,6,'',('93.184.216.34',443))]),patch.object(c,'_request',return_value={'data':{'items':[{'id':1,'name':'Name example-private-token','description':'Description','url':'https://example.com/item'}]}}) as request:
                result=c.read(manifest['id'],'needle')
                self.assertTrue(request.call_args.kwargs['public_only']);self.assertTrue(request.call_args.args[0].endswith('?q=needle'))
                self.assertNotIn('example-private-token',str(result))
            c.custom_save({**manifest,'endpoint':'https://other.example.com/list'})
            self.assertFalse(c.path(manifest['id']).exists())
            c.custom_remove(manifest['id']);self.assertNotIn(manifest['id'],[i['id'] for i in c.catalog()])
    def test_custom_rejects_code_credentials_paths_and_networks(self):
        base=dict(id='custom-test',name='Test',category='work',endpoint='https://example.com/list')
        with tempfile.TemporaryDirectory() as tmp:
            c=Connections(Path(tmp))
            for bad in ({**base,'script':'print(1)'},{**base,'token':'secret'},{**base,'id':'../escape'},{**base,'endpoint':'http://example.com/list'},{**base,'endpoint':'https://127.0.0.1/list'},{**base,'endpoint':'https://[::1]/list'},{**base,'endpoint':'https://example.com/list?token=x'},{**base,'endpoint':'https://user:pass@example.com/list'},{**base,'items_path':'items[0]'}):
                with self.assertRaises(ValueError,msg=str(bad)):c.custom_save(bad)
            with patch('rainforge_worker.connections.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
                with self.assertRaises(ValueError):custom_endpoint('https://example.com/list',resolve=True)
            # Persisted map keys cannot turn a valid manifest into a credential path escape.
            (c.root/'custom-connectors.json').write_text(json.dumps({'../../escape':base}))
            self.assertEqual(c._custom(),{})
            self.assertEqual(safe_url('javascript:alert(1)'),'');self.assertEqual(safe_url('https://example.com/?secret=1'),'')
    def test_errors_do_not_echo_provider_or_token_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Connections(Path(tmp))
            with patch.object(c,'_credential',return_value={'access_token':'token-secret-example'}),patch.object(c,'_request',side_effect=OSError('token-secret-example https://private')):
                self.assertNotIn('token-secret-example',str(c.test('github')))
            with self.assertRaises(ValueError):c.read('github','x'*201)
    def test_custom_connection_pins_public_dns_and_tls_hostname(self):
        handler=PublicHTTPS();request=urllib.request.Request('https://api.example.com/list')
        with patch('rainforge_worker.connections.socket.getaddrinfo',return_value=[(2,1,6,'',('93.184.216.34',443))]) as resolve,patch('rainforge_worker.connections.socket.create_connection') as connect,patch('rainforge_worker.connections.ssl.create_default_context') as context:
            def inspect(factory,req,**kwargs):
                connection=factory('api.example.com',timeout=15,**kwargs);connection.connect();return 'bounded response'
            with patch.object(handler,'do_open',side_effect=inspect):
                self.assertEqual(handler.https_open(request),'bounded response')
            self.assertEqual(resolve.call_count,1)
            self.assertEqual(connect.call_args.args[0],('93.184.216.34',443))
            self.assertEqual(context.return_value.wrap_socket.call_args.kwargs['server_hostname'],'api.example.com')
        with patch('rainforge_worker.connections.socket.getaddrinfo',return_value=[(2,1,6,'',('169.254.169.254',443))]),patch.object(handler,'do_open') as opening:
            with self.assertRaises(ValueError):handler.https_open(request)
            self.assertFalse(opening.called)
    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_real_loopback_pkce_state_singleuse_refresh_and_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Connections(Path(tmp));auth=c.oauth.begin('outlook','registered-client-id')
            u=urlsplit(auth['authorization_url']);q=parse_qs(u.query);callback=urlsplit(auth['redirect_uri'])
            self.assertEqual(q['code_challenge_method'],['S256']);self.assertEqual(callback.hostname,'127.0.0.1')
            session=c.oauth.sessions[auth['session_id']]
            import base64
            self.assertEqual(q['code_challenge'][0],base64.urlsafe_b64encode(hashlib.sha256(session['verifier'].encode()).digest()).rstrip(b'=').decode())
            with patch.object(c.oauth,'_exchange',return_value={'access_token':'oauth-access-secret','refresh_token':'refresh-secret','expires_in':3600}) as exchange:
                conn=http.client.HTTPConnection('127.0.0.1',callback.port,timeout=5)
                conn.request('GET','/oauth/callback?'+urlencode({'state':'wrong','code':'one-time'}));self.assertEqual(conn.getresponse().status,400);conn.close()
                self.assertEqual(c.oauth.status(auth['session_id'])['status'],'waiting')
                conn=http.client.HTTPConnection('127.0.0.1',callback.port,timeout=5)
                conn.request('GET','/oauth/callback?'+urlencode({'state':q['state'][0],'code':'one-time'}));response=conn.getresponse();response.read();conn.close()
                deadline=time.time()+3
                while c.oauth.status(auth['session_id'])['status']=='exchanging' and time.time()<deadline:time.sleep(.01)
                self.assertEqual(c.oauth.status(auth['session_id'])['status'],'connected')
                self.assertEqual(exchange.call_count,1);self.assertNotIn('oauth-access-secret',str(c.catalog()))
                c.oauth._complete(auth['session_id'],{'code':['replay']});self.assertEqual(exchange.call_count,1)
            record=c._credential('outlook');self.assertEqual(record['refresh_token'],'refresh-secret')
            record['expires_at']=time.time()-1;c._write_secret('outlook',record)
            with patch.object(c.oauth,'_exchange',return_value={'access_token':'updated-access-secret','expires_in':3600}) as exchange:
                self.assertEqual(c._credential('outlook')['access_token'],'updated-access-secret')
                self.assertEqual(exchange.call_args.args[1]['grant_type'],'refresh_token')
            auth2=c.oauth.begin('gitlab','registered-client-id');c.oauth.cancel(auth2['session_id'])
            c.oauth._complete(auth2['session_id'],{'code':['late-code']});self.assertFalse(c.path('gitlab').exists())
            c.oauth.close();time.sleep(.6)
    def test_cancel_during_exchange_drops_late_credential(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Connections(Path(tmp));auth=c.oauth.begin('outlook','registered-client-id');started=threading.Event();release=threading.Event()
            def exchange(*args):started.set();release.wait(2);return {'access_token':'late-access-secret'}
            with patch.object(c.oauth,'_exchange',side_effect=exchange),patch.object(c,'_write_secret') as write:
                thread=threading.Thread(target=c.oauth._complete,args=(auth['session_id'],{'code':['code']}));thread.start();self.assertTrue(started.wait(2));c.disconnect('outlook');release.set();thread.join(2);self.assertFalse(write.called)
            c.oauth.close();time.sleep(.6)
    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_stale_refresh_cannot_resurrect_disconnected_credential(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Connections(Path(tmp));c._write_secret('outlook',{'access_token':'expired-access-token','refresh_token':'refresh-token','client_id':'test-client','expires_at':1})
            original=c.oauth.refresh;started=threading.Event();release=threading.Event();errors=[]
            def delayed(*args):started.set();release.wait(3);return original(*args)
            def read():
                try:c._credential('outlook')
                except ValueError as exc:errors.append(str(exc))
            with patch.object(c.oauth,'refresh',side_effect=delayed),patch.object(c.oauth,'_exchange',return_value={'access_token':'late-new-access-token'}):
                thread=threading.Thread(target=read);thread.start();self.assertTrue(started.wait(2));c.disconnect('outlook');release.set();thread.join(3)
                self.assertFalse(c.path('outlook').exists());self.assertEqual(len(errors),1)
    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_custom_endpoint_and_credential_snapshot_cannot_mix(self):
        base=dict(id='custom-race',name='Race',category='work',endpoint='https://old.example.com/list')
        with tempfile.TemporaryDirectory() as tmp:
            c=Connections(Path(tmp));c.custom_save(base);c.save(base['id'],'old-endpoint-token')
            original=c._credential;started=threading.Event();release=threading.Event();errors=[]
            def delayed(*args,**kwargs):started.set();release.wait(3);return original(*args,**kwargs)
            def fetch():
                try:c.read(base['id'])
                except ValueError as exc:errors.append(str(exc))
            with patch.object(c,'_credential',side_effect=delayed),patch.object(c,'_request') as request:
                thread=threading.Thread(target=fetch);thread.start();self.assertTrue(started.wait(2));c.custom_save({**base,'endpoint':'https://new.example.com/list'});c.save(base['id'],'new-endpoint-token');release.set();thread.join(3)
                self.assertFalse(request.called);self.assertEqual(len(errors),1)
    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_manual_token_cancels_pending_oauth_and_exchange_cannot_be_evicted(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Connections(Path(tmp));auth=c.oauth.begin('outlook','registered-client-id')
            c.save('outlook','manual-new-access-token');self.assertEqual(c.oauth.status(auth['session_id'])['status'],'cancelled')
            with patch.object(c.oauth,'_exchange') as exchange:c.oauth._complete(auth['session_id'],{'code':['late-code']});self.assertFalse(exchange.called)
            self.assertEqual(c._credential('outlook')['access_token'],'manual-new-access-token')
            auth=c.oauth.begin('outlook','registered-client-id');c.oauth.sessions[auth['session_id']]['status']='exchanging'
            with self.assertRaises(ValueError):c.oauth.begin('gitlab','registered-client-id')
            c.oauth.close();time.sleep(.6)
    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_encrypt_paused_save_cannot_outlive_remove_or_endpoint_change(self):
        from rainforge_worker.connections import protect
        base=dict(id='custom-encrypt',name='Encrypt',category='work',endpoint='https://old.example.com/list')
        for mutation in ('remove','reassign','recreate_same_endpoint'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as tmp:
                c=Connections(Path(tmp));c.custom_save(base);started=threading.Event();release=threading.Event();errors=[]
                def delayed(raw,decrypt=False):
                    if not decrypt:started.set();release.wait(3)
                    return protect(raw,decrypt)
                def save():
                    try:c.save(base['id'],'old-endpoint-secret')
                    except ValueError as exc:errors.append(str(exc))
                with patch('rainforge_worker.connections.protect',side_effect=delayed):
                    thread=threading.Thread(target=save);thread.start();self.assertTrue(started.wait(2))
                    if mutation=='reassign':c.custom_save({**base,'endpoint':'https://new.example.com/list'})
                    else:
                        c.custom_remove(base['id'])
                        if mutation=='recreate_same_endpoint':c.custom_save(base)
                    release.set();thread.join(3)
                self.assertEqual(len(errors),1);self.assertFalse((c.root/(base['id']+'.dpapi')).exists())
        with tempfile.TemporaryDirectory() as tmp:
            c=Connections(Path(tmp));c.custom_save(base);c.save(base['id'],'bound-endpoint-secret')
            self.assertEqual(c._credential(base['id'])['endpoint'],base['endpoint'])

if __name__=='__main__':unittest.main()
