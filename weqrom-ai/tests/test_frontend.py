"""Browser user flows with real local tools and an explicit native-bridge double.

The bridge double tests UI protocol behavior; it is not model inference. Math
and games use the actual shipped scripts. No external request is permitted.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import threading
import unittest

from playwright.sync_api import expect, sync_playwright

ASSETS = Path(__file__).resolve().parents[1] / 'app/src/main/assets'

BRIDGE = '''(() => {
 window.fixture={state:'ready',requests:[],cancels:0,previews:[],exports:[]};
 try { if(localStorage.getItem('fixture-no-native')==='yes') return; } catch (_) {}
 window.WeqromNative={
  status(){return JSON.stringify({state:window.fixture.state,model:'Weqrom Seed',parameters:9600000});},
  generate(raw){window.fixture.requests.push(JSON.parse(raw));},
  cancel(){window.fixture.cancels++;},
  previewHtml(html){window.fixture.previews.push(html);},
  exportHtml(html,filename){window.fixture.exports.push({html,filename});}
 };
})();'''


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *_args): pass
    def do_GET(self):
        if self.path == '/favicon.ico':
            self.send_response(204); self.end_headers()
        else: super().do_GET()


class FrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(ASSETS)))
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.worker.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.server.shutdown(); cls.server.server_close(); cls.worker.join(5)

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width':390,'height':844})
        self.context.add_init_script(BRIDGE)
        self.page = self.context.new_page(); self.errors = []; self.external = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        def route(request):
            url = request.request.url
            if url.startswith(self.base + '/') or url.startswith('blob:' + self.base + '/'):
                request.continue_()
            else:
                self.external.append(url); request.abort()
        self.context.route('**/*', route)
        self.page.goto(self.base + '/index.html', wait_until='networkidle')
        expect(self.page.locator('#model-status-text')).to_have_text('Модель готова')

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external, [])

    def send(self, text):
        self.page.locator('#chat-input').fill(text)
        self.page.locator('#send-message').click()
        return self.page.evaluate('window.fixture.requests[window.fixture.requests.length-1]')

    def event(self, **event):
        self.page.evaluate('event=>window.WeqromEvents.onEvent(event)', event)

    def test_stream_stop_then_new_request_ignores_old_events_and_keeps_roles_valid(self):
        request = self.send('Привет')
        self.assertEqual(request['maxTokens'],256)
        self.assertEqual(request['messages'],[{'role':'user','content':'Привет'}])
        self.event(id=request['id'],type='partial',text='Привет, <img src=x onerror="window.pwn=1">')
        expect(self.page.locator('.assistant .message-body')).to_contain_text('<img src=x')
        self.assertEqual(self.page.locator('#messages img').count(),0)
        self.page.locator('#stop-generation').click()
        self.assertEqual(self.page.evaluate('window.fixture.cancels'),1)
        expect(self.page.locator('#chat-input')).to_be_disabled()
        self.event(id=request['id'],type='done',text='',cancelled=True)
        expect(self.page.locator('#chat-input')).to_be_enabled()
        expect(self.page.locator('.assistant .message-footer')).to_contain_text('Ответ остановлен')
        expect(self.page.locator('.assistant .message-body')).to_contain_text('<img src=x')
        second = self.send('Напиши HTML код')
        self.assertNotEqual(second['id'],request['id'])
        self.assertEqual(second['maxTokens'],512)
        self.assertEqual(second['messages'],[{'role':'user','content':'Напиши HTML код'}])
        self.event(id=request['id'],type='partial',text='OLD STALE TEXT')
        expect(self.page.locator('.assistant').last).not_to_contain_text('OLD STALE TEXT')
        html='<!doctype html><title>Тест</title><h1>Привет</h1>'
        fenced='Готово:\n```html\n'+html+'\n```'
        self.event(id=second['id'],type='done',text=fenced,cancelled=False)
        expect(self.page.locator('.assistant').last.locator('pre code')).to_have_text(html)
        self.page.get_by_role('button',name='Открыть HTML',exact=True).click()
        self.page.get_by_role('button',name='Сохранить .html',exact=True).click()
        self.assertEqual(self.page.evaluate('window.fixture.previews'),[html])
        self.assertEqual(self.page.evaluate('window.fixture.exports[0].html'),html)
        third=self.send('Для чего нужен HTML?')
        self.assertEqual(third['maxTokens'],256)
        self.event(id=third['id'],type='done',text='')
        expect(self.page.locator('.assistant').last.locator('.message-error')).to_contain_text('без текста')

    def test_bounded_history_context_and_clear_confirmation(self):
        saved=[]
        for i in range(14):
            saved.extend([{'id':'u'+str(i),'role':'user','text':'Вопрос '+str(i)+' '+('ю'*200),'status':'complete'},
                          {'id':'a'+str(i),'role':'assistant','text':'Ответ '+str(i)+' '+('я'*200),'status':'complete'}])
        self.context.add_init_script('localStorage.setItem("weqrom-ai-chat-v1",'+json.dumps(json.dumps(saved))+');')
        self.page.reload(wait_until='networkidle')
        self.assertEqual(self.page.locator('.message').count(),20)
        request=self.send('Новый вопрос')
        messages=request['messages']
        self.assertLessEqual(len(messages),11)
        self.assertEqual(len(messages)%2,1)
        self.assertLessEqual(sum(len(item['content']) for item in messages),1800)
        self.assertEqual([item['role'] for item in messages],['user' if i%2==0 else 'assistant' for i in range(len(messages))])
        self.event(id=request['id'],type='done',text='Короткий ответ')
        self.assertEqual(self.page.locator('.message').count(),20)
        self.assertEqual(self.page.evaluate('JSON.parse(localStorage.getItem("weqrom-ai-chat-v1")).length'),20)
        self.page.locator('#clear-history').click()
        self.page.get_by_role('button',name='Отмена',exact=True).click()
        self.assertEqual(self.page.locator('.message').count(),20)
        self.page.locator('#clear-history').click()
        self.page.get_by_role('button',name='Очистить',exact=True).last.click()
        self.assertEqual(self.page.locator('.message').count(),0)
        expect(self.page.locator('#chat-empty')).to_be_visible()

    def test_code_followup_sends_complete_previous_html_when_it_fits_context(self):
        first=self.send('Напиши HTML страницу.')
        html='<!doctype html><html><body><!--'+('x'*700)+'--><p id="last">Конец</p></body></html>'
        fenced='```html\n'+html+'\n```'
        self.event(id=first['id'],type='done',text=fenced)
        followup=self.send('Измени фон в предыдущем коде.')
        self.assertGreater(len(fenced),400)
        self.assertEqual(followup['messages'],[
            {'role':'user','content':'Напиши HTML страницу.'},
            {'role':'assistant','content':fenced},
            {'role':'user','content':'Измени фон в предыдущем коде.'}])
        expect(self.page.locator('#context-notice')).to_be_hidden()
        self.event(id=followup['id'],type='done',text='Тестовая заглушка не изменяет HTML.')
        expect(self.page.locator('pre code')).to_have_text(html)
        self.page.get_by_role('button',name='Сохранить .html',exact=True).click()
        self.assertEqual(self.page.evaluate('window.fixture.exports[0].html'),html)

    def test_oversize_previous_code_is_omitted_with_visible_notice_and_full_export(self):
        first=self.send('Напиши большую HTML страницу.')
        html='<!doctype html><html><body><!--'+('x'*2600)+'--><p id="last">Полный код</p></body></html>'
        fenced='```html\n'+html+'\n```'
        self.event(id=first['id'],type='done',text=fenced)
        followup=self.send('Исправь предыдущий код.')
        self.assertEqual(followup['messages'],[{'role':'user','content':'Исправь предыдущий код.'}])
        expect(self.page.locator('#context-notice')).to_be_visible()
        expect(self.page.locator('#context-notice')).to_contain_text('код не передан модели')
        self.event(id=followup['id'],type='error',message='Тестовая ошибка')
        expect(self.page.locator('pre code')).to_have_text(html)
        self.page.get_by_role('button',name='Открыть HTML',exact=True).click()
        self.page.get_by_role('button',name='Сохранить .html',exact=True).click()
        self.assertEqual(self.page.evaluate('window.fixture.previews'),[html])
        self.assertEqual(self.page.evaluate('window.fixture.exports[0].html'),html)
        self.assertEqual(self.page.evaluate('JSON.parse(localStorage.getItem("weqrom-ai-chat-v1"))[1].text'),fenced)

    def test_token_limited_code_is_marked_incomplete_and_preserved_without_fake_closure(self):
        request=self.send('Напиши HTML игру.')
        partial='```html\n<!doctype html><html><body><script>const level = '
        self.event(id=request['id'],type='done',text=partial,finishReason='length',cancelled=False)
        expect(self.page.locator('.assistant .message-footer')).to_contain_text('лимит длины')
        expect(self.page.locator('#announcer')).to_have_text('Достигнут лимит длины ответа')
        expect(self.page.locator('.assistant .message-body')).to_have_text(partial)
        self.assertEqual(self.page.get_by_role('button',name='Открыть HTML',exact=True).count(),0)
        self.page.reload(wait_until='networkidle')
        expect(self.page.locator('.assistant .message-footer')).to_contain_text('может быть незавершён')
        expect(self.page.locator('.assistant .message-body')).to_have_text(partial)
        self.assertEqual(self.page.evaluate('JSON.parse(localStorage.getItem("weqrom-ai-chat-v1"))[1].finishReason'),'length')

    def test_real_math_worker_expression_equation_and_derivative(self):
        self.page.locator('#tab-math').click()
        self.page.locator('#math-input').fill('1/3+1/6')
        self.page.locator('#math-run').click()
        expect(self.page.locator('#math-answer')).to_have_text('1/2',timeout=5000)
        expect(self.page.locator('#math-exact')).to_have_text('Точный результат')
        self.page.locator('#math-mode').select_option('equation')
        self.page.locator('#math-input').fill('2x+3=7')
        self.page.locator('#math-run').click()
        expect(self.page.locator('#math-answer')).to_have_text('x = 2',timeout=5000)
        self.page.locator('#math-mode').select_option('derivative')
        self.page.locator('#math-input').fill('sin(x)')
        self.page.locator('#math-run').click()
        expect(self.page.locator('#math-answer')).to_have_text('cos(x)',timeout=5000)
        self.assertEqual(self.page.evaluate('window.fixture.requests.length'),0)

    def test_chat_routes_real_math_with_tool_badge_without_claiming_model_output(self):
        for prompt, expected in [('2+2','4'),('Сколько будет 1/3+1/6?','1/2'),
                                 ('Найди производную sin(x)','cos(x)')]:
            with self.subTest(prompt=prompt):
                self.page.locator('#chat-input').fill(prompt)
                self.page.locator('#send-message').click()
                expect(self.page.locator('.assistant').last.locator('.message-body')).to_have_text(expected,timeout=5000)
                expect(self.page.locator('.assistant').last.locator('.message-meta')).to_have_text('Офлайн-вычисление')
        self.page.locator('#chat-input').fill('Реши уравнение x^2-5*x+6=0')
        self.page.locator('#send-message').click()
        expect(self.page.locator('.assistant').last.locator('.message-body')).to_contain_text('x = 2',timeout=5000)
        expect(self.page.locator('.assistant').last.locator('.message-body')).to_contain_text('x = 3')
        self.page.locator('#chat-input').fill('Вычисли 1/0')
        self.page.locator('#send-message').click()
        expect(self.page.locator('.assistant').last.locator('.message-error')).to_contain_text('ноль',timeout=5000)
        self.assertEqual(self.page.evaluate('window.fixture.requests.length'),0)
        self.page.reload(wait_until='networkidle')
        self.assertEqual(self.page.locator('.assistant.tool').count(),5)
        self.event(type='state',state='loading')
        self.page.locator('#chat-input').fill('Посчитай 2^10')
        expect(self.page.locator('#send-message')).to_be_enabled()
        self.page.locator('#send-message').click()
        expect(self.page.locator('.assistant').last.locator('.message-body')).to_have_text('1024',timeout=5000)
        self.assertEqual(self.page.evaluate('window.fixture.requests.length'),0)
        self.event(type='state',state='ready')
        request=self.send('Мне нравится число 2. Расскажи о себе.')
        self.assertEqual(request['messages'][-1]['content'],'Мне нравится число 2. Расскажи о себе.')
        self.event(id=request['id'],type='done',text='Это тестовый ответ bridge double.')
        expect(self.page.locator('.assistant').last.locator('.message-meta')).to_have_text('Weqrom Seed')

    def test_real_game_builder_and_edited_html_preview_export(self):
        self.page.locator('#tab-games').click()
        self.page.locator('#game-genre').select_option('defense')
        title='<img src=x onerror="window.pwn=1">'
        self.page.locator('#game-title').fill(title)
        self.page.locator('#game-build').click()
        expect(self.page.locator('#game-output')).to_be_visible()
        expect(self.page.locator('#game-output-title')).to_have_text(title)
        self.assertEqual(self.page.locator('#panel-games img').count(),0)
        html=self.page.locator('#game-code').input_value()
        self.assertTrue(html.lower().lstrip().startswith('<!doctype html>'))
        self.assertGreater(len(html),2000)
        edited=html+'\n<!-- user edit -->'
        self.page.locator('.code-details summary').click()
        self.page.locator('#game-code').fill(edited)
        self.page.locator('#game-preview').click()
        self.page.locator('#game-export').click()
        self.assertEqual(self.page.evaluate('window.fixture.previews'),[edited])
        self.assertEqual(self.page.evaluate('window.fixture.exports[0].html'),edited)
        self.assertTrue(self.page.evaluate('window.fixture.exports[0].filename').endswith('.html'))
        self.assertEqual(self.page.evaluate('window.fixture.requests.length'),0)

    def test_loading_and_missing_bridge_never_fake_model_answer(self):
        self.event(type='state',state='loading',model='Weqrom Seed')
        self.page.locator('#chat-input').fill('Тест')
        expect(self.page.locator('#send-message')).to_be_disabled()
        self.event(type='state',state='ready',model='Weqrom Seed',parameters=9600000)
        expect(self.page.locator('#send-message')).to_be_enabled()
        self.page.locator('#about-open').click()
        expect(self.page.locator('#modal-content')).to_contain_text('обучена с нуля')
        expect(self.page.locator('#modal-content')).to_contain_text('9 600 000')
        self.page.get_by_role('button',name='Понятно').click()
        self.page.evaluate('localStorage.setItem("fixture-no-native","yes")')
        self.page.reload(wait_until='networkidle')
        expect(self.page.locator('#model-status-text')).to_have_text('Модель недоступна')
        expect(self.page.locator('#model-error')).to_contain_text('Откройте APK')
        self.assertEqual(self.page.locator('.assistant').count(),0)
        self.page.locator('#tab-games').click()
        self.page.locator('#game-build').click()
        expect(self.page.locator('#game-output')).to_be_visible()

    def test_phone_layout_stays_within_viewport_and_reduced_motion(self):
        self.page.emulate_media(reduced_motion='reduce')
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),390)
        self.page.screenshot(path='/tmp/weqrom-ai-chat-ui.png',full_page=True)
        self.page.locator('#tab-games').click()
        self.page.locator('#game-build').click()
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),390)
        self.page.screenshot(path='/tmp/weqrom-ai-games-ui.png',full_page=True)


if __name__ == '__main__': unittest.main(verbosity=2)
