"""Host integration check; this does not claim Android/Termux runtime validation."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time
import unittest

PROJECT = Path(__file__).resolve().parent.parent


class TermuxLauncherTests(unittest.TestCase):
    def test_setup_launcher_restart_and_persistent_data(self):
        with tempfile.TemporaryDirectory(prefix='chits-termux-test-') as tmp:
            data = Path(tmp)/'data'
            env = dict(os.environ, CHITS_TERMUX_HOME=str(data))
            first = subprocess.run(['bash','termux/setup.sh','--password-stdin'],input='test-only-termux-password\n',text=True,capture_output=True,env=env,cwd=PROJECT,timeout=30)
            self.assertEqual(first.returncode,0,first.stderr)
            self.assertNotIn('test-only-termux-password',first.stdout+first.stderr)
            with sqlite3.connect(data/'market.sqlite3') as db:
                admin = db.execute('SELECT username,password_hash FROM admins').fetchone()
                items = db.execute('SELECT id,sha256 FROM apps ORDER BY id').fetchall()
            self.assertEqual(admin[0],'Leon_Salimov')
            self.assertEqual(len(items),2)
            self.assertNotIn('test-only-termux-password',admin[1])
            second = subprocess.run(['bash','termux/setup.sh'],stdin=subprocess.DEVNULL,text=True,capture_output=True,env=env,cwd=PROJECT,timeout=30)
            self.assertEqual(second.returncode,0,second.stderr)
            with sqlite3.connect(data/'market.sqlite3') as db:
                self.assertEqual(db.execute('SELECT username,password_hash FROM admins').fetchone(),admin)
                self.assertEqual(db.execute('SELECT id,sha256 FROM apps ORDER BY id').fetchall(),items)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1',0))
                port = probe.getsockname()[1]
            token = None
            for run in range(2):
                process = subprocess.Popen(['bash','termux/start.sh','--port',str(port)],env=env,cwd=PROJECT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic()+10
                    while True:
                        try:
                            status, health = self.request(port,'GET','/health')
                            if status==200:break
                        except OSError:
                            pass
                        if time.monotonic()>deadline or process.poll() is not None:
                            self.fail('Termux launcher did not start local API')
                        time.sleep(.05)
                    self.assertTrue(health['adminConfigured'])
                    status, catalog = self.request(port,'GET','/api/apps')
                    self.assertEqual(len(catalog['apps']),2)
                    self.assertEqual(sorted(x['id'] for x in catalog['apps']),[x[0] for x in items])
                    item = catalog['apps'][0]
                    status, payload = self.request(port,'GET',item['downloadUrl'])
                    self.assertEqual(hashlib.sha256(payload).hexdigest(),item['sha256'])
                    if run == 0:
                        status, auth = self.request(port,'POST','/api/auth/login',{'username':'Leon_Salimov','password':'test-only-termux-password'})
                        self.assertEqual(status,200)
                        token = auth['token']
                        duplicate = subprocess.run(['bash','termux/start.sh','--port',str(port)],env=env,cwd=PROJECT,text=True,capture_output=True,timeout=10)
                        self.assertEqual(duplicate.returncode,0,duplicate.stderr)
                        self.assertIn('уже запущен',duplicate.stdout)
                        self.assertIsNone(process.poll())
                    else:
                        status, result = self.request(port,'POST','/api/auth/logout',{}, {'Authorization':'Bearer '+token})
                        self.assertEqual(status,200)
                        self.assertTrue(result['ok'])
                finally:
                    process.terminate()
                    stdout, stderr = process.communicate(timeout=10)
                    self.assertNotIn('test-only-termux-password',stdout+stderr)
                    self.assertEqual(process.returncode,0,stderr)
            unsafe = subprocess.run(['bash','termux/start.sh','--host','0.0.0.0'],env=env,cwd=PROJECT,text=True,capture_output=True,timeout=10)
            self.assertEqual(unsafe.returncode,2)

    @staticmethod
    def request(port,method,path,body=None,headers=None):
        headers=dict(headers or {})
        if body is not None:
            body=json.dumps(body)
            headers['Content-Type']='application/json'
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
        try:
            connection.request(method,path,body,headers)
            response=connection.getresponse()
            data=response.read()
            if response.getheader('Content-Type','').startswith('application/json'):
                data=json.loads(data)
            return response.status,data
        finally:
            connection.close()


if __name__=='__main__':
    unittest.main()
