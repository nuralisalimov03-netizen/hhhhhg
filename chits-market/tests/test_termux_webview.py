"""HTTPS packaged-app origin -> real HTTP loopback API browser boundary check.

No mixed-content, CORS, TLS, private-network or other browser security setting is
disabled. Static HTTPS responses emulate WebView's packaged resource handler;
the Python API and its CORS/preflight behavior are real. The Android bridge is a
small mock. A normal user permission for local-network access is explicitly
granted in Chromium; the default desktop permission denies this origin before
an API request can reach the server. This does not claim Android WebView/Termux
permission behavior or validation on an Android device.
"""
import importlib.util
import json
import mimetypes
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import unquote, urlsplit

from playwright.sync_api import expect, sync_playwright

PROJECT = Path(__file__).resolve().parents[1]
WEB = PROJECT/'app/src/main/assets/web'
ORIGIN = 'https://app.chits.local'
spec = importlib.util.spec_from_file_location('chits_loopback_server', PROJECT/'server/app.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class TermuxWebViewBoundaryTests(unittest.TestCase):
    def test_https_app_catalog_login_and_apk_upload_to_http_loopback(self):
        observed = []
        selected = ('Origin', 'Access-Control-Request-Method', 'Access-Control-Request-Headers', 'Access-Control-Request-Private-Network', 'Sec-Fetch-Site', 'Sec-Fetch-Mode')

        class ObservedHandler(backend.Handler):
            def capture(self):
                observed.append({'method':self.command,'path':self.path,'headers':{key:self.headers[key] for key in selected if key in self.headers}})

            def do_OPTIONS(self):
                self.capture()
                super().do_OPTIONS()

            def dispatch(self):
                self.capture()
                super().dispatch()

            def log_message(self, *_args):
                pass

        with tempfile.TemporaryDirectory(prefix='chits-secure-loopback-') as folder:
            store = backend.Store(folder)
            store.init_admin('test-only-loopback-password')
            fixture = PROJECT/'app/src/main/assets/apks/orbita.apk'
            store.add(fixture,{'title':'Loopback seed','description':'Real APK fixture','category':'Игры'})
            server = backend.MarketServer(('127.0.0.1',0),store)
            server.RequestHandlerClass = ObservedHandler
            worker = threading.Thread(target=server.serve_forever,daemon=True)
            worker.start()
            api = f'http://127.0.0.1:{server.server_port}'
            errors, console = [], []
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                    try:
                        context = browser.new_context(viewport={'width':390,'height':844})
                        # Equivalent to the user allowing Chromium's Local Network
                        # Access prompt, not disabling browser network protection.
                        context.grant_permissions(['local-network-access'],origin=ORIGIN)
                        # Fulfill only packaged static content. HTTP API requests
                        # remain actual browser network requests to the server.
                        def asset(route):
                            path = unquote(urlsplit(route.request.url).path).lstrip('/') or 'index.html'
                            file = (WEB/path).resolve()
                            if not file.is_relative_to(WEB.resolve()) or not file.is_file():
                                route.fulfill(status=404,body='Not found')
                                return
                            mime = 'application/javascript' if file.suffix=='.js' else mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
                            route.fulfill(status=200,content_type=mime,body=file.read_bytes())
                        context.route(ORIGIN+'/**',asset)
                        state = {'serverUrl':api,'apps':[],'downloads':[], 'firebaseConfig':{'enabled':False},'ai':{'available':False,'installed':False,'vision':False}}
                        context.add_init_script('window.ChitsNative={getState:()=>JSON.stringify('+json.dumps(state)+')};')
                        page = context.new_page()
                        page.on('pageerror',lambda error:errors.append(str(error)))
                        page.on('requestfailed',lambda request:errors.append(request.method+' '+request.url+' '+str(request.failure)))
                        page.on('console',lambda message:console.append({'type':message.type,'text':message.text}))
                        page.goto(ORIGIN+'/',wait_until='networkidle')
                        # Chromium DevTools interception can suppress automatic
                        # CORS preflights even for unmatched routes. Remove it
                        # after loading packaged resources, then rerun catalog
                        # fetch and all mutation requests on the actual network.
                        context.unroute(ORIGIN+'/**',asset)
                        observed.clear()
                        page.evaluate('window.Chits.refresh()')
                        self.assertTrue(page.evaluate('window.isSecureContext'))
                        expect(page.locator('#connection-badge')).to_contain_text('Сервер подключён',timeout=15000)
                        expect(page.locator('.app-card')).to_have_count(1)
                        page.evaluate("window.Chits.navigate('settings')")
                        page.locator('#admin-password').fill('test-only-loopback-password')
                        page.locator('#login-form button[type=submit]').click()
                        expect(page.locator('#upload-form')).to_be_visible(timeout=10000)
                        page.locator('#upload-form [name=title]').fill('HTTPS to loopback upload')
                        page.locator('#upload-form [name=description]').fill('Actual upload across the packaged HTTPS origin and HTTP Termux API.')
                        page.locator('#upload-form [name=file]').set_input_files(str(fixture))
                        page.locator('#upload-form button[type=submit]').click()
                        expect(page.locator('.admin-item').filter(has_text='HTTPS to loopback upload')).to_have_count(1,timeout=15000)
                        self.assertEqual(len(store.apps()),2)
                        self.assertTrue(any(item['method']=='OPTIONS' and item['path']=='/api/auth/login' for item in observed))
                        self.assertTrue(any(item['method']=='POST' and item['path']=='/api/apps' for item in observed))
                        self.assertTrue(all(item['headers'].get('Origin')==ORIGIN for item in observed))
                        self.assertEqual(errors,[])
                        self.assertEqual([item for item in console if item['type']=='error'],[])
                        print(json.dumps({'browser':browser.version,'secure_origin':ORIGIN,'api_origin':'http://127.0.0.1:<ephemeral>','user_permissions':['local-network-access'], 'requests':observed,'console':console,'errors':errors},ensure_ascii=False,indent=2))
                        context.close()
                    finally:
                        browser.close()
            except Exception:
                print(json.dumps({'requests':observed,'console':console,'errors':errors},ensure_ascii=False,indent=2))
                raise
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
