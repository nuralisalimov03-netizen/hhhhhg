"""Browser UI integration. Native Android calls are mocks, never device claims.

Run: python3 tests/test_ui.py
Requires the prepared Python Playwright package and /usr/bin/chromium. Creates
its own temporary backend database, test-only administrator and loopback server.
No production credentials, stored application data or repository files change.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest

from playwright.sync_api import expect, sync_playwright

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('chits_test_server', PROJECT / 'server/app.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class MarketUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.store = backend.Store(cls.directory.name)
        cls.store.init_admin('test-only-passphrase')
        cls.fixture = PROJECT / 'app/src/main/assets/apks/orbita.apk'
        cls.seed = cls.store.add(cls.fixture, {'title':'Орбита', 'description':'45 офлайн-игр', 'category':'Игры'})
        cls.server = backend.MarketServer(('127.0.0.1', 0), cls.store, web_dir=PROJECT / 'app/src/main/assets/web')
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
        cls.directory.cleanup()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width':390,'height':844})
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def mock_native(self):
        app = {**self.seed, 'id':'orbita-v6', 'bundled':True, 'downloadUrl':'bundled:orbita-v6'}
        other = {'id':'breach-v2', 'title':'Прорыв', 'description':'Офлайн-шутер', 'category':'Игры', 'size':242972, 'sha256':'b'*64, 'bundled':True, 'downloadUrl':'bundled:breach-v2'}
        state = {'apps':[app,other], 'downloads':[app,other], 'serverUrl':'', 'ai':{'available':False,'installed':False}}
        self.page.add_init_script('''
          window.nativeState = %s;
          window.ChitsNative = {
            getState:()=>JSON.stringify(window.nativeState),
            installApp:id=>window.lastInstalled=id,
            deleteDownload:id=>window.deletedId=id,
            downloadApp:json=>window.lastDownload=JSON.parse(json),
            setServerUrl:url=>{window.nativeState.serverUrl=url;window.savedServer=url;},
            openApkPicker:()=>window.importOpened=true,
            importAiModel:()=>window.modelImportOpened=true,
            downloadAiModel:()=>window.modelStarted=true,
            askAssistant:prompt=>window.lastPrompt=prompt
          };
        ''' % json.dumps(state))

    def test_native_mock_offline_catalog_install_delete_restore_and_events(self):
        self.mock_native()
        page = self.page
        page.goto(self.url, wait_until='networkidle')
        expect(page.locator('.app-card')).to_have_count(2)
        page.locator('#app-search').fill('Прорыв')
        expect(page.locator('.app-card')).to_have_count(1)
        page.locator('[data-detail="breach-v2"]').click()
        expect(page.locator('#app-dialog')).to_be_visible()
        page.locator('#app-detail [data-install]').click()
        self.assertEqual(page.evaluate('window.lastInstalled'), 'breach-v2')
        page.evaluate("window.Chits.onNativeEvent({type:'install',status:'permission',error:'Тестовое разрешение Android'})")
        expect(page.locator('#toast')).to_contain_text('Тестовое разрешение Android')
        self.assertTrue(page.evaluate('window.Chits.goBack()'))
        page.locator('[data-page="downloads"]').click()
        expect(page.locator('.download-row')).to_have_count(2)
        page.locator('[data-delete-download="orbita-v6"]').click()
        page.locator('#confirm-button').click()
        self.assertEqual(page.evaluate('window.deletedId'), 'orbita-v6')
        # Android deletion is asynchronous; do not claim deletion before its event.
        expect(page.locator('.download-row')).to_have_count(2)
        page.evaluate("window.nativeState.downloads=window.nativeState.downloads.filter(x=>x.id!=='orbita-v6');window.Chits.onNativeEvent({type:'download',id:'orbita-v6',status:'deleted'})")
        expect(page.locator('.download-row')).to_have_count(1)
        page.locator('[data-page="catalog"]').click()
        page.locator('#app-search').fill('Орбита')
        page.locator('[data-download="orbita-v6"]').click()
        self.assertTrue(page.evaluate('window.lastDownload.bundled'))
        self.assertEqual(page.evaluate('window.lastDownload.id'), 'orbita-v6')
        page.evaluate("window.Chits.onNativeEvent({type:'download',status:'error',error:'Тестовая ошибка импорта'})")
        expect(page.locator('#toast')).to_contain_text('Тестовая ошибка импорта')

    def test_native_mock_local_model_download_import_and_chat(self):
        self.mock_native()
        page = self.page
        page.goto(self.url, wait_until='networkidle')
        page.locator('[data-page="assistant"]').click()
        expect(page.locator('#chat-input')).to_be_disabled()
        page.locator('[data-action="import-model"]').click()
        self.assertTrue(page.evaluate('window.modelImportOpened'))
        page.locator('[data-action="download-model"]').click()
        self.assertTrue(page.evaluate('window.modelStarted'))
        page.evaluate("window.Chits.onNativeEvent({type:'ai-model',status:'downloading',progress:52})")
        expect(page.locator('#ai-model-panel')).to_contain_text('52%')
        page.evaluate("window.nativeState.ai={installed:true,available:true};window.Chits.onNativeEvent({type:'ai-model',status:'ready'})")
        expect(page.locator('#chat-input')).to_be_enabled()
        page.locator('#chat-input').fill('Привет')
        page.locator('#chat-form button').click()
        self.assertEqual(page.evaluate('window.lastPrompt'), 'Привет')
        page.evaluate("window.Chits.onNativeEvent({type:'assistant',status:'done',text:'Тестовый ответ локальной модели'})")
        expect(page.locator('.message.assistant')).to_contain_text('Тестовый ответ локальной модели')
        page.locator('[data-help="offline"]').click()
        expect(page.locator('.message').last).to_contain_text('БЕЗ ИИ')

    def test_real_backend_desktop_admin_upload_catalog_delete_logout(self):
        page = self.page
        page.goto(self.url, wait_until='networkidle')
        # Web admin uses its own backend origin without a manual URL setup.
        expect(page.locator('.app-card')).to_have_count(1)
        page.locator('[data-page="settings"]').click()
        expect(page.locator('#server-url')).to_have_value(self.url)
        expect(page.locator('#admin-password')).to_have_value('')
        page.locator('#admin-password').fill('test-only-passphrase')
        page.locator('#login-form button').click()
        expect(page.locator('#upload-form')).to_be_visible()
        saved = page.evaluate('JSON.stringify(localStorage)')
        self.assertNotIn('test-only-passphrase', saved)
        self.assertNotIn('token', saved)
        page.locator('#upload-form [name=title]').fill('Проверка <APK>')
        page.locator('#upload-form [name=description]').fill('Настоящая отправка multipart APK на временный сервер')
        page.locator('#upload-form [name=file]').set_input_files(str(self.fixture))
        page.locator('#upload-form button').click()
        expect(page.locator('#toast')).to_contain_text('опубликовано', timeout=15000)
        item = page.locator('.admin-item').filter(has_text='Проверка <APK>')
        expect(item).to_have_count(1)
        item.locator('[data-delete-remote]').click()
        page.locator('#confirm-button').click()
        expect(page.locator('#toast')).to_contain_text('удалено из каталога')
        page.locator('[data-action="logout"]').click()
        expect(page.locator('#login-form')).to_be_visible()
        expect(page.locator('#admin-password')).to_have_value('')
        page.locator('[data-page="catalog"]').click()
        expect(page.locator('.app-card')).to_have_count(1)

    def test_native_remote_cache_merge_deduplicates_sha_and_survives_offline(self):
        self.mock_native()
        self.page.add_init_script('window.nativeState.serverUrl=%s;' % json.dumps(self.url))
        page = self.page
        page.goto(self.url, wait_until='networkidle')
        expect(page.locator('#connection-badge')).to_contain_text('Сервер подключён')
        # Remote Orbita UUID and builtin Orbita ID are one APK, not duplicate cards.
        expect(page.locator('.app-card')).to_have_count(2)
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        expect(page.locator('.app-card')).to_have_count(2)
        page.route('**/api/apps', lambda route:route.abort())
        page.reload(wait_until='networkidle')
        expect(page.locator('.app-card')).to_have_count(2)
        expect(page.locator('[data-install]')).to_have_count(2)
        expect(page.locator('#connection-badge')).to_contain_text('Локальный режим')

    def test_compact_layout_and_html_content_are_safe(self):
        self.mock_native()
        page = self.page
        page.set_viewport_size({'width':320,'height':720})
        page.goto(self.url, wait_until='networkidle')
        for section in ('catalog','downloads','assistant','settings'):
            page.locator('[data-page="'+section+'"]').click()
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), section)
        page.evaluate("window.nativeState.apps[0].title='<img src=x onerror=alert(1)>';window.dispatchEvent(new Event('focus'));window.Chits.navigate('catalog')")
        expect(page.locator('.app-card img')).to_have_count(0)
        expect(page.locator('.app-card h3').first).to_have_text('<img src=x onerror=alert(1)>')
        page.locator('[data-page="settings"]').click()
        page.locator('#server-url').fill('https://example.com/path')
        page.locator('#server-form button').click()
        expect(page.locator('#server-error')).to_contain_text('без пути')
        page.locator('#server-url').fill('http://example.com')
        page.locator('#server-form button').click()
        expect(page.locator('#server-error')).to_contain_text('HTTPS')


if __name__ == '__main__':
    unittest.main(verbosity=2)
