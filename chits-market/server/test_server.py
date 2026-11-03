"""API integration tests; the app uses real APK fixtures without editing them."""
import hashlib
import http.client
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import zipfile

from app import MarketServer, Store, APIError


class MarketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.store = Store(cls.folder.name)
        cls.password = 'test-only-passphrase'
        cls.store.init_admin(cls.password)
        cls.web = Path(cls.folder.name)/'web'
        cls.web.mkdir()
        (cls.web/'index.html').write_text('<html>Chits Market</html>')
        (cls.web/'app.js').write_text('window.example = true;')
        (Path(cls.folder.name)/'secret.json').write_text('{"secret":true}')
        (cls.web/'leak.json').symlink_to(Path(cls.folder.name)/'secret.json')
        cls.server = MarketServer(('127.0.0.1', 0), cls.store, web_dir=cls.web)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(5)
        cls.folder.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        data = response.read()
        status, response_headers = response.status, dict(response.getheaders())
        connection.close()
        if response_headers.get('Content-Type', '').startswith('application/json'):
            data = json.loads(data)
        return status, data, response_headers

    def login(self):
        code, data, _ = self.request('POST', '/api/auth/login', json.dumps({'username':'Leon_Salimov','password':self.password}), {'Content-Type':'application/json'})
        self.assertEqual(code, 200)
        return data['token']

    def upload(self, payload, token, title='Test <APK>'):
        boundary = 'CHITS-TEST-BOUNDARY'
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="title"\r\n\r\n{title}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="../../evil.apk"\r\n'
                f'Content-Type: application/vnd.android.package-archive\r\n\r\n').encode() + payload + f'\r\n--{boundary}--\r\n'.encode()
        return self.request('POST', '/api/apps', body, {'Content-Type':'multipart/form-data; boundary='+boundary,'Authorization':'Bearer '+token})

    def test_health_and_cors(self):
        status, body, headers = self.request('GET', '/health')
        self.assertEqual(status, 200)
        self.assertTrue(body['adminConfigured'])
        self.assertEqual(headers['Access-Control-Allow-Origin'], '*')

    def test_web_ui_and_static_path_boundaries(self):
        status, body, headers = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'Chits Market', body)
        self.assertTrue(headers['Content-Type'].startswith('text/html'))
        status, body, headers = self.request('GET', '/app.js')
        self.assertEqual(status, 200)
        self.assertIn(b'window.example',body)
        self.assertEqual(headers['Cache-Control'],'no-cache')
        for path in ['/../secret.json','/%2e%2e/secret.json','/leak.json','/market.sqlite3','/app.py']:
            self.assertEqual(self.request('GET',path)[0],404,path)

    def test_auth_and_logout(self):
        status, _, _ = self.request('POST','/api/auth/login', json.dumps({'username':'Leon_Salimov','password':'incorrect'}), {'Content-Type':'application/json'})
        self.assertEqual(status, 401)
        token = self.login()
        status, _, _ = self.request('POST', '/api/auth/logout', b'', {'Authorization':'Bearer '+token})
        self.assertEqual(status, 200)
        self.assertEqual(self.upload(b'invalid', token)[0], 401)
        with self.store.connect() as db:
            encoded = db.execute('SELECT password_hash FROM admins').fetchone()[0]
            self.assertNotIn(self.password, encoded)

    def test_invalid_uploads_and_no_path_traversal(self):
        self.assertEqual(self.request('POST','/api/apps',b'not authorized')[0],401)
        token = self.login()
        self.assertEqual(self.upload(b'not an APK',token)[0],400)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:
            archive.writestr('AndroidManifest.xml','<manifest package="test"/>')
            archive.writestr('classes.dex',b'dex\n035\x00')
            archive.writestr('../evil',b'bad')
        self.assertEqual(self.upload(buffer.getvalue(),token)[0],400)
        self.assertFalse((Path(self.folder.name).parent/'evil.apk').exists())
        self.assertEqual(list(Path(self.folder.name).glob('upload-*')),[])

    def test_real_apk_upload_download_restart_delete(self):
        path = Path(__file__).resolve().parents[1]/'app/src/main/assets/apks/orbita.apk'
        payload = path.read_bytes()
        token = self.login()
        status, item, _ = self.upload(payload,token)
        self.assertEqual(status,201,item)
        self.assertEqual(item['sha256'],hashlib.sha256(payload).hexdigest())
        self.assertEqual(item['size'],len(payload))
        self.assertTrue(item['packageName'].startswith('com.'))
        self.assertEqual(item['versionName'],'6.0')
        status, apps, _ = self.request('GET','/api/apps')
        self.assertIn(item['id'],[a['id'] for a in apps['apps']])
        status, downloaded, headers = self.request('GET',item['downloadUrl'])
        self.assertEqual(status,200)
        self.assertEqual(payload,downloaded)
        self.assertEqual(headers['X-Content-SHA256'],item['sha256'])
        restored = Store(self.folder.name)
        self.assertEqual(restored.app(item['id'])['sha256'],item['sha256'])
        self.assertTrue(restored.authenticate('Bearer '+token))
        status, _, _ = self.request('DELETE','/api/apps/'+item['id'],headers={'Authorization':'Bearer '+token})
        self.assertEqual(status,200)
        self.assertEqual(self.request('GET',item['downloadUrl'])[0],404)
        self.assertFalse((self.store.files/(item['id']+'.apk')).exists())

    def test_password_cannot_be_silently_replaced(self):
        with self.assertRaises(APIError):
            self.store.init_admin('some-other-password')

    def test_unknown_user_is_rate_limited(self):
        credentials = json.dumps({'username':'missing-user','password':'wrong'})
        for _ in range(5):
            self.assertEqual(self.request('POST','/api/auth/login',credentials, {'Content-Type':'application/json'})[0],401)
        self.assertEqual(self.request('POST','/api/auth/login',credentials, {'Content-Type':'application/json'})[0],429)


if __name__ == '__main__':
    unittest.main()
