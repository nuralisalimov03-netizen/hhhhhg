"""Manifest editing checks using real AAPT binary XML and isolated fixtures.

No application is installed or executed. Source APK manifests are never changed.
"""
from pathlib import Path
import importlib.util
import struct
import subprocess
import tempfile
import unittest
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('weqrom_axml', ROOT / 'tools/axml.py')
axml = importlib.util.module_from_spec(spec)
spec.loader.exec_module(axml)
AAPT = Path('/workspace/.chits-tools/build-tools/android-15/aapt')
ANDROID = Path('/workspace/.chits-tools/platform/android-35/android.jar')


def pool_chunk(values, utf8, styles=False):
    offsets, strings = [], bytearray()
    for value in values:
        offsets.append(len(strings))
        utf16 = value.encode('utf-16le')
        if utf8:
            encoded = value.encode('utf-8')
            strings.extend(axml._encode_length8(len(utf16) // 2) + axml._encode_length8(len(encoded)) + encoded + b'\0')
        else:
            strings.extend(axml._encode_length16(len(utf16) // 2) + utf16 + b'\0\0')
    strings.extend(b'\0' * (-len(strings) % 4))
    spans = struct.pack('<6I', 1, 0, 1, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF) if styles else b''
    count = 1 if styles else 0
    start = 28 + 4 * (len(values) + count)
    style_start = start + len(strings) if styles else 0
    size = start + len(strings) + len(spans)
    return struct.pack('<HHI5I', 1, 28, size, len(values), count, (0x100 if utf8 else 0) | 1, start, style_start) + struct.pack('<' + 'I' * len(offsets), *offsets) + (struct.pack('<I', 0) if styles else b'') + strings + spans


def attributes(node):
    return {(x['namespace'], x['name']): x for x in node['attributes']}


def android_attr(node, name):
    return attributes(node).get((axml.ANDROID, name))


class StringPoolTests(unittest.TestCase):
    def test_utf8_and_utf16_preserve_indices_styles_and_unicode_lengths(self):
        for utf8 in (False, True):
            with self.subTest(utf8=utf8):
                original = pool_chunk(['styled text', 'b', 'Привет 🚀', 'x' * 160], utf8, styles=True)
                pool = axml.StringPool(original)
                initial = list(pool.strings)
                self.assertEqual(pool.build(), original)
                index = pool.intern('Weqrom 🚀')
                self.assertEqual(index, 4)
                changed = axml.StringPool(pool.build())
                self.assertEqual(changed.strings[:4], initial)
                self.assertEqual(changed.strings[index], 'Weqrom 🚀')
                self.assertEqual(changed.style_offsets, pool.style_offsets)
                self.assertEqual(changed.style_data, pool.style_data)
                self.assertFalse(changed.flags & 1, 'Appending text invalidates sorted-pool flag')
                self.assertEqual(changed.flags & 0x100, 0x100 if utf8 else 0)

    def test_extended_utf16_length_and_duplicate_text_are_safe(self):
        original = pool_chunk(['x' * 40000, 'duplicate', 'duplicate'], False)
        pool = axml.StringPool(original)
        pool.intern('Weqrom')
        decoded = axml.StringPool(pool.build())
        self.assertEqual(decoded.strings, pool.strings)
        self.assertEqual(decoded.strings[1:3], ['duplicate', 'duplicate'])

    def test_malformed_string_offsets_and_terminators_are_rejected(self):
        blob = bytearray(pool_chunk(['abc'], True))
        struct.pack_into('<I', blob, 28, 10000)
        with self.assertRaises(axml.AXMLError):
            axml.StringPool(blob)
        blob = bytearray(pool_chunk(['abc'], True))
        start = struct.unpack_from('<I', blob, 20)[0]
        blob[start + 5] = 1
        with self.assertRaises(axml.AXMLError):
            axml.StringPool(blob)


class ManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not AAPT.is_file() or not ANDROID.is_file():
            raise RuntimeError('Prepared Android AAPT/SDK tools are required')
        cls.temp = tempfile.TemporaryDirectory(prefix='weqrom-manifest-test-')
        cls.base = Path(cls.temp.name)
        (cls.base / 'res/values').mkdir(parents=True)
        (cls.base / 'res/values/strings.xml').write_text('<resources><string name="app_name">Original</string><string name="alias_name">Alternate</string></resources>')
        cls.xml = '''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="old.example.app" android:versionCode="1" android:versionName="1.0">
          <uses-sdk android:minSdkVersion="26" android:targetSdkVersion="35"/>
          <permission android:name="old.example.app.permission.LOCAL" android:protectionLevel="signature"/>
          <uses-permission android:name="old.example.app.permission.LOCAL"/>
          <uses-permission android:name="android.permission.INTERNET"/>
          <application android:name=".App" android:label="@string/app_name" android:allowBackup="false">
            <activity android:name=".Main" android:label="Main title" android:exported="true">
              <intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/></intent-filter>
            </activity>
            <activity-alias android:name=".Alternate" android:targetActivity=".Main" android:label="@string/alias_name" android:enabled="false" android:exported="true">
              <intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/></intent-filter>
            </activity-alias>
            <activity android:name="Internal" android:label="Keep internal" android:parentActivityName=".Main" android:taskAffinity="old.example.app.tasks"/>
            <service android:name=".Worker" android:permission="old.example.app.permission.LOCAL"/>
            <provider android:name=".Provider" android:authorities="old.example.app.files;old.example.app.media" android:readPermission="old.example.app.permission.LOCAL"/>
          </application>
          <instrumentation android:name=".Tests" android:targetPackage="old.example.app"/>
        </manifest>'''
        cls.original = cls.compile(cls.xml, 'original.apk')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def compile(cls, xml, name):
        folder = cls.base / (name + '.source')
        folder.mkdir(exist_ok=True)
        manifest = folder / 'AndroidManifest.xml'
        manifest.write_text(xml)
        apk = cls.base / name
        result = subprocess.run([str(AAPT), 'package', '-f', '-M', str(manifest), '-S', str(cls.base/'res'), '-I', str(ANDROID), '-F', str(apk)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        with ZipFile(apk) as z:
            return z.read('AndroidManifest.xml')

    def repackage(self, manifest, name='edited.apk'):
        path = self.base / name
        with ZipFile(self.base/'original.apk') as source, ZipFile(path, 'w', ZIP_DEFLATED) as output:
            for info in source.infolist():
                output.writestr(info, manifest if info.filename == 'AndroidManifest.xml' else source.read(info))
        return path

    def test_actual_aapt_manifest_rebrands_all_launcher_overrides(self):
        changed = axml.rebrand_manifest(self.original)
        result = axml.inspect_manifest(changed)
        self.assertEqual(result['package'], 'old.example.app')
        labels = [android_attr(n,'label')['value'] for n in result['nodes'] if n['name']=='application' or n['launcher']]
        self.assertEqual(labels, ['Weqrom', 'Weqrom', 'Weqrom'])
        internal = next(n for n in result['nodes'] if n['name']=='activity' and not n['launcher'])
        self.assertEqual(android_attr(internal,'label')['value'], 'Keep internal')
        # Confirm Android's own parser accepts and exposes the edited values.
        apk = self.repackage(changed)
        parsed = subprocess.run([str(AAPT),'dump','badging',str(apk)],capture_output=True,text=True,check=True).stdout
        self.assertIn("application-label:'Weqrom'", parsed)
        self.assertIn("launchable-activity: name='old.example.app.Main'  label='Weqrom'", parsed)

    def test_labels_do_not_change_other_attributes_or_unknown_chunks(self):
        original = axml.Manifest(self.original)
        # Keep a vendor extension byte-for-byte; it is deliberately unknown here.
        unknown = struct.pack('<HHI', 0x017F, 8, 12) + b'abcd'
        data = bytearray(self.original + unknown)
        struct.pack_into('<I', data, 4, len(data))
        changed = axml.Manifest(axml.rebrand_manifest(bytes(data)))
        self.assertEqual(changed.chunks[-1][1], unknown)
        for before, after in zip(original.nodes, changed.nodes):
            for a, b in zip(before.attributes, after.attributes):
                if before.attr(original.pool,'label') is a and (original.pool.get(before.name)=='application' or before.launcher):
                    continue
                self.assertEqual(a.build(), b.build())
        for (kind, before), (_, after) in zip(original.chunks, changed.chunks):
            if kind not in (1, 0x102):
                self.assertEqual(before, after)

    def test_missing_application_label_can_be_added(self):
        data = self.compile(self.xml.replace(' android:label="@string/app_name"',''), 'no-label.apk')
        changed = axml.rebrand_manifest(data)
        parsed = axml.inspect_manifest(changed)
        application = next(n for n in parsed['nodes'] if n['name']=='application')
        self.assertEqual(android_attr(application,'label')['value'],'Weqrom')
        apk = self.repackage(changed,'added-label.apk')
        result = subprocess.run([str(AAPT),'dump','badging',str(apk)],capture_output=True,text=True,check=True).stdout
        self.assertIn("application-label:'Weqrom'", result)

    def test_optional_clone_keeps_class_identity_and_renames_only_own_manifest_names(self):
        changed = axml.inspect_manifest(axml.rebrand_manifest(self.original,package_name='new.weqrom.app'))
        self.assertEqual(changed['package'],'new.weqrom.app')
        app = next(n for n in changed['nodes'] if n['name']=='application')
        self.assertEqual(android_attr(app,'name')['value'],'old.example.app.App')
        alias = next(n for n in changed['nodes'] if n['name']=='activity-alias')
        self.assertEqual(android_attr(alias,'targetActivity')['value'],'old.example.app.Main')
        provider = next(n for n in changed['nodes'] if n['name']=='provider')
        self.assertEqual(android_attr(provider,'authorities')['value'],'new.weqrom.app.files;new.weqrom.app.media')
        self.assertEqual(android_attr(provider,'readPermission')['value'],'new.weqrom.app.permission.LOCAL')
        permissions = [android_attr(n,'name')['value'] for n in changed['nodes'] if n['name']=='uses-permission']
        self.assertIn('android.permission.INTERNET',permissions)
        self.assertIn('new.weqrom.app.permission.LOCAL',permissions)
        apk = self.repackage(axml.rebrand_manifest(self.original,package_name='new.weqrom.app'),'clone.apk')
        result = subprocess.run([str(AAPT),'dump','badging',str(apk)],capture_output=True,text=True,check=True).stdout
        self.assertIn("package: name='new.weqrom.app'",result)
        self.assertIn("launchable-activity: name='old.example.app.Main'",result)

    def test_clone_requires_explicit_mapping_for_external_authorities(self):
        data = self.compile(self.xml.replace('old.example.app.files;old.example.app.media','external.provider'), 'external.apk')
        with self.assertRaises(axml.AXMLError):
            axml.rebrand_manifest(data,package_name='new.weqrom.app')
        parsed = axml.inspect_manifest(axml.rebrand_manifest(data,package_name='new.weqrom.app',authorities_map={'external.provider':'new.weqrom.app.external'}))
        provider = next(n for n in parsed['nodes'] if n['name']=='provider')
        self.assertEqual(android_attr(provider,'authorities')['value'],'new.weqrom.app.external')

    def test_invalid_binary_and_invalid_labels_are_rejected(self):
        for invalid in [b'',b'<manifest/>',self.original[:-1]]:
            with self.subTest(data=invalid[:8]), self.assertRaises(axml.AXMLError):
                axml.rebrand_manifest(invalid)
        for label in ['', '  ', 'x\0y']:
            with self.assertRaises(axml.AXMLError):
                axml.rebrand_manifest(self.original,label=label)


if __name__ == '__main__':
    unittest.main(verbosity=2)
