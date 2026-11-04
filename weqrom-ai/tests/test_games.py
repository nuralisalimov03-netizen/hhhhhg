"""Exercise self-contained exports as actual browser games, without network."""
import json
from pathlib import Path
import subprocess
import unittest

from playwright.sync_api import expect, sync_playwright

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT/'app/src/main/assets/games.js'


def build(config):
    script = 'require(process.argv[1]); let data=""; process.stdin.on("data",chunk=>data+=chunk);process.stdin.on("end",()=>{try{process.stdout.write(JSON.stringify(WeqromGames.build(JSON.parse(data))));}catch(e){process.stdout.write(JSON.stringify({error:e.message}));}});'
    result = subprocess.run(['node','-e',script,str(SCRIPT)],input=json.dumps(config),text=True,capture_output=True,check=True,timeout=10)
    return json.loads(result.stdout)


class GameExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()

    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':1100,'height':900})
        self.page=self.context.new_page()
        self.errors=[];self.requests=[]
        self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.on('console',lambda message:self.errors.append(message.text) if message.type=='error' else None)
        self.page.on('request',lambda request:self.requests.append(request.url))

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors,[])
        self.assertEqual(self.requests,[], 'Export attempted network or external assets')

    def game(self,genre,**options):
        data=build({'genre':genre,'title':'Тестовая игра','theme':'mint','difficulty':'normal','seed':42,**options})
        self.assertNotIn('error',data)
        self.page.set_content(data['html'],wait_until='load')
        self.assertEqual(self.page.evaluate('WeqromGame.getState().genre'),genre)
        self.page.locator('#primary-action').click()
        self.page.wait_for_function('() => WeqromGame.getState().time>.05')
        return data

    def state(self):return self.page.evaluate('WeqromGame.getState()')

    def click_cell(self,col,row):
        box=self.page.locator('#game').bounding_box()
        self.page.locator('#game').click(position={'x':(96+(col+.5)*64)*box['width']/960,'y':(44+(row+.5)*64)*box['height']/600})

    def test_survival_movement_combat_wave_upgrade_pause_and_restart(self):
        self.game('survival')
        initial=self.state()
        self.page.keyboard.down('ArrowRight');self.page.wait_for_timeout(450);self.page.keyboard.up('ArrowRight')
        self.assertGreater(self.state()['player']['x'],initial['player']['x']+40)
        self.page.wait_for_function('() => WeqromGame.getState().enemies>0')
        self.page.wait_for_function('() => WeqromGame.getState().score>0',timeout=10000)
        self.page.locator('#pause').click()
        paused=self.state()
        self.assertTrue(paused['paused'])
        self.page.wait_for_timeout(250)
        self.assertEqual(self.state()['time'],paused['time'])
        self.page.locator('#primary-action').click()
        self.page.wait_for_function('() => !document.getElementById("upgrades").hidden',timeout=22000)
        self.page.locator('[data-upgrade=damage]').click()
        self.assertEqual(self.state()['wave'],2)
        self.assertEqual(self.state()['upgrades'],1)
        self.page.locator('#restart').click()
        state=self.state()
        self.assertEqual((state['wave'],state['score'],state['upgrades']),(1,0,0))
        self.assertAlmostEqual(state['player']['x'],480,delta=2)

    def test_defense_path_collision_tower_upgrade_waves_and_economy(self):
        self.game('defense')
        self.click_cell(2,4)
        self.assertEqual(self.state()['gold'],150)
        expect(self.page.locator('#notice')).to_contain_text('маршруте')
        self.click_cell(2,3)
        self.assertEqual(self.state()['gold'],100)
        self.click_cell(2,3)
        self.assertEqual(self.state()['towers'][0]['level'],2)
        self.assertEqual(self.state()['gold'],30)
        self.page.locator('#next-wave').click()
        self.page.wait_for_function('() => WeqromGame.getState().enemies>0')
        before=self.state()['enemyPositions'][0]
        self.page.wait_for_timeout(300)
        self.assertGreater(self.state()['enemyPositions'][0]['x'],before['x'])
        self.page.wait_for_function('() => WeqromGame.getState().wave>=2',timeout=20000)
        self.assertGreater(self.state()['score'],0)
        self.assertGreaterEqual(self.state()['gold'],70)
        self.page.locator('[data-tower=frost]').click()
        self.click_cell(4,1)
        self.assertEqual([x['kind'] for x in self.state()['towers']],['gun','frost'])
        self.page.locator('#restart').click()
        state=self.state()
        self.assertEqual((state['wave'],state['gold'],len(state['towers'])),(1,150,0))

    def test_platformer_keyboard_touch_jump_coins_and_restart(self):
        self.game('platformer')
        self.page.wait_for_function('() => WeqromGame.getState().player.grounded')
        original=self.state()['player']
        self.page.keyboard.down('ArrowRight');self.page.wait_for_timeout(550);self.page.keyboard.up('ArrowRight')
        self.assertGreater(self.state()['player']['x'],original['x']+80)
        self.assertGreater(self.state()['coins'],0)
        self.page.keyboard.press('Space')
        self.page.wait_for_timeout(180)
        self.assertLess(self.state()['player']['y'],original['y']-35)
        self.page.wait_for_function('() => WeqromGame.getState().player.grounded')
        before=self.state()['player']['x']
        button=self.page.locator('[data-key=ArrowRight]')
        button.dispatch_event('pointerdown',{'pointerId':1})
        self.page.wait_for_timeout(250)
        button.dispatch_event('pointerup',{'pointerId':1})
        self.assertGreater(self.state()['player']['x'],before+25)
        self.page.locator('#restart').click()
        state=self.state()
        self.assertEqual((state['level'],state['score'],state['coins']),(1,0,0))

    def test_hostile_title_is_text_and_export_is_complete_reproducible(self):
        title='</script><script>window.gameInjected=1</script>'
        first=build({'genre':'survival','title':title,'theme':'violet','difficulty':'easy','seed':7})
        second=build({'genre':'survival','title':title,'theme':'violet','difficulty':'easy','seed':7})
        self.assertEqual(first,second)
        self.assertTrue(first['html'].startswith('<!doctype html>'))
        self.assertTrue(first['html'].endswith('</html>'))
        self.assertNotIn('/',first['filename'])
        self.page.set_content(first['html'])
        expect(self.page.locator('h1')).to_have_text(title)
        self.assertIsNone(self.page.evaluate('window.gameInjected'))
        self.page.locator('#primary-action').click()
        self.page.wait_for_function('() => WeqromGame.getState().time>.1')

    def test_invalid_constructor_inputs_are_rejected(self):
        for options in [{'genre':'unknown'},{'theme':'__proto__'},{'difficulty':'impossible'},{'title':'x'*81},{'title':'bad\nname'},{'seed':-1}]:
            self.assertIn('error',build({'genre':'survival','title':'Игра',**options}))


if __name__=='__main__':
    unittest.main(verbosity=2)
