#!/usr/bin/env python3
"""Exercise the actual Java preview CSP in Chromium; not an Android device test."""
from pathlib import Path
import json
import re
import unittest

from playwright.sync_api import sync_playwright


class PreviewPolicyTest(unittest.TestCase):
    def test_inline_game_runs_and_capabilities_are_blocked(self):
        source = (Path(__file__).resolve().parents[1] / 'app/src/main/java/app/weqrom/ai/PreviewPolicy.java').read_text()
        expression = source.split('String CSP =', 1)[1].split(';\n', 1)[0]
        policy = ''.join(json.loads('"' + text + '"') for text in re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', expression))
        document = '''<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src * 'unsafe-inline' 'unsafe-eval' data: blob:">
<style>body{background:rgb(1,2,3)}</style></head><body><canvas width="10" height="10"></canvas>
<script>
window.checks={inline:true,evalBlocked:false,workerBlocked:false,fetchBlocked:false};
const canvas=document.querySelector('canvas');canvas.getContext('2d').fillRect(0,0,5,5);
try{eval('window.unwantedEvaluation=true')}catch(e){checks.evalBlocked=true}
try{let worker=new Worker(URL.createObjectURL(new Blob(['postMessage(1)'],{type:'text/javascript'})));worker.onerror=()=>checks.workerBlocked=true}catch(e){checks.workerBlocked=true}
fetch('https://blocked.example/fetch').catch(()=>{checks.fetchBlocked=true;document.body.dataset.fetchBlocked='yes'});
</script></body></html>'''
        attempts = []
        url = 'https://preview.weqrom.invalid/index.html'
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
            try:
                page = browser.new_page()
                page.set_default_timeout(5000)
                def intercept(route):
                    attempts.append(route.request.url)
                    if route.request.url == url:
                        route.fulfill(status=200, content_type='text/html; charset=utf-8', headers={'Content-Security-Policy': policy}, body=document)
                    else:
                        route.abort()
                page.route('**/*', intercept)
                page.goto(url, wait_until='domcontentloaded')
                page.locator('body[data-fetch-blocked="yes"]').wait_for()
                self.assertEqual(page.evaluate('() => window.checks'), {'inline': True, 'evalBlocked': True, 'workerBlocked': True, 'fetchBlocked': True})
                self.assertFalse(page.evaluate('Boolean(window.unwantedEvaluation)'))
                self.assertEqual(page.evaluate("getComputedStyle(document.body).backgroundColor"), 'rgb(1, 2, 3)')
                self.assertEqual(page.evaluate("document.querySelector('canvas').getContext('2d').getImageData(1,1,1,1).data[3]"), 255)
                page.evaluate("""() => {
                    let frame=document.createElement('iframe');frame.src='https://blocked.example/frame';document.body.appendChild(frame);
                    let script=document.createElement('script');script.src='https://blocked.example/code.js';document.body.appendChild(script);
                    let form=document.createElement('form');form.method='post';form.action='https://blocked.example/form';document.body.appendChild(form);form.submit();
                }""")
                page.wait_for_timeout(100)
                self.assertEqual(page.url, url)
                self.assertEqual(attempts, [url], 'A forbidden network request escaped the CSP')
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
