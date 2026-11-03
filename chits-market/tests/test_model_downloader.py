"""Real Java/HTTP/TLS integration tests for the resumable model downloader.

Run python3 tests/test_model_downloader.py. Uses only loopback sockets, a
temporary test TLS certificate, ECJ and Java. Does not access/download models.
TLS certificate checking stays enabled; the test CA is trusted for test JVMs.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest

PROJECT = Path(__file__).resolve().parents[1]
DATA = bytes(range(256)) * 1024
SHA256 = hashlib.sha256(DATA).hexdigest()


class DownloadServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(('127.0.0.1', 0), DownloadHandler)
        self.requests = []
        self.interrupted = False
        self.http_port = self.server_port


class DownloadHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path
        range_value = self.headers.get('Range')
        self.server.requests.append((path, range_value))
        start = int(range_value[6:-1]) if range_value else 0
        if path == '/downgrade':
            self.send_response(302)
            self.send_header('Location', f'http://127.0.0.1:{self.server.http_port}/model')
            self.end_headers()
            return
        if path == '/insecure-redirect':
            self.send_response(302)
            self.send_header('Location', 'http://example.com/model')
            self.end_headers()
            return
        if path == '/relative-redirect':
            self.send_response(307)
            self.send_header('Location', '/model')
            self.end_headers()
            return
        if path == '/range-unsatisfied':
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{len(DATA)}')
            self.end_headers()
            return
        body = DATA[start:] if range_value and path != '/ignore-range' else DATA
        partial = bool(range_value) and path != '/ignore-range'
        self.send_response(206 if partial else 200)
        if partial:
            bad_start = start + 1 if path == '/bad-range' else start
            self.send_header('Content-Range', f'bytes {bad_start}-{len(DATA)-1}/{len(DATA)}')
        if path != '/oversize':
            self.send_header('Content-Length', str(len(body) - (1 if path == '/bad-length' else 0)))
        if path == '/encoded':
            self.send_header('Content-Encoding', 'gzip')
        self.end_headers()
        try:
            if path == '/interrupted' and not self.server.interrupted:
                self.server.interrupted = True
                self.wfile.write(body[:70000])
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            self.wfile.write(body)
            if path == '/oversize':
                self.wfile.write(b'excess bytes')
        except (BrokenPipeError, ConnectionResetError):
            pass


class ModelDownloaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        cls.classes = cls.base / 'classes'
        ecj = Path(os.environ.get('CHITS_TOOLS_DIR', '/workspace/.chits-tools')) / 'ecj.jar'
        subprocess.run(['java', '-jar', str(ecj), '-1.8', '-proc:none', '-warn:none',
                        '-encoding', 'UTF-8', '-d', str(cls.classes),
                        str(PROJECT/'app/src/main/java/market/chits/ModelDownloader.java'),
                        str(PROJECT/'tests/java/ModelDownloaderHarness.java')], check=True, capture_output=True)
        cls.http = DownloadServer()
        cls.http_thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.http_thread.start()
        cls.url = f'http://127.0.0.1:{cls.http.server_port}'
        # A private, short-lived fixture CA. It is not used by production code.
        cls.key = cls.base/'test-key.pem'
        cls.cert = cls.base/'test-cert.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes',
                        '-keyout',str(cls.key),'-out',str(cls.cert),'-days','1',
                        '-subj','/CN=localhost', '-addext','subjectAltName=IP:127.0.0.1,DNS:localhost'],
                       check=True, capture_output=True)
        cls.trust = cls.base/'test-trust.p12'
        subprocess.run(['keytool','-importcert','-noprompt','-alias','chits-test-ca',
                        '-file',str(cls.cert),'-keystore',str(cls.trust),'-storetype','PKCS12',
                        '-storepass','test-only-truststore'],check=True,capture_output=True)
        cls.https = DownloadServer()
        cls.https.http_port = cls.http.server_port
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.cert, cls.key)
        cls.https.socket = context.wrap_socket(cls.https.socket, server_side=True)
        cls.https_thread = threading.Thread(target=cls.https.serve_forever, daemon=True)
        cls.https_thread.start()
        cls.https_url = f'https://127.0.0.1:{cls.https.server_port}'

    @classmethod
    def tearDownClass(cls):
        for server, thread in [(cls.http, cls.http_thread),(cls.https,cls.https_thread)]:
            server.shutdown()
            server.server_close()
            thread.join(5)
        cls.temp.cleanup()

    def setUp(self):
        self.case = tempfile.TemporaryDirectory(dir=self.base)
        self.partial = Path(self.case.name)/'model.part'
        self.http.requests.clear()
        self.http.interrupted = False
        self.https.requests.clear()

    def tearDown(self):
        self.case.cleanup()

    def run_download(self, path='/model', *, url=None, sha=SHA256, size=len(DATA), limit=len(DATA), stop=None):
        command = ['java', '-Djavax.net.ssl.trustStore='+str(self.trust),
                   '-Djavax.net.ssl.trustStorePassword=test-only-truststore',
                   '-cp',str(self.classes),'ModelDownloaderHarness',url or self.url+path,
                   str(self.partial),str(size),sha,str(limit)]
        if stop is not None:
            command.append(str(stop))
        return subprocess.run(command,capture_output=True,text=True,timeout=25)

    def assert_verified(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.partial.read_bytes(),DATA)

    def test_interrupted_body_is_resumed_and_verified(self):
        first = self.run_download('/interrupted')
        self.assertEqual(first.returncode,2)
        offset = self.partial.stat().st_size
        self.assertGreater(offset,0)
        self.assertLess(offset,len(DATA))
        self.assert_verified(self.run_download('/interrupted'))
        self.assertEqual(self.http.requests[-1][1],f'bytes={offset}-')

    def test_callback_pause_preserves_prefix(self):
        first = self.run_download(stop=65536)
        self.assertEqual(first.returncode,2)
        self.assertIn('Test pause',first.stderr)
        offset = self.partial.stat().st_size
        self.assertTrue(0 < offset < len(DATA))
        self.assert_verified(self.run_download())
        self.assertEqual(self.http.requests[-1][1],f'bytes={offset}-')

    def test_ignored_range_restarts_without_append(self):
        self.partial.write_bytes(DATA[:7777])
        self.assert_verified(self.run_download('/ignore-range'))
        self.assertEqual(self.http.requests[-1][1],'bytes=7777-')

    def test_wrong_hash_removes_untrusted_download(self):
        result = self.run_download(sha='0'*64)
        self.assertEqual(result.returncode,2)
        self.assertIn('SHA-256',result.stderr)
        self.assertFalse(self.partial.exists())

    def test_bad_range_never_appends_to_existing_prefix(self):
        prefix=DATA[:7777]
        self.partial.write_bytes(prefix)
        result=self.run_download('/bad-range')
        self.assertEqual(result.returncode,2)
        self.assertIn('Content-Range',result.stderr)
        self.assertEqual(self.partial.read_bytes(),prefix)

    def test_partial_content_length_must_match(self):
        prefix=DATA[:7777]
        self.partial.write_bytes(prefix)
        result=self.run_download('/bad-length')
        self.assertEqual(result.returncode,2)
        self.assertIn('length',result.stderr)
        self.assertEqual(self.partial.read_bytes(),prefix)

    def test_oversized_body_is_deleted(self):
        result=self.run_download('/oversize')
        self.assertEqual(result.returncode,2)
        self.assertIn('exceeds expected size',result.stderr)
        self.assertFalse(self.partial.exists())

    def test_size_limit_is_checked_before_any_request(self):
        result=self.run_download(limit=len(DATA)-1)
        self.assertEqual(result.returncode,2)
        self.assertEqual(self.http.requests,[])
        self.assertFalse(self.partial.exists())

    def test_valid_https_and_refused_tls_downgrade(self):
        self.assert_verified(self.run_download(url=self.https_url+'/model'))
        self.partial.unlink()
        result=self.run_download(url=self.https_url+'/downgrade')
        self.assertEqual(result.returncode,2)
        self.assertIn('TLS downgrade refused',result.stderr)
        self.assertEqual(self.http.requests,[])

    def test_complete_saved_file_needs_no_network_and_wrong_hash_is_rejected(self):
        self.partial.write_bytes(DATA)
        self.assert_verified(self.run_download(url='https://127.0.0.1:1/no-server'))
        result=self.run_download(url='https://127.0.0.1:1/no-server',sha='0'*64)
        self.assertEqual(result.returncode,2)
        self.assertIn('SHA-256',result.stderr)
        self.assertFalse(self.partial.exists())

    def test_416_cannot_promote_an_incomplete_file(self):
        prefix=DATA[:7777]
        self.partial.write_bytes(prefix)
        result=self.run_download('/range-unsatisfied')
        self.assertEqual(result.returncode,2)
        self.assertIn('HTTP 416',result.stderr)
        self.assertEqual(self.partial.read_bytes(),prefix)

    def test_relative_redirect_retains_range(self):
        self.partial.write_bytes(DATA[:7777])
        self.assert_verified(self.run_download('/relative-redirect'))
        self.assertEqual(self.http.requests,[('/relative-redirect','bytes=7777-'),('/model','bytes=7777-')])

    def test_insecure_public_redirect_is_rejected(self):
        result=self.run_download('/insecure-redirect')
        self.assertEqual(result.returncode,2)
        self.assertIn('Models require HTTPS',result.stderr)

    def test_corrupt_prefix_cannot_pass_after_resume(self):
        self.partial.write_bytes(b'x'*7777)
        result=self.run_download()
        self.assertEqual(result.returncode,2)
        self.assertIn('SHA-256',result.stderr)
        self.assertFalse(self.partial.exists())


if __name__=='__main__':
    unittest.main(verbosity=2)
