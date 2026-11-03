"""Browser UI integration. Native Android calls are mocks, never device claims.

Run: python3 tests/test_ui.py
Requires the prepared Python Playwright package and /usr/bin/chromium. Creates
its own temporary backend database, test-only administrator and loopback server.
No production credentials, stored application data or repository files change.
"""
import base64
import hashlib
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
        state = {'apps':[app,other], 'downloads':[app,other], 'serverUrl':'', 'ai':{'mode':'text','available':False,'installed':False,'vision':False,'model':'Qwen2.5 0.5B Instruct','fast':{'available':False,'installed':False,'model':'Qwen2.5 0.5B Instruct'},'photo':{'available':False,'installed':False,'model':'Qwen2.5-VL 3B Instruct'}},'assistantPhoto':None}
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
            askAssistant:prompt=>window.lastPrompt=prompt,
            pickAssistantPhoto:()=>window.photoPickerOpened=true,
            clearAssistantPhoto:()=>{window.nativeState.assistantPhoto=null;window.Chits.onNativeEvent({type:'photo',status:'cleared'});},
            setFirebaseConfig:json=>{window.nativeState.firebaseConfig=JSON.parse(json);window.lastFirebaseConfig=JSON.parse(json);},
            cancelAssistant:()=>window.cancelRequested=true,
            downloadVisionModel:()=>window.visionModelStarted=true,
            setAssistantMode:mode=>{let ai=window.nativeState.ai;const profile=(mode==='vision'?ai.photo:ai.fast)||{};window.nativeState.ai={...ai,...profile,mode,vision:ai.vision};}
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
        page.evaluate("window.nativeState.ai={installed:true,available:true,vision:true};window.Chits.onNativeEvent({type:'ai-model',status:'ready'})")
        expect(page.locator('#chat-input')).to_be_enabled()
        page.locator('#chat-input').fill('Привет')
        page.locator('#chat-form button[type=submit]').click()
        self.assertEqual(page.evaluate('window.lastPrompt'), 'Привет')
        page.evaluate("window.Chits.onNativeEvent({type:'assistant',status:'done',text:'Тестовый ответ локальной модели'})")
        expect(page.locator('.message.assistant')).to_contain_text('Тестовый ответ локальной модели')
        page.locator('[data-help="offline"]').click()
        expect(page.locator('.message').last).to_contain_text('БЕЗ ИИ')

    def test_native_mock_photo_privacy_vision_gate_and_wifi_autoload(self):
        self.mock_native()
        page = self.page
        page.goto(self.url, wait_until='networkidle')
        page.locator('[data-page="assistant"]').click()
        page.evaluate("window.Chits.onNativeEvent({type:'ai-model',status:'waiting-wifi',totalBytes:3500000000})")
        expect(page.locator('#ai-model-panel')).to_contain_text('Ждём Wi-Fi')
        expect(page.locator('[data-action="download-model"]')).to_contain_text('разрешить мобильную сеть')
        page.locator('[data-action="pick-photo"]').click()
        self.assertTrue(page.evaluate('window.photoPickerOpened'))
        preview = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
        photo = {'type':'photo','status':'ready','id':'test-photo','name':'Тестовое фото.png','width':1,'height':1,'preview':preview}
        page.evaluate("photo=>{window.nativeState.assistantPhoto=photo;window.nativeState.ai={available:true,installed:true,vision:false};window.Chits.onNativeEvent(photo);window.dispatchEvent(new Event('focus'));}", photo)
        expect(page.locator('#photo-attachment img')).to_be_visible()
        expect(page.locator('.photo-warning')).to_contain_text('визуальной модели')
        expect(page.locator('#chat-form button[type=submit]')).to_be_disabled()
        page.locator('[data-action="clear-photo"]').click()
        expect(page.locator('#photo-attachment')).to_have_count(0)
        page.locator('[data-ai-mode="text"]').click()
        expect(page.locator('#chat-form button[type=submit]')).to_be_enabled()
        page.evaluate("photo=>{window.nativeState.assistantPhoto=photo;window.nativeState.ai={available:true,installed:true,vision:true};window.Chits.onNativeEvent({type:'ai-model',status:'ready'});window.Chits.onNativeEvent(photo)}", photo)
        page.locator('#chat-input').fill('Что на фотографии?')
        page.locator('#chat-form button[type=submit]').click()
        self.assertEqual(page.evaluate('window.lastPrompt'), 'Что на фотографии?')
        expect(page.locator('[data-action="clear-photo"]')).to_be_disabled()
        self.assertNotIn('data:image', page.evaluate('JSON.stringify(localStorage)'))
        self.assertNotIn(preview.split(',')[1], page.evaluate('JSON.stringify(localStorage)'))
        expect(page.locator('.message.user')).to_contain_text('Тестовое фото.png')
        page.evaluate("window.Chits.onNativeEvent({type:'assistant',status:'done',text:'Тестовый локальный ответ о фото'})")
        expect(page.locator('#photo-attachment')).to_be_visible()
        expect(page.locator('[data-action="clear-photo"]')).to_be_enabled()

    def test_firebase_mock_rest_login_apk_upload_catalog_and_delete(self):
        # All Google HTTP endpoints are mocked. This does not validate a live
        # Firebase project, deployed rules or a real administrator account.
        self.mock_native()
        page = self.page
        config = {'enabled':True,'apiKey':'test-public-api-key','authDomain':'chits-ui-test.firebaseapp.com',
                  'projectId':'chits-ui-test','storageBucket':'chits-ui-test.firebasestorage.app',
                  'appId':'test-public-app-id','adminEmail':'test-admin@example.com'}
        def b64(value): return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
        token = b64({'alg':'RS256'})+'.'+b64({'chitsAdmin':True,'sub':'test-admin'})+'.test-signature'
        documents = {}; storage_uploads = []; deleted = []; logins = []; refreshes = []
        headers={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Methods':'GET,POST,PATCH,DELETE,OPTIONS','Access-Control-Allow-Headers':'*'}
        def google(route):
            req = route.request
            if req.method == 'OPTIONS': route.fulfill(status=204,headers=headers); return
            if 'identitytoolkit.googleapis.com' in req.url:
                data=req.post_data_json; logins.append(data)
                self.assertEqual(data['email'], config['adminEmail'])
                self.assertEqual(data['password'], 'test-only-passphrase')
                body={'idToken':token,'refreshToken':'test-only-refresh-token','expiresIn':'60'}
            elif 'securetoken.googleapis.com' in req.url:
                refreshes.append(req.post_data)
                self.assertIn('grant_type=refresh_token',req.post_data)
                body={'id_token':token,'refresh_token':'test-only-rotated-refresh-token','expires_in':'3600'}
            elif 'firebasestorage.googleapis.com' in req.url:
                self.assertEqual(req.headers.get('authorization'),'Firebase '+token)
                if req.method=='POST':
                    raw=req.post_data_buffer; storage_uploads.append(raw)
                    self.assertIn(hashlib.sha256(self.fixture.read_bytes()).hexdigest().encode(),raw)
                    body={'name':'test-object'}
                else: deleted.append(req.url); body={}
            elif 'firestore.googleapis.com' in req.url:
                if req.method=='GET': body={'documents':list(documents.values())}
                elif req.method=='PATCH':
                    self.assertEqual(req.headers.get('authorization'),'Bearer '+token)
                    identity=req.url.split('/apps/')[1].split('?')[0]
                    doc={'name':'projects/chits-ui-test/databases/(default)/documents/apps/'+identity,'fields':req.post_data_json['fields']}
                    documents[identity]=doc; body=doc
                else:
                    identity=req.url.split('/apps/')[1].split('?')[0]; documents.pop(identity,None);body={}
            else: self.fail('Unexpected Google request '+req.url)
            route.fulfill(status=200,headers=headers,content_type='application/json',body=json.dumps(body))
        page.route('https://*.googleapis.com/**',google)
        page.goto(self.url,wait_until='networkidle')
        page.locator('[data-page="settings"]').click()
        page.locator('#firebase-config').fill(json.dumps(config))
        page.locator('#firebase-form button').click()
        expect(page.locator('#connection-badge')).to_contain_text('Firebase подключён')
        self.assertEqual(page.evaluate('window.lastFirebaseConfig.projectId'), config['projectId'])
        page.locator('#admin-password').fill('test-only-passphrase')
        page.locator('#login-form button').click()
        expect(page.locator('#upload-form')).to_be_visible()
        page.locator('#upload-form [name=title]').fill('Firebase test APK')
        page.locator('#upload-form [name=description]').fill('Тест публикации через mocked Firebase REST')
        page.locator('#upload-form [name=file]').set_input_files(str(self.fixture))
        page.locator('#upload-form button').click()
        expect(page.locator('#toast')).to_contain_text('опубликовано',timeout=15000)
        self.assertEqual(len(storage_uploads),1)
        self.assertEqual(len(documents),1)
        self.assertEqual(len(logins),1)
        self.assertEqual(len(refreshes),1)
        saved=page.evaluate('JSON.stringify(localStorage)')
        for secret in ('test-only-passphrase','test-only-refresh-token',token):self.assertNotIn(secret,saved)
        item=page.locator('.admin-item').filter(has_text='Firebase test APK')
        expect(item).to_have_count(1)
        item.locator('[data-delete-remote]').click()
        page.locator('#confirm-button').click()
        expect(page.locator('#toast')).to_contain_text('удалено из каталога')
        self.assertEqual(documents,{})
        self.assertEqual(len(deleted),1)
        page.locator('[data-action="logout"]').click()
        expect(page.locator('#login-form')).to_be_visible()
        page.locator('[data-action="disable-firebase"]').click()
        self.assertFalse(page.evaluate('window.Chits.getState().firebase'))

    def test_fast_text_and_explicit_photo_modes_are_independent(self):
        self.mock_native()
        page=self.page
        page.goto(self.url,wait_until='networkidle')
        page.evaluate("window.nativeState.ai={mode:'text',available:true,installed:true,vision:false,model:'Qwen2.5 0.5B Instruct',fast:{available:true,installed:true,model:'Qwen2.5 0.5B Instruct',embedded:true},photo:{available:false,installed:false,downloading:false,model:'Qwen2.5-VL 3B Instruct'}};window.dispatchEvent(new Event('focus'));window.Chits.navigate('assistant')")
        expect(page.locator('[data-ai-mode="text"]')).to_have_attribute('aria-pressed','true')
        expect(page.locator('#ai-model-panel')).to_contain_text('400 МБ')
        expect(page.locator('#chat-input')).to_be_enabled()
        page.locator('[data-ai-mode="vision"]').click()
        expect(page.locator('[data-ai-mode="vision"]')).to_have_attribute('aria-pressed','true')
        expect(page.locator('#ai-model-panel')).to_contain_text('3–4 ГБ')
        expect(page.locator('#chat-input')).to_be_disabled()
        page.locator('[data-action="download-model"]').click()
        self.assertTrue(page.evaluate('window.visionModelStarted'))
        self.assertFalse(page.evaluate('Boolean(window.modelStarted)'))
        page.evaluate("window.nativeState.ai.photo.downloading=true;window.nativeState.ai.photo.progress=27")
        page.locator('[data-ai-mode="text"]').click()
        expect(page.locator('#chat-input')).to_be_enabled()
        expect(page.locator('.background-model-note')).to_contain_text('27%')
        page.evaluate("window.Chits.onNativeEvent({type:'ai-model',mode:'vision',status:'downloading',progress:27})")
        expect(page.locator('[data-ai-mode="text"]')).to_have_attribute('aria-pressed','true')
        expect(page.locator('#chat-input')).to_be_enabled()
        expect(page.locator('#ai-model-panel')).to_contain_text('Qwen2.5 0.5B')
        page.locator('[data-action="pick-photo"]').click()
        self.assertTrue(page.evaluate('window.photoPickerOpened'))
        expect(page.locator('[data-ai-mode="vision"]')).to_have_attribute('aria-pressed','true')

    def test_streaming_cancel_preserves_draft_and_model_packaging_is_truthful(self):
        self.mock_native()
        page=self.page
        page.goto(self.url,wait_until='networkidle')
        page.evaluate("window.nativeState.ai={available:true,installed:true,vision:true,embedded:false,embeddingReason:'В тестовом APK весов нет'};window.dispatchEvent(new Event('focus'));window.Chits.navigate('assistant')")
        expect(page.locator('.model-packaging')).to_contain_text('В тестовом APK весов нет')
        page.locator('#chat-input').fill('Тест короткого ответа')
        page.locator('#chat-form button[type=submit]').click()
        page.locator('#chat-input').fill('Черновик следующего вопроса')
        page.locator('#chat-input').evaluate("el=>el.dataset.preserveCheck='same-node'")
        page.evaluate("window.Chits.onNativeEvent({type:'assistant',status:'generating'});window.Chits.onNativeEvent({type:'assistant',status:'partial',text:'Начало ответа'})")
        expect(page.locator('#assistant-live')).to_have_text('Начало ответа')
        page.evaluate("window.Chits.onNativeEvent({type:'assistant',status:'partial',text:'Начало ответа и продолжение'})")
        expect(page.locator('#assistant-live')).to_have_text('Начало ответа и продолжение')
        expect(page.locator('#chat-input')).to_have_attribute('data-preserve-check','same-node')
        expect(page.locator('#chat-input')).to_have_value('Черновик следующего вопроса')
        page.locator('[data-action="cancel-assistant"]').click()
        self.assertTrue(page.evaluate('window.cancelRequested'))
        expect(page.locator('[data-action="cancel-assistant"]')).to_be_disabled()
        page.evaluate("window.Chits.onNativeEvent({type:'assistant',status:'cancelled',text:'Начало ответа и продолжение'})")
        expect(page.locator('#assistant-pending')).to_have_count(0)
        expect(page.locator('.message.assistant').last).to_contain_text('Ответ остановлен')
        expect(page.locator('#chat-input')).to_be_enabled()
        expect(page.locator('#chat-input')).to_have_value('Черновик следующего вопроса')
        page.evaluate("window.nativeState.ai.embedded=true;window.dispatchEvent(new Event('focus'))")
        expect(page.locator('.model-packaging')).to_contain_text('Модель включена в APK')

    def test_redesigned_search_reduced_motion_and_compact_layout(self):
        self.mock_native()
        page=self.page
        page.emulate_media(reduced_motion='reduce')
        page.goto(self.url,wait_until='networkidle')
        expect(page.locator('.spotlight')).to_have_count(3)
        expect(page.locator('.app-card')).to_have_count(2)
        expect(page.locator('.official-source')).to_have_count(0)
        expect(page.locator('#market-region')).to_have_count(0)
        page.locator('[data-page="search"]').click()
        page.locator('#app-search').fill('Орбита')
        expect(page.locator('.app-card')).to_have_count(1)
        self.assertEqual(page.locator('.nav-item.active').get_attribute('data-page'),'search')
        self.assertEqual(page.locator('.spotlight').count(),0)
        self.assertEqual(page.locator('main').evaluate("el=>getComputedStyle(el).animationName"),'none')
        page.locator('[data-detail="orbita-v6"]').click()
        self.assertEqual(page.locator('#app-dialog').evaluate("el=>getComputedStyle(el).animationName"),'none')
        page.locator('[data-action="close-detail"]').click()
        for width in (320,390):
            page.set_viewport_size({'width':width,'height':844})
            for section in ('catalog','search','downloads','assistant','settings'):
                page.locator('[data-page="'+section+'"]').click()
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,section))

    def test_native_loopback_transport_login_upload_catalog_and_delete_without_fetch(self):
        self.mock_native()
        page=self.page
        # The UI's loopback API uses native HTTP, not fetch. No browser-security
        # flags or permission grants are used; real Java transport has its own test.
        page.add_init_script("""
          window.nativeState.serverUrl='http://127.0.0.1:8765';
          window.localRequests=[];window.localApps=[];
          window.ChitsNative.requestLocalApi=raw=>{
            const req=JSON.parse(raw);window.localRequests.push(req);
            let status=200,body={};
            if(req.path==='/api/auth/login'){
              const credentials=JSON.parse(req.body);
              if(credentials.password==='test-only-passphrase')body={token:'test-only-local-token',username:'Leon_Salimov'};
              else {status=401;body={error:'Тестовый неверный пароль'};}
            }else if(req.path==='/api/apps'&&req.method==='POST'){
              window.localApps=[{id:'local-api-test',title:req.fields.title,description:req.fields.description,category:req.fields.category,size:433079,sha256:'a'.repeat(64),versionName:'6.0',packageName:'test.local',downloadUrl:'/files/test.apk'}];
              status=201;body=window.localApps[0];
            }else if(req.path.startsWith('/api/apps/')&&req.method==='DELETE'){
              window.localApps=[];body={ok:true};
            }else if(req.path==='/api/apps')body={apps:window.localApps};
            else if(req.path==='/api/auth/logout')body={ok:true};
            setTimeout(()=>window.Chits.onNativeEvent({type:'local-api',id:req.id,statusCode:status,body}),0);
          };
        """)
        forbidden=[]
        def forbid(route): forbidden.append(route.request.url);route.abort()
        page.route('**/api/**',forbid)
        page.goto(self.url,wait_until='networkidle')
        expect(page.locator('#connection-badge')).to_contain_text('Сервер подключён')
        page.locator('[data-page="settings"]').click()
        page.locator('#admin-password').fill('test-only-passphrase')
        page.locator('#login-form button').click()
        expect(page.locator('#upload-form')).to_be_visible()
        page.locator('#upload-form [name=title]').fill('Через Termux bridge')
        page.locator('#upload-form [name=description]').fill('Файл остаётся в нативном выборе документа')
        page.locator('#upload-form [name=file]').set_input_files(str(self.fixture))
        page.locator('#upload-form button').click()
        expect(page.locator('#toast')).to_contain_text('опубликовано')
        posted=page.evaluate("window.localRequests.find(x=>x.path==='/api/apps'&&x.method==='POST')")
        self.assertTrue(posted['multipart'])
        self.assertEqual(posted['headers'],{'Authorization':'Bearer test-only-local-token'})
        self.assertEqual(set(posted['fields']),{'title','description','category'})
        self.assertNotIn('body',posted)
        self.assertNotIn('file',posted)
        item=page.locator('.admin-item').filter(has_text='Через Termux bridge')
        item.locator('[data-delete-remote]').click();page.locator('#confirm-button').click()
        expect(page.locator('#toast')).to_contain_text('удалено из каталога')
        self.assertTrue(page.evaluate("window.localRequests.some(x=>x.method==='DELETE')"))
        page.locator('[data-action="logout"]').click()
        page.locator('#admin-password').fill('incorrect-test-password')
        page.locator('#login-form button').click()
        expect(page.locator('#login-error')).to_contain_text('Тестовый неверный пароль')
        self.assertEqual(forbidden,[])

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
        for section in ('catalog','search','downloads','assistant','settings'):
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
