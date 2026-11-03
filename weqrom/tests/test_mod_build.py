import hashlib
import json
from pathlib import Path
import unittest
from zipfile import ZipFile

from weqrom.tools.mod_build import patch_startup, refresh_manifest, START_HOOK, MOD_SHA
from weqrom.tools.build import release_manifest, RELEASE_CODE, RELEASE_VERSION
from weqrom.tools.axml import inspect_manifest
from weqrom.tools.loader_patch import WEQROM_STABLE_API


class ModBuildTests(unittest.TestCase):
    def test_startup_initializes_after_mod_preferences_and_keeps_integrity_call(self):
        original = '\n'.join(['.method start', START_HOOK, '    invoke-static {}, LZ/ye;->a()V',
            '    invoke-static {p1}, Lcom/ss/android/ugc/aweme/utils/CommonUtils;->verifySignatureNative(Landroid/content/Context;)Z', '.end method'])
        patched = patch_startup(original)
        self.assertLess(patched.index(START_HOOK), patched.index('ModIntegration;->initialize'))
        self.assertLess(patched.index('ModIntegration;->initialize'), patched.index('LZ/ye;->a'))
        self.assertIn('CommonUtils;->verifySignatureNative', patched)
        for invalid in (original.replace(START_HOOK, ''), original + START_HOOK, patched):
            with self.assertRaises(ValueError):patch_startup(invalid)

    def test_payload_manifest_refresh_preserves_loader_compatibility(self):
        original = {'payloadType':'full','variant':'release','dynamicVersion':1792428213,
            'stableApiVersion':1792428213,'targetDexSha256':MOD_SHA,'targetSize':1018316,
            'dexFiles':[{'name':'classes.dex','payload':'classes.dex','targetSha256':MOD_SHA,'targetSize':1018316}],
            'hashes':{'classes.dex':MOD_SHA}}
        payload = b'new built mod DEX fixture'
        updated = json.loads(refresh_manifest(json.dumps(original), payload))
        digest = hashlib.sha256(payload).hexdigest()
        self.assertEqual(updated['targetDexSha256'], digest)
        self.assertEqual(updated['dexFiles'][0]['targetSha256'], digest)
        self.assertEqual(updated['hashes']['classes.dex'], digest)
        self.assertEqual(updated['targetSize'], len(payload))
        self.assertEqual(updated['dexFiles'][0]['targetSize'], len(payload))
        self.assertEqual(updated['dynamicVersion'],WEQROM_STABLE_API)
        self.assertEqual(updated['stableApiVersion'],WEQROM_STABLE_API)
        for key in ('variant','payloadType'):
            self.assertEqual(updated[key], original[key])
        original['targetDexSha256'] = '0'*64
        with self.assertRaises(ValueError):refresh_manifest(json.dumps(original), payload)

    def test_real_manifest_version_upgrade_keeps_package_and_launcher_identity(self):
        source = Path('/workspace/.chits-tools/tiktok/source/TikTok_You-5.0.47.4.3.apk')
        if not source.exists():self.skipTest('Pinned user-owned input is not cached')
        with ZipFile(source) as apk:data = apk.read('AndroidManifest.xml')
        before = inspect_manifest(data)
        after = inspect_manifest(release_manifest(data))
        self.assertEqual(before['package'], after['package'])
        root = after['nodes'][0]
        attrs = {a['name']:a['value'] for a in root['attributes']}
        self.assertEqual(attrs['versionCode'], RELEASE_CODE)
        self.assertEqual(attrs['versionName'], RELEASE_VERSION)
        for node in after['nodes']:
            if node['name']=='uses-sdk':
                values={a['name']:a['value'] for a in node['attributes']}
                self.assertEqual(values['minSdkVersion'],26)
                self.assertEqual(values['targetSdkVersion'],36)
        for old,new in zip(before['nodes'],after['nodes']):
            self.assertEqual(old['name'],new['name'])
            old_values={a['name']:a['value'] for a in old['attributes']}
            new_values={a['name']:a['value'] for a in new['attributes']}
            if old['launcher']:
                self.assertEqual(new_values.get('label','Weqrom'),'Weqrom')
            for key in ('name','targetActivity','authorities','exported','enabled','process'):
                self.assertEqual(old_values.get(key),new_values.get(key))


if __name__ == '__main__':unittest.main()
