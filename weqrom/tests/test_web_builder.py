"""Exact byte reconstruction and browser checks; no APK code is executed.

Managed Chromium blocks file:// navigation. The generated *self-contained* HTML
is tested with the same scripts/CSP over a temporary loopback server, without
policy overrides. Offline file-opening could not be exercised under this managed browser policy.
"""
import base64
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import random
import tempfile
import threading
import unittest
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from playwright.sync_api import expect, sync_playwright

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('weqrom_builder', PROJECT / 'tools/make_web_builder.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def zip_fixture(path, changed=False, payload=None):
    if payload is None:
        payload = random.Random(14).randbytes(1024 * 1024)
    with ZipFile(path, 'w', ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in [
            ('AndroidManifest.xml', b'Weqrom manifest with a new length' if changed else b'Old manifest'),
            ('classes.dex', payload),
            ('res/icon.xml', b'new green W icon' if changed else b'old icon'),
            ('assets/unchanged.txt', b'preserved APK data' * 100),
        ]:
            info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    with path.open('ab') as output:
        output.write(b'NEW SIGNING TEST BYTES' if changed else b'ORIGINAL SIGNING TEST BYTES')


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.source, self.target = self.base / 'source.apk', self.base / 'target.apk'
        zip_fixture(self.source)
        zip_fixture(self.target, changed=True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_copy_payload_and_reconstruct_exact_zip_headers_and_trailer(self):
        patch, stats = builder.make_patch(self.source, self.target)
        self.assertGreater(stats['copied_bytes'], 1024 * 1024)
        self.assertLess(stats['inserted_bytes'], 4096)
        self.assertEqual(sum(op[2] for op in patch['ops']), self.target.stat().st_size)
        result = self.base / 'result.apk'
        builder.reconstruct(self.source, patch, result)
        self.assertEqual(result.read_bytes(), self.target.read_bytes())
        with ZipFile(result) as archive:
            self.assertIsNone(archive.testzip())

    def test_wrong_source_and_target_checksum_cannot_be_accepted(self):
        patch, _ = builder.make_patch(self.source, self.target)
        wrong = self.base / 'wrong.apk'
        wrong.write_bytes(self.source.read_bytes() + b'changed')
        with self.assertRaisesRegex(builder.BuilderError, 'Wrong source'):
            builder.reconstruct(wrong, patch, self.base / 'output.apk')
        patch['target']['sha256'] = '0' * 64
        with self.assertRaisesRegex(builder.BuilderError, 'checksum mismatch'):
            builder.reconstruct(self.source, patch, self.base / 'output.apk')

    def test_same_path_and_unsupported_changed_bytes_are_rejected(self):
        with self.assertRaises(builder.BuilderError):
            builder.make_patch(self.source, self.source)
        zip_fixture(self.target, changed=True, payload=random.Random(99).randbytes(1024 * 1024))
        with self.assertRaisesRegex(builder.BuilderError, 'small-builder limit'):
            builder.make_patch(self.source, self.target, max_insert=1024)

    def test_invalid_zip_and_duplicate_entries_are_rejected(self):
        invalid = self.base / 'invalid.apk'
        invalid.write_bytes(b'not an archive' * 10)
        with self.assertRaises(builder.BuilderError):
            builder.make_patch(invalid, self.target)
        with ZipFile(invalid, 'w') as archive:
            archive.writestr('same', b'one')
            archive.writestr('same', b'two')
        with self.assertRaisesRegex(builder.BuilderError, 'duplicate'):
            builder.make_patch(invalid, self.target)

    def test_packaging_is_self_contained_deterministic_and_refuses_user_changes(self):
        output = self.base / 'builder'
        zipped = self.base / 'builder.zip'
        result = builder.package(self.source, self.target, output, zipped)
        first = zipped.read_bytes()
        info = json.loads((output / 'build-info.json').read_text())
        self.assertEqual(info['generator'], builder.GENERATOR)
        html = (output / 'index.html').read_text()
        self.assertIn("connect-src 'none'", html)
        self.assertIn("script-src 'sha256-", html)
        self.assertNotIn('<script src=', html)
        self.assertNotIn('href="styles.css"', html)
        builder.package(self.source, self.target, output, zipped)
        self.assertEqual(first, zipped.read_bytes())
        with ZipFile(zipped) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), builder.MEMBERS)
        (output / 'README.txt').write_text('user edits')
        with self.assertRaisesRegex(builder.BuilderError, 'changed'):
            builder.package(self.source, self.target, output, zipped)
        self.assertEqual((output / 'README.txt').read_text(), 'user edits')

    def test_unknown_directory_symlink_and_input_output_overlap_are_protected(self):
        output = self.base / 'builder'
        output.mkdir()
        (output / 'keep.txt').write_text('user data')
        with self.assertRaises(builder.BuilderError):
            builder.package(self.source, self.target, output)
        self.assertEqual((output / 'keep.txt').read_text(), 'user data')
        alias = self.base / 'alias'
        alias.symlink_to(output, target_is_directory=True)
        with self.assertRaises(builder.BuilderError):
            builder.package(self.source, self.target, alias)
        with self.assertRaises(builder.BuilderError):
            builder.package(self.source, self.target, self.base)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        cls.source, cls.target = cls.base/'source.apk', cls.base/'target.apk'
        zip_fixture(cls.source)
        zip_fixture(cls.target, changed=True)
        builder.package(cls.source, cls.target, cls.base/'browser')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(cls.base/'browser')))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temporary.cleanup()

    def setUp(self):
        self.context = self.browser.new_context(accept_downloads=True, viewport={'width':1280,'height':900})
        self.page = self.context.new_page()
        self.errors = []
        self.network = []
        self.page.on('pageerror', lambda value:self.errors.append(str(value)))
        def route(request):
            if request.request.url.startswith(self.url + '/'):
                self.network.append(request.request.url)
                request.continue_()
            else:
                self.errors.append('Unexpected external request')
                request.abort()
        self.context.route('**/*', route)
        self.page.goto(self.url + '/index.html', wait_until='networkidle')
        expect(self.page.locator('#status')).to_have_text('Ожидает исходный APK')

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def test_browser_reconstructs_exact_target_bytes_without_external_requests(self):
        self.page.locator('#source-file').set_input_files(str(self.source))
        self.page.locator('#build-button').click()
        expect(self.page.locator('#download-link')).to_be_visible(timeout=30000)
        with self.page.expect_download() as pending:
            self.page.locator('#download-link').click()
        download = pending.value
        result = self.base / 'browser-download.apk'
        download.save_as(result)
        self.assertEqual(download.suggested_filename, 'Weqrom.apk')
        self.assertEqual(result.read_bytes(), self.target.read_bytes())
        self.assertTrue(all(value.startswith(self.url) for value in self.network))
        # The final HTML is self-contained; auxiliary source files are audit copies.
        self.assertEqual(self.network, [self.url + '/index.html'])

    def test_wrong_source_is_reported_without_producing_a_download(self):
        wrong = self.base / 'wrong-input.apk'
        content = bytearray(self.source.read_bytes())
        content[100] ^= 1
        wrong.write_bytes(content)
        self.page.locator('#source-file').set_input_files(str(wrong))
        self.page.locator('#build-button').click()
        expect(self.page.locator('#message')).to_be_visible()
        expect(self.page.locator('#message')).to_contain_text('не совпадает')
        expect(self.page.locator('#download-link')).to_be_hidden()


@unittest.skipUnless(os.environ.get('WEQROM_ACTUAL_SOURCE') and os.environ.get('WEQROM_ACTUAL_TARGET'), 'Set actual APK paths for the large signed-output browser test')
class ActualBrowserTests(unittest.TestCase):
    def test_actual_signed_apk_reconstruction(self):
        source = Path(os.environ['WEQROM_ACTUAL_SOURCE'])
        target = Path(os.environ['WEQROM_ACTUAL_TARGET'])
        directory = Path(os.environ.get('WEQROM_BUILDER_DIR', PROJECT / 'dist/builder'))
        info = json.loads((directory / 'build-info.json').read_text())
        self.assertEqual(builder.sha256(source), info['source']['sha256'])
        expected = builder.sha256(target)
        self.assertEqual(expected, info['target']['sha256'])
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(directory)))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        errors, requests = [], []
        try:
            with tempfile.TemporaryDirectory(prefix='weqrom-actual-browser-') as temporary:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
                    try:
                        context = browser.new_context(accept_downloads=True, viewport={'width':1280,'height':1000})
                        page = context.new_page()
                        page.on('pageerror', lambda value:errors.append(str(value)))
                        def route(request):
                            requests.append(request.request.url)
                            if request.request.url == base + '/index.html':request.continue_()
                            else:
                                errors.append('Unexpected request: ' + request.request.url)
                                request.abort()
                        context.route('**/*', route)
                        page.goto(base + '/index.html', wait_until='networkidle')
                        expect(page.locator('#status')).to_have_text('Ожидает исходный APK')
                        page.locator('#source-file').set_input_files(str(source))
                        page.locator('#build-button').click()
                        expect(page.locator('#download-link')).to_be_visible(timeout=180000)
                        page.screenshot(path='/tmp/weqrom-builder-actual.png',full_page=True)
                        with page.expect_download(timeout=60000) as pending:
                            page.locator('#download-link').click()
                        download = pending.value
                        result = Path(temporary) / 'Weqrom.apk'
                        download.save_as(result)
                        self.assertEqual(download.suggested_filename, 'Weqrom.apk')
                        self.assertEqual(result.stat().st_size, target.stat().st_size)
                        self.assertEqual(builder.sha256(result), expected)
                        self.assertEqual(errors, [])
                        self.assertEqual(requests, [base + '/index.html'])
                        context.close()
                    finally:browser.close()
        finally:
            server.shutdown();server.server_close();worker.join(timeout=5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
