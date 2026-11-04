"""Real Chromium worker mathematics checks; no language-model claims."""
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import unittest

from playwright.sync_api import sync_playwright

ASSETS=Path(__file__).resolve().parents[1]/'app/src/main/assets'


class Handler(SimpleHTTPRequestHandler):
    def log_message(self,*_args):pass
    def do_GET(self):
        if self.path=='/':
            body=b'''<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; worker-src 'self' blob:; connect-src 'self'"><script src="/math-tools.js"></script>'''
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        elif self.path=='/favicon.ico':
            self.send_response(204);self.end_headers()
        else:super().do_GET()


class MathToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,directory=str(ASSETS)))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop();cls.server.shutdown();cls.server.server_close();cls.thread.join(5)

    def setUp(self):
        self.context=self.browser.new_context();self.page=self.context.new_page();self.errors=[]
        self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.goto(self.url,wait_until='networkidle')

    def tearDown(self):
        self.context.close();self.assertEqual(self.errors,[])

    def calculate(self,expression,mode='calculate'):
        return self.page.evaluate('request=>window.WeqromMath.calculate(request)',{'mode':mode,'expression':expression})

    def test_exact_fraction_large_integer_decimal_and_precedence(self):
        for expression,expected in [('1/3+1/6','1/2'),('9007199254740993+1','9007199254740994'),('0.1+0.2','3/10'),('-2^2','-4'),('2^(-2)','1/4')]:
            with self.subTest(expression=expression):
                result=self.calculate(expression);self.assertNotIn('error',result);self.assertEqual(result['text'],expected);self.assertTrue(result['exact'])

    def test_linear_quadratic_identity_contradiction_and_complex_domain(self):
        result=self.calculate('2x+3=7','equation');self.assertEqual(result['text'],'x = 2')
        result=self.calculate('x^2-5*x+6=0','equation');self.assertIn('x = 2',result['text']);self.assertIn('x = 3',result['text'])
        self.assertIn('любое значение',self.calculate('x-x=0','equation')['text'])
        self.assertIn('Решений нет',self.calculate('0=1','equation')['text'])
        self.assertEqual(self.calculate('x^2-4*x+4=0','equation')['text'],'x = 2')
        complex_roots=self.calculate('x^2+1=0','equation')['text'];self.assertIn('Действительных корней нет',complex_roots);self.assertIn('i',complex_roots)
        self.assertEqual(self.calculate('(2/3)*x=1/2','equation')['text'],'x = 3/4')

    def test_derivative_polynomial_and_sine(self):
        result=self.calculate('x^3+2*x','derivative');self.assertNotIn('error',result);self.assertEqual(result['text'],'2+3*x^2')
        self.assertEqual(self.calculate('sin(x)','derivative')['text'],'cos(x)')

    def test_rejects_variable_denominators_division_zero_and_unsupported_degree(self):
        for expression in ['x/x=1','(x-1)/(x-1)=1','1/(x-x+1)=1','x^(-1)=2']:
            with self.subTest(expression=expression):self.assertIn('знаменател',self.calculate(expression,'equation')['error'])
        self.assertIn('ноль',self.calculate('1/0')['error'])
        self.assertIn('степени не выше 2',self.calculate('x^3=1','equation')['error'])

    def test_rejects_code_unknown_functions_and_costly_input_before_cas(self):
        for expression in ['window.alert(1)','constructor(1)','factorial(1000000)','1;alert(1)','x=1']:
            with self.subTest(expression=expression):self.assertIn('error',self.calculate(expression))
        self.assertIn('Степень',self.calculate('2^1000000')['error'])
        self.assertIn('512',self.calculate('1+'*300+'1')['error'])

    def test_worker_timeout_terminates_without_freezing_ui(self):
        # The hang is only a routed test fixture, never an option in production.
        self.context.route('**/math-worker.js',lambda route:route.fulfill(content_type='application/javascript',headers={'Cache-Control':'no-store'},body='self.onmessage=()=>{while(true){}};'))
        self.page.evaluate('window.heartbeat=0;window.heartbeatTimer=setInterval(()=>window.heartbeat++,10);')
        result=self.calculate('1+1')
        self.assertIn('превысил',result['error'])
        self.assertGreater(self.page.evaluate('window.heartbeat'),10)
        self.page.evaluate('clearInterval(window.heartbeatTimer)')

    def test_vendor_bytes_match_recorded_integrity(self):
        spec=json.loads((ASSETS/'vendor/integrity.json').read_text())
        self.assertEqual(hashlib.sha256((ASSETS/'vendor/nerdamer.min.js').read_bytes()).hexdigest(),spec['bundled_sha256'])
        self.assertEqual(hashlib.sha256((ASSETS/'vendor/LICENSE.nerdamer').read_bytes()).hexdigest(),spec['license_sha256'])


if __name__=='__main__':unittest.main(verbosity=2)
