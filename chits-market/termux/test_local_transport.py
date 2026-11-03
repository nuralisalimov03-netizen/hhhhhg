"""Real Java/loopback API tests. No Android or production credentials are used."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.request

PROJECT = Path(__file__).resolve().parent.parent
TOOLS = Path(os.environ.get('CHITS_TOOLS_DIR','/workspace/.chits-tools'))
JSON_NAME = 'json-20250107.jar'
JSON_SHA256 = '85d4c1ab192d3117fd02c7fff1ec0fe63ade45cf56def7fe950ef060cf06e99f'
spec = importlib.util.spec_from_file_location('chits_local_java_backend',PROJECT/'server/app.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class LocalApiTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='chits-java-loopback-')
        cls.base = Path(cls.folder.name)
        jar = TOOLS/JSON_NAME
        if not jar.exists():
            TOOLS.mkdir(parents=True,exist_ok=True)
            payload = urllib.request.urlopen('https://repo.maven.apache.org/maven2/org/json/json/20250107/'+JSON_NAME,timeout=40).read()
            if hashlib.sha256(payload).hexdigest()!=JSON_SHA256:raise RuntimeError('Official org.json checksum mismatch')
            jar.write_bytes(payload)
        if hashlib.sha256(jar.read_bytes()).hexdigest()!=JSON_SHA256:raise RuntimeError('Cached org.json checksum mismatch')
        classes = cls.base/'classes'
        subprocess.run(['java','-jar',str(TOOLS/'ecj.jar'),'-1.8','-proc:none','-warn:none','-encoding','UTF-8',
                        '-classpath',str(jar),'-d',str(classes),str(PROJECT/'app/src/main/java/market/chits/LocalApiTransport.java'),
                        str(PROJECT/'termux/LocalApiTransportHarness.java')],check=True,capture_output=True,text=True)
        cls.classpath = os.pathsep.join([str(classes),str(jar)])
        cls.store = backend.Store(cls.base/'data')
        cls.store.init_admin('test-only-java-password')
        cls.server = backend.MarketServer(('127.0.0.1',0),cls.store)
        cls.worker = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.worker.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.worker.join(5)
        cls.folder.cleanup()

    def call(self,request,url=None,upload=None,name='fixture.apk',reject=False):
        request_file=self.base/'request.json'
        request_file.write_text(json.dumps(request))
        args=['java','-cp',self.classpath,'LocalApiTransportHarness',url or self.url,str(request_file),str(upload) if upload else '-',name]
        result=subprocess.run(args,capture_output=True,text=True,timeout=30)
        if reject:
            self.assertEqual(result.returncode,2,result.stderr)
            self.assertIn('REJECTED ',result.stderr)
            return result
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def test_real_health_login_multipart_catalog_delete_and_logout(self):
        self.assertEqual(self.call({'method':'GET','path':'/health'})['body']['status'],'ok')
        local=self.url.replace('127.0.0.1','localhost')
        self.assertEqual(self.call({'method':'GET','path':'/api/apps'},url=local)['statusCode'],200)
        bad=self.call({'method':'POST','path':'/api/auth/login','body':json.dumps({'username':'Leon_Salimov','password':'test-only-wrong'})})
        self.assertEqual(bad['statusCode'],401)
        logged=self.call({'method':'POST','path':'/api/auth/login','body':json.dumps({'username':'Leon_Salimov','password':'test-only-java-password'})})
        self.assertEqual(logged['statusCode'],200)
        headers={'Authorization':'Bearer '+logged['body']['token']}
        fixture=PROJECT/'app/src/main/assets/apks/orbita.apk'
        added=self.call({'method':'POST','path':'/api/apps','headers':headers,'fields':{'title':'Тест с телефона','description':'Real native multipart fixture','category':'Игры'}},upload=fixture,name='fixture.apk"\r\nX-Evil: injected')
        self.assertEqual(added['statusCode'],201)
        item=added['body']
        self.assertEqual(item['sha256'],hashlib.sha256(fixture.read_bytes()).hexdigest())
        self.assertEqual(item['packageName'],'com.orbita.offlinegames')
        self.assertEqual(item['title'],'Тест с телефона')
        catalog=self.call({'method':'GET','path':'/api/apps'})
        self.assertIn(item['id'],[value['id'] for value in catalog['body']['apps']])
        removed=self.call({'method':'DELETE','path':'/api/apps/'+item['id'],'headers':headers})
        self.assertEqual(removed['statusCode'],200)
        self.assertEqual(self.call({'method':'POST','path':'/api/auth/logout','headers':headers,'body':''})['statusCode'],200)
        self.assertEqual(self.call({'method':'DELETE','path':'/api/apps/'+item['id'],'headers':headers})['statusCode'],401)

    def test_rejects_external_hosts_paths_userinfo_and_extra_headers(self):
        request={'method':'GET','path':'/health'}
        for url in ['http://example.com','https://127.0.0.1','http://127.0.0.1.evil.invalid','http://user@127.0.0.1','http://127.0.0.1/api','http://127.0.0.1?x=1']:
            self.call(request,url=url,reject=True)
        for path in ['/files/test.apk','//example.com/health','/api/apps/../health','/api/apps?x=1']:
            self.call({'method':'GET','path':path},reject=True)
        self.call({'method':'GET','path':'/health','headers':{'X-Evil':'value'}},reject=True)
        self.call({'method':'POST','path':'/api/auth/logout','headers':{'Authorization':'Bearer invalid\r\nX-Evil: yes'}},reject=True)

    def test_rejects_redirect_and_oversized_response_without_following(self):
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_args):pass
            def do_GET(self):
                requests.append(self.path)
                if len(requests)==1:
                    self.send_response(302);self.send_header('Location','http://example.com/private');self.send_header('Content-Length','0');self.end_headers()
                else:
                    self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(4*1024*1024+1));self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            url=f'http://127.0.0.1:{server.server_port}'
            self.call({'method':'GET','path':'/health'},url=url,reject=True)
            self.assertEqual(requests,['/health'])
            self.call({'method':'GET','path':'/health'},url=url,reject=True)
        finally:
            server.shutdown();server.server_close();worker.join(5)


if __name__=='__main__':
    unittest.main(verbosity=2)
