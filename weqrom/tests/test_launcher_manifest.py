"""Real Android binary-XML checks for the ordinary Weqrom entry activities."""
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from zipfile import ZipFile, ZIP_DEFLATED

from weqrom.tools import axml
from weqrom.tools.launcher_manifest import (
    LAUNCHER_ACTIVITY, SETTINGS_ACTIVITY, INITIALIZER_PROVIDER, THEME_MATERIAL_LIGHT_NO_ACTION_BAR,
    LauncherManifestError, add_weqrom_launcher,
)


AAPT = Path('/workspace/.chits-tools/build-tools/android-15/aapt')
ANDROID_JAR = Path('/workspace/.chits-tools/platform/android-35/android.jar')


def attr(document, node, name):
    return node.attr(document.pool, name)


def named(document, name):
    return next(node for node in document.nodes if attr(document, node, 'name') is not None and attr(document, node, 'name').text(document.pool) == name)


class LauncherManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not AAPT.is_file() or not ANDROID_JAR.is_file():
            raise RuntimeError('Prepared Android AAPT/SDK tools are required')
        cls.temporary = tempfile.TemporaryDirectory(prefix='weqrom-launcher-manifest-')
        cls.base = Path(cls.temporary.name)
        (cls.base/'res/values').mkdir(parents=True)
        (cls.base/'res/values/strings.xml').write_text('<resources><string name="app_name">Weqrom</string></resources>')
        aliases = []
        for i in range(7):
            aliases.append(f'''<activity-alias android:name="old.example.icon.Alias{i}" android:targetActivity="old.example.TikTokMainActivity"
                android:enabled="{'true' if i == 0 else 'false'}" android:exported="true" android:icon="@android:drawable/ic_media_play" android:label="Weqrom {i}">
                <intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/></intent-filter>
              </activity-alias>''')
        cls.xml = '''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="old.example" android:versionCode="42" android:versionName="47.4.3-weqrom.1.3">
          <uses-sdk android:minSdkVersion="23" android:targetSdkVersion="35"/>
          <permission android:name="old.example.permission.CUSTOM" android:protectionLevel="signature"/>
          <uses-permission android:name="android.permission.INTERNET"/>
          <uses-permission android:name="old.example.permission.CUSTOM"/>
          <application android:name="old.example.HostApplication" android:label="@string/app_name" android:allowBackup="false" android:process="old.example.main">
            <meta-data android:name="theme" android:value="Keep literal string value"/>
            <activity android:name="old.example.TikTokMainActivity" android:exported="true" android:launchMode="singleTask" android:configChanges="orientation|screenSize" android:taskAffinity="old.example.feed">
              <intent-filter><action android:name="android.intent.action.VIEW"/><category android:name="android.intent.category.DEFAULT"/><data android:scheme="https" android:host="www.tiktok.com"/></intent-filter>
            </activity>
            ALIASES
            <activity-alias android:name="old.example.InternalAlias" android:targetActivity="old.example.TikTokMainActivity" android:exported="false"/>
            <activity android:name="old.example.DirectLauncher" android:exported="true">
              <intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/></intent-filter>
            </activity>
            <service android:name="old.example.Service" android:permission="old.example.permission.CUSTOM" android:process=":worker"/>
            <provider android:name="old.example.Provider" android:authorities="old.example.provider" android:exported="false"/>
          </application>
        </manifest>'''.replace('ALIASES', '\n'.join(aliases))
        cls.original, cls.original_apk = cls.compile(cls.xml, 'original')

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def compile(cls, xml, name):
        folder = cls.base/name
        folder.mkdir(exist_ok=True)
        manifest = folder/'AndroidManifest.xml'
        manifest.write_text(xml)
        output = folder/'fixture.apk'
        result = subprocess.run([str(AAPT), 'package', '-f', '-M', str(manifest), '-S', str(cls.base/'res'), '-I', str(ANDROID_JAR), '-F', str(output)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        with ZipFile(output) as archive:
            return archive.read('AndroidManifest.xml'), output

    def repackage(self, manifest, name='edited.apk'):
        output = self.base/name
        with ZipFile(self.original_apk) as original, ZipFile(output, 'w', ZIP_DEFLATED) as result:
            for entry in original.infolist():
                result.writestr(entry, manifest if entry.filename == 'AndroidManifest.xml' else original.read(entry))
        return output

    def test_two_activities_precede_all_aliases_and_all_seven_targets_change(self):
        result = axml.Manifest(add_weqrom_launcher(self.original))
        launcher = named(result, LAUNCHER_ACTIVITY)
        settings = named(result, SETTINGS_ACTIVITY)
        aliases = [node for node in result.nodes if result.pool.get(node.name) == 'activity-alias' and node.launcher]
        self.assertEqual(len(aliases), 7)
        for node in (launcher, settings):
            self.assertEqual(result.pool.get(node.name), 'activity')
            self.assertEqual(node.depth, 2)
            self.assertFalse(node.launcher)
            self.assertEqual(attr(result, node, 'exported').kind, 0x12)
            self.assertEqual(attr(result, node, 'exported').data, 0xFFFFFFFF)
            self.assertEqual(attr(result, node, 'theme').kind, 0x01)
            self.assertEqual(attr(result, node, 'theme').data, THEME_MATERIAL_LIGHT_NO_ACTION_BAR)
            self.assertEqual({result.pool.get(a.name) for a in node.attributes}, {'theme', 'name', 'exported'})
            self.assertLess(node.chunk_index, min(alias.chunk_index for alias in aliases))
            ids = [result.resource_ids[a.name] for a in node.attributes]
            self.assertEqual(ids, [0x01010000, 0x01010003, 0x01010010])
        for alias in aliases:
            self.assertEqual(attr(result, alias, 'targetActivity').text(result.pool), LAUNCHER_ACTIVITY)

    def test_all_other_nodes_attributes_permissions_classes_and_chunks_are_preserved(self):
        document = axml.Manifest(self.original)
        unknown = struct.pack('<HHI', 0x017F, 8, 12) + b'keep'
        original = bytearray(self.original + unknown)
        struct.pack_into('<I', original, 4, len(original))
        result = axml.Manifest(add_weqrom_launcher(bytes(original)))
        original_nodes = list(document.nodes)
        retained = [node for node in result.nodes if not (attr(result, node, 'name') is not None and attr(result, node, 'name').text(result.pool) in (LAUNCHER_ACTIVITY, SETTINGS_ACTIVITY, INITIALIZER_PROVIDER))]
        self.assertEqual(len(retained), len(original_nodes))
        for before, after in zip(original_nodes, retained):
            self.assertEqual(before.name, after.name)
            self.assertEqual(len(before.attributes), len(after.attributes))
            for old_attr, new_attr in zip(before.attributes, after.attributes):
                if before.launcher and document.pool.get(before.name) == 'activity-alias' and document.pool.get(old_attr.name) == 'targetActivity':
                    continue
                self.assertEqual(old_attr.build(), new_attr.build())
            if not (before.launcher and document.pool.get(before.name) == 'activity-alias'):
                self.assertEqual(before.build(), after.build())
        self.assertEqual(named(document, 'old.example.TikTokMainActivity').build(), named(result, 'old.example.TikTokMainActivity').build())
        self.assertEqual(result.chunks[-1][1], unknown)
        self.assertEqual(result.resource_ids[:len(document.resource_ids)], document.resource_ids)
        self.assertEqual(result.pool.strings[:len(document.pool.strings)], document.pool.strings)
        self.assertEqual(document.root.attr(document.pool, 'package', None).build(), result.root.attr(result.pool, 'package', None).build())
        # End nodes, namespace chunks and custom payloads remain byte-identical.
        self.assertEqual([chunk for kind,chunk in result.chunks if kind not in (1,0x180,0x102,0x103)],
                         [chunk for kind,chunk in axml.Manifest(bytes(original)).chunks if kind not in (1,0x180,0x102,0x103)])

    def test_android_aapt_accepts_badging_and_framework_theme_matches_sdk(self):
        changed = add_weqrom_launcher(self.original)
        apk = self.repackage(changed)
        tree = subprocess.run([str(AAPT), 'dump', 'xmltree', str(apk), 'AndroidManifest.xml'], capture_output=True, text=True, check=True).stdout
        badging = subprocess.run([str(AAPT), 'dump', 'badging', str(apk)], capture_output=True, text=True, check=True).stdout
        self.assertEqual(tree.count('android:targetActivity(0x01010202)="' + LAUNCHER_ACTIVITY + '"'), 7)
        self.assertEqual(tree.count('android:theme(0x01010000)=@0x01030241'), 2)
        self.assertIn("package: name='old.example' versionCode='42'", badging)
        # This fixture also has an independent direct launcher activity; AAPT's
        # one-line badging selects it, and that original entry stays unchanged.
        self.assertIn("launchable-activity: name='old.example.DirectLauncher'", badging)
        # Obtain the expected numeric framework ID from AAPT, not our encoder.
        themed, _apk = self.compile(self.xml.replace('android:launchMode="singleTask"', 'android:theme="@android:style/Theme.Material.Light.NoActionBar" android:launchMode="singleTask"'), 'sdk-theme')
        expected = axml.Manifest(themed)
        expected_theme = attr(expected, named(expected, 'old.example.TikTokMainActivity'), 'theme')
        self.assertEqual(expected_theme.kind, 0x01)
        self.assertEqual(expected_theme.data, THEME_MATERIAL_LIGHT_NO_ACTION_BAR)

    def test_missing_theme_mapping_is_added_without_reusing_equal_string_value(self):
        before = axml.Manifest(self.original)
        literal_theme = before.pool.strings.index('theme')
        self.assertTrue(literal_theme >= len(before.resource_ids) or before.resource_ids[literal_theme] != 0x01010000)
        result = axml.Manifest(add_weqrom_launcher(self.original))
        theme = attr(result, named(result, LAUNCHER_ACTIVITY), 'theme')
        self.assertNotEqual(theme.name, literal_theme)
        self.assertEqual(result.resource_ids[theme.name], 0x01010000)
        self.assertEqual(result.pool.get(literal_theme), 'theme')

    def test_private_initializer_authority_order_and_framework_ids_match_sdk(self):
        result = axml.Manifest(add_weqrom_launcher(self.original))
        provider = named(result, INITIALIZER_PROVIDER)
        self.assertEqual(result.pool.get(provider.name), 'provider')
        self.assertEqual(provider.depth, 2)
        self.assertEqual(attr(result, provider, 'exported').data, 0)
        self.assertEqual(attr(result, provider, 'authorities').text(result.pool), 'old.example.weqrom.bootstrap')
        self.assertEqual(attr(result, provider, 'initOrder').data, 100)
        self.assertEqual([result.resource_ids[a.name] for a in provider.attributes], [0x01010003,0x01010010,0x01010018,0x0101001A])
        aliases = [n for n in result.nodes if result.pool.get(n.name)=='activity-alias']
        self.assertLess(provider.chunk_index,min(n.chunk_index for n in aliases))
        reference_xml = self.xml.replace('<meta-data', '<provider android:name="sdk.example.Init" android:authorities="old.example.weqrom.bootstrap" android:exported="false" android:initOrder="100"/><meta-data', 1)
        compiled,_ = self.compile(reference_xml,'sdk-initializer')
        sdk = axml.Manifest(compiled)
        expected = named(sdk,'sdk.example.Init')
        for name in ('name','exported','authorities','initOrder'):
            actual_attr = attr(result,provider,name)
            sdk_attr = attr(sdk,expected,name)
            self.assertEqual(result.resource_ids[actual_attr.name],sdk.resource_ids[sdk_attr.name])
            self.assertEqual(actual_attr.kind,sdk_attr.kind)
        with self.assertRaises(LauncherManifestError):
            add_weqrom_launcher(compiled)

    def test_duplicate_owned_names_absent_aliases_and_malformed_targets_fail_closed(self):
        with self.assertRaises(LauncherManifestError):
            add_weqrom_launcher(add_weqrom_launcher(self.original))
        no_aliases = axml.Manifest(self.original)
        for node in no_aliases.nodes:
            if no_aliases.pool.get(node.name) == 'action':
                value = attr(no_aliases, node, 'name')
                if value.text(no_aliases.pool) == 'android.intent.action.MAIN':
                    value.set_text(no_aliases.pool, 'old.example.CUSTOM')
        with self.assertRaises(LauncherManifestError):
            add_weqrom_launcher(no_aliases.build())
        missing_target = axml.Manifest(self.original)
        first = next(node for node in missing_target.nodes if missing_target.pool.get(node.name) == 'activity-alias')
        first.attributes.remove(attr(missing_target, first, 'targetActivity'))
        with self.assertRaises(LauncherManifestError):
            add_weqrom_launcher(missing_target.build())


if __name__ == '__main__':
    unittest.main(verbosity=2)
