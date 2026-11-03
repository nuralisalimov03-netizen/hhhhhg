"""Local HTTP fixtures for official model fetching; no real model/network use.

Fixture GGUF prefixes test byte integrity/packaging transport only. They do not
claim that a synthetic fixture is usable for inference.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request


PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('chits_fetch_models', PROJECT / 'tools/fetch-models.py')
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)
FAST_NAME = 'Qwen-Fast-Q4_K_M.gguf'
VISION_NAME = 'Qwen-VL-Q4_K_M.gguf'
Q8_NAME = 'mmproj-Q8_0.gguf'
F16_NAME = 'mmproj-F16.gguf'
DATA = {
    FAST_NAME: b'GGUF\x03\x00\x00\x00' + bytes(range(256)) * 1600,
    VISION_NAME: b'GGUF\x03\x00\x00\x00' + b'vision-fixture' * 18000,
    Q8_NAME: b'GGUF\x03\x00\x00\x00' + b'projector-q8-fixture' * 12000,
    F16_NAME: b'GGUF\x03\x00\x00\x00' + b'projector-f16-fixture' * 16000,
}
REVISIONS = {fetch.FAST_REPO: 'a' * 40, fetch.VISION_REPO: 'b' * 40}


def record(name):
    body = DATA[name]
    return {'type': 'file', 'path': name, 'size': len(body),
            'lfs': {'oid': hashlib.sha256(body).hexdigest(), 'size': len(body)}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply_json(self, value):
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        range_header = self.headers.get('Range')
        self.server.requests.append((path, range_header))
        if self.server.offline:
            self.send_error(503)
            return
        for repo, revision in REVISIONS.items():
            if path == '/api/models/' + repo + '/revision/main':
                self.reply_json({'sha': revision})
                return
            if path == '/api/models/' + repo + '/tree/' + revision:
                entries = [record(FAST_NAME)] if repo == fetch.FAST_REPO else [record(VISION_NAME), record(F16_NAME), record(Q8_NAME)]
                self.reply_json(entries)
                return
        name = urllib.parse.unquote(path.rsplit('/', 1)[-1])
        if name not in DATA:
            self.send_error(404)
            return
        if self.server.mode == 'external-redirect':
            self.send_response(302)
            self.send_header('Location', 'http://example.invalid/model.gguf')
            self.end_headers()
            return
        body = DATA[name]
        start = int(range_header[6:-1]) if range_header else 0
        if self.server.mode == 'ignore-range':
            start = 0
            range_header = None
        self.send_response(206 if range_header else 200)
        if range_header:
            actual = start + 1 if self.server.mode == 'bad-range' else start
            self.send_header('Content-Range', f'bytes {actual}-{len(body)-1}/{len(body)}')
        self.send_header('Content-Length', str(len(body)-start))
        self.end_headers()
        if self.server.mode == 'interrupt' and not self.server.interrupted:
            self.server.interrupted = True
            self.wfile.write(body[start:start+80000])
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        if self.server.mode == 'corrupt':
            body = body[:-1] + bytes([body[-1] ^ 1])
        try:
            self.wfile.write(body[start:])
        except (BrokenPipeError, ConnectionResetError):
            pass


class FetchModelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(5)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / 'models'
        self.client = fetch.Fetcher(self.root, _base_url=self.url, _allow_loopback_tests=True)
        self.server.requests = []
        self.server.mode = ''
        self.server.offline = False
        self.server.interrupted = False

    def tearDown(self):
        self.directory.cleanup()

    def test_full_immutable_profile_q8_preference_and_offline_reuse(self):
        result = self.client.fetch('full')
        self.assertEqual(result['visionRevision'], REVISIONS[fetch.VISION_REPO])
        for role, name in [('text', FAST_NAME), ('vision', VISION_NAME), ('projector', Q8_NAME)]:
            self.assertEqual(Path(result[role]).read_bytes(), DATA[name])
        self.assertEqual(json.loads((self.root / 'paths.json').read_text()), result)
        request_paths = [path for path, _ in self.server.requests]
        self.assertNotIn('/' + fetch.VISION_REPO + '/resolve/' + REVISIONS[fetch.VISION_REPO] + '/' + F16_NAME, request_paths)
        self.assertTrue(all('/resolve/main/' not in path for path in request_paths))
        self.server.requests.clear()
        self.server.offline = True
        self.assertEqual(self.client.fetch('full'), result)
        self.assertEqual(self.server.requests, [])
        command = fetch.bundle_command(result)
        self.assertIn('--vision-revision ' + REVISIONS[fetch.VISION_REPO], command)
        self.assertIn('--output app/src/main/assets/models', command)

    def test_transport_interruption_preserves_part_and_resumes_range(self):
        self.server.mode = 'interrupt'
        with self.assertRaises((fetch.FetchError, OSError)):
            self.client.fetch('fast')
        part = next(self.root.glob('*.part'))
        self.assertEqual(part.stat().st_size, 80000)
        metadata_before = sum('/api/models/' in path for path, _ in self.server.requests)
        result = self.client.fetch('fast')
        self.assertEqual(Path(result['text']).read_bytes(), DATA[FAST_NAME])
        self.assertTrue(any(value == 'bytes=80000-' for _, value in self.server.requests))
        self.assertEqual(sum('/api/models/' in path for path, _ in self.server.requests), metadata_before)

    def test_server_ignoring_range_restarts_instead_of_appending(self):
        entry = self.client.manifest('fast')['files'][0]
        (self.root / (entry['filename'] + '.part')).write_bytes(DATA[FAST_NAME][:90000])
        self.server.mode = 'ignore-range'
        path = self.client.download(entry)
        self.assertEqual(path.read_bytes(), DATA[FAST_NAME])

    def test_invalid_content_range_leaves_prefix_unchanged(self):
        entry = self.client.manifest('fast')['files'][0]
        part = self.root / (entry['filename'] + '.part')
        prefix = DATA[FAST_NAME][:90000]
        part.write_bytes(prefix)
        self.server.mode = 'bad-range'
        with self.assertRaisesRegex(fetch.FetchError, 'Content-Range'):
            self.client.download(entry)
        self.assertEqual(part.read_bytes(), prefix)

    def test_corrupt_response_cannot_create_a_model_or_paths_manifest(self):
        self.server.mode = 'corrupt'
        with self.assertRaises(fetch.IntegrityError):
            self.client.fetch('fast')
        self.assertEqual(list(self.root.glob('*.gguf')), [])
        self.assertEqual(list(self.root.glob('*.part')), [])
        self.assertFalse((self.root / 'paths.json').exists())

    def test_metadata_requires_pinned_revision_valid_lfs_and_safe_filenames(self):
        good = record(FAST_NAME)
        cases = [('main', [good]), ('a'*40, [{**good, 'lfs': {'oid': 'bad'}}]),
                 ('a'*40, [{**good, 'size': fetch.COMPONENT_LIMIT+1}]),
                 ('a'*40, [{**good, 'path': '../Q4_K_M.gguf'}]),
                 ('a'*40, [good, {**good, 'path': 'another-Q4_K_M.gguf'}])]
        for revision, tree in cases:
            with self.subTest(revision=revision, tree=tree), self.assertRaises(fetch.FetchError):
                fetch.select_files(fetch.FAST_REPO, revision, tree)

    def test_cached_manifest_cannot_switch_repo_or_escape_cache(self):
        manifest = self.client.manifest('fast')
        for field, replacement in [('repo', 'unknown/repo'), ('filename', '../outside.gguf')]:
            copied = json.loads(json.dumps(manifest))
            copied['files'][0][field] = replacement
            with self.subTest(field=field), self.assertRaises(fetch.FetchError):
                fetch.validate_manifest(copied, 'fast')

    def test_insecure_redirect_is_refused_before_external_request(self):
        self.server.mode = 'external-redirect'
        with self.assertRaises(fetch.FetchError):
            self.client.fetch('fast')
        redirect = fetch._VerifiedRedirect(True)
        with self.assertRaisesRegex(fetch.FetchError, 'HTTPS-to-HTTP'):
            redirect.redirect_request(urllib.request.Request('https://huggingface.co/model'),
                                      None, 302, '', {}, self.url + '/model')
        with self.assertRaises(fetch.FetchError):
            fetch.Fetcher(self.root, _base_url=self.url)


if __name__ == '__main__':
    unittest.main(verbosity=2)
