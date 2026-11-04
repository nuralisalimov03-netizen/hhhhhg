"""Check the two actual append-only URL builders, without running APK code."""
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from zipfile import ZipFile

from weqrom.tools import request_patch as target

SOURCE = Path('/workspace/.chits-tools/tiktok/source/TikTok_You-5.0.47.4.3.apk')
JAR = Path('/workspace/.chits-tools/jadx/lib/jadx-1.5.3-all.jar')


def method(text, signature):
    return re.search(r'(?ms)^' + re.escape(signature) + r'\n.*?^\.end method$', text).group()


class RequestUrlPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SOURCE.is_file() or not JAR.is_file():
            raise unittest.SkipTest('Pinned user-owned APK and verified Smali tool are required')
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        with ZipFile(SOURCE) as apk:
            data = apk.read(target.SOURCE_DEX)
        if hashlib.sha256(data).hexdigest() != target.SOURCE_DEX_SHA256:
            raise AssertionError('Pinned source DEX checksum differs')
        dex = cls.root / 'source.dex'
        dex.write_bytes(data)
        subprocess.run(['java', '-cp', str(JAR), 'com.android.tools.smali.baksmali.Main',
                        'd', str(dex), '-o', str(cls.root / 'source'),
                        '--classes', 'LX/03kn;,LX/03J2;'],
                       check=True, capture_output=True, text=True)
        cls.original = (cls.root / 'source' / target.SOURCE_CLASS).read_text()
        cls.common_original = (cls.root / 'source' / target.COMMON_URL_CLASS).read_text()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'temporary'):
            cls.temporary.cleanup()

    def test_pins_both_original_methods_from_checked_apk(self):
        for original, signature, checksum in (
            (self.original, target.URL_METHOD, target.URL_METHOD_SHA256),
            (self.common_original, target.COMMON_URL_METHOD, target.COMMON_URL_METHOD_SHA256),
        ):
            self.assertEqual(hashlib.sha256(method(original, signature).encode()).hexdigest(), checksum)

    def test_instance_url_hook_follows_append_and_precedes_final_to_string(self):
        changed = target.patch_request_url_text(self.original)
        self.assertEqual(changed.replace(target.URL_INSERT, '', 1), self.original)
        self.assertEqual(changed.count(target.URL_INSERT), 1)
        body = method(changed, target.URL_METHOD)
        self.assertIn('if-eqz v0, :cond_b\n\n    return-object v1', body)
        self.assertLess(body.index('->append(Ljava/lang/String;)'), body.index(target.URL_INSERT))
        self.assertLess(body.index(target.URL_INSERT), body.rindex('->toString()Ljava/lang/String;'))
        self.assertIn('invoke-static {p2}, Lapp/weqrom/RequestRegion;->rewriteUrl', body)

    def test_static_url_guards_still_skip_hook_on_disabled_null_or_empty(self):
        changed = target.patch_common_url_text(self.common_original)
        self.assertEqual(changed.replace(target.COMMON_URL_INSERT, '', 1), self.common_original)
        self.assertEqual(changed.count(target.COMMON_URL_INSERT), 1)
        body = method(changed, target.COMMON_URL_METHOD)
        self.assertIn('if-eqz v0, :cond_6e\n\n    if-eqz p0, :cond_6e', body)
        self.assertIn('if-eqz v0, :cond_11\n\n    return-void', body)
        self.assertLess(body.index('->append(Ljava/lang/String;)'), body.index(target.COMMON_URL_INSERT))
        self.assertLess(body.index(target.COMMON_URL_INSERT), body.index('    :cond_6e\n'))
        self.assertIn('invoke-static {p0}, Lapp/weqrom/RequestRegion;->rewriteUrl', body)

    def test_map_and_url_hooks_compose_in_either_order(self):
        map_first = target.patch_request_url_text(target.patch_request_text(self.original))
        url_first = target.patch_request_text(target.patch_request_url_text(self.original))
        self.assertEqual(map_first, url_first)
        self.assertEqual(map_first.replace(target.INSERT, '', 1).replace(target.URL_INSERT, '', 1), self.original)
        with self.assertRaises(target.RequestPatchError):
            target.patch_request_text(map_first)
        with self.assertRaises(target.RequestPatchError):
            target.patch_request_url_text(map_first)

    def test_unknown_source_signatures_guards_registers_and_repeat_hooks_fail_closed(self):
        for original, signature, patch, owner, registers, guard in (
            (self.original, target.URL_METHOD, target.patch_request_url_text, 'LX/03kn;',
             '.registers 13\n', 'if-eqz v0, :cond_b'),
            (self.common_original, target.COMMON_URL_METHOD, target.patch_common_url_text, 'LX/03J2;',
             '.registers 10\n', 'if-eqz p0, :cond_6e'),
        ):
            for invalid in (
                original.replace(owner, 'LX/Unknown;'),
                original.replace(signature, signature + 'X'),
                original.replace(registers, '.registers 20\n'),
                original.replace(guard, guard.replace('if-eqz', 'if-nez')),
                original.replace('->getQueryParameterNames(', '->otherQueryParameterNames('),
                patch(original),
            ):
                with self.subTest(signature=signature, invalid=invalid[:80]):
                    with self.assertRaises(target.RequestPatchError):
                        patch(invalid)

    def _write_sources(self, root):
        for relative, original in ((target.SOURCE_CLASS, self.original),
                                   (target.COMMON_URL_CLASS, self.common_original)):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(original)

    def test_validates_all_inputs_before_writing_either_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_sources(root)
            first, second = root / target.SOURCE_CLASS, root / target.COMMON_URL_CLASS
            second.write_text(self.common_original.replace(target.COMMON_URL_METHOD,
                                                         target.COMMON_URL_METHOD + 'X'))
            before = first.read_bytes(), second.read_bytes()
            with self.assertRaises(target.RequestPatchError):
                target.patch_request_urls_smali(root)
            self.assertEqual((first.read_bytes(), second.read_bytes()), before)
            self._write_sources(root)
            with self.assertRaises(target.RequestPatchError):
                target.patch_request_urls_smali(root, dex_name='classes3.dex')
            self.assertEqual(first.read_text(), self.original)
            self.assertEqual(second.read_text(), self.common_original)
            second.unlink()
            with self.assertRaises(target.RequestPatchError):
                target.patch_request_urls_smali(root)
            self.assertEqual(first.read_text(), self.original)

    def test_refuses_symlink_and_preserves_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_sources(root)
            path = root / target.COMMON_URL_CLASS
            destination = root / 'unrelated.smali'
            destination.write_text(self.common_original)
            path.unlink()
            path.symlink_to(destination)
            with self.assertRaises(target.RequestPatchError):
                target.patch_request_urls_smali(root)
            self.assertEqual(destination.read_text(), self.common_original)
            self.assertEqual((root / target.SOURCE_CLASS).read_text(), self.original)

    def test_file_patch_reports_both_exact_methods(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_sources(root)
            audit = target.patch_request_urls_smali(root)
            self.assertEqual((root / target.SOURCE_CLASS).read_text(), target.patch_request_url_text(self.original))
            self.assertEqual((root / target.COMMON_URL_CLASS).read_text(), target.patch_common_url_text(self.common_original))
            self.assertEqual(audit['extra_registers'], 0)
            self.assertTrue(audit['initial_guards_and_early_returns_unchanged'])
            self.assertEqual([entry['class'] for entry in audit['source_methods']], ['LX/03kn;', 'LX/03J2;'])
            self.assertEqual([entry['original_method_sha256'] for entry in audit['source_methods']],
                             [target.URL_METHOD_SHA256, target.COMMON_URL_METHOD_SHA256])

    def test_both_actual_classes_assemble_and_roundtrip_with_existing_map_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            smali = root / 'smali'
            self._write_sources(smali)
            target.patch_request_smali(smali)
            target.patch_request_urls_smali(smali)
            dex = root / 'classes.dex'
            subprocess.run(['java', '-cp', str(JAR), 'com.android.tools.smali.smali.Main',
                            'a', '-a', '26', str(smali), '-o', str(dex)],
                           check=True, capture_output=True, text=True)
            data = dex.read_bytes()
            self.assertTrue(data.startswith(b'dex\n'))
            self.assertEqual(hashlib.sha1(data[32:]).digest(), data[12:32])
            subprocess.run(['java', '-cp', str(JAR), 'com.android.tools.smali.baksmali.Main',
                            'd', str(dex), '-o', str(root / 'roundtrip')],
                           check=True, capture_output=True, text=True)
            for relative, signature, register_count, argument in (
                (target.SOURCE_CLASS, target.URL_METHOD, 13, 'p2'),
                (target.COMMON_URL_CLASS, target.COMMON_URL_METHOD, 10, 'p0'),
            ):
                body = method((root / 'roundtrip' / relative).read_text(), signature)
                self.assertIn(f'    .registers {register_count}\n', body)
                self.assertEqual(body.count('->rewriteUrl(Ljava/lang/StringBuilder;)V'), 1)
                self.assertIn('invoke-static {' + argument + '}, Lapp/weqrom/RequestRegion;', body)


if __name__ == '__main__':
    unittest.main()
