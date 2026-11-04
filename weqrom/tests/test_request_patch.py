"""Checked actual request builder; no APK code or server request is executed."""
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from zipfile import ZipFile

from weqrom.tools import request_patch as target

REAL = Path('/tmp/weqrom-region14/source2-smali/X/03kn.smali')
JAR = Path('/workspace/.chits-tools/jadx/lib/jadx-1.5.3-all.jar')


class RequestPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = None
        if not REAL.exists():
            source = Path('/workspace/.chits-tools/tiktok/source/TikTok_You-5.0.47.4.3.apk')
            if not source.exists() or not JAR.exists():
                raise unittest.SkipTest('Pinned user-owned source and verified Smali tool are required')
            cls.temporary = tempfile.TemporaryDirectory()
            root = Path(cls.temporary.name)
            with ZipFile(source) as apk: data = apk.read(target.SOURCE_DEX)
            if hashlib.sha256(data).hexdigest() != target.SOURCE_DEX_SHA256:
                raise AssertionError('Pinned source DEX checksum differs')
            dex = root / 'original.dex';dex.write_bytes(data)
            subprocess.run(['java','-cp',str(JAR),'com.android.tools.smali.baksmali.Main','d',str(dex),
                '-o',str(root/'smali'),'--classes','LX/03kn;'],check=True,capture_output=True,text=True)
            cls.original = (root/'smali'/target.SOURCE_CLASS).read_text()
        else:
            cls.original = REAL.read_text()

    @classmethod
    def tearDownClass(cls):
        if cls.temporary is not None:cls.temporary.cleanup()

    def test_patches_only_final_map_and_keeps_all_original_instructions(self):
        result = target.patch_request_text(self.original)
        self.assertEqual(result.replace(target.INSERT, '', 1), self.original)
        self.assertEqual(result.count(target.INSERT), 1)
        self.assertIn('if-nez p3, :cond_3\n\n    return-void', result)
        self.assertGreater(result.index(target.INSERT), result.index('LX/05W5;->LIZ()'))
        self.assertGreater(result.index(target.INSERT), result.index('const-string/jumbo v0, "openudid"'))
        self.assertEqual(result.count('return-void'), self.original.count('return-void'))

    def test_unknown_class_method_body_and_repeat_patch_fail_closed(self):
        for invalid in (
            self.original.replace('LX/03kn;', 'LX/Unknown;'),
            self.original.replace(target.METHOD, target.METHOD+'X'),
            self.original.replace('.registers 16', '.registers 17'),
            self.original.replace('if-nez p3, :cond_3', 'if-eqz p3, :cond_3'),
            self.original.replace('"device_platform"', '"changed_platform"'),
            target.patch_request_text(self.original),
        ):
            with self.assertRaises(target.RequestPatchError): target.patch_request_text(invalid)

    def test_file_is_not_written_if_validation_or_dex_identity_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);path = root / target.SOURCE_CLASS;path.parent.mkdir()
            path.write_text(self.original+'\n')
            with self.assertRaises(target.RequestPatchError):target.patch_request_smali(root, dex_name='classes3.dex')
            self.assertEqual(path.read_text(), self.original+'\n')
            path.write_text(self.original.replace('.registers 16', '.registers 17'))
            before = path.read_bytes()
            with self.assertRaises(target.RequestPatchError):target.patch_request_smali(root)
            self.assertEqual(path.read_bytes(),before)

    def test_real_class_still_assembles_without_additional_registers(self):
        if not JAR.exists():self.skipTest('Verified Smali tool not cached')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);smali=root/'smali';path=smali/target.SOURCE_CLASS;path.parent.mkdir(parents=True)
            path.write_text(target.patch_request_text(self.original))
            output=root/'classes.dex'
            subprocess.run(['java','-cp',str(JAR),'com.android.tools.smali.smali.Main','a','-a','26',str(smali),'-o',str(output)],check=True,capture_output=True,text=True)
            data=output.read_bytes()
            self.assertTrue(data.startswith(b'dex\n'))
            self.assertIn(b'Lapp/weqrom/RequestRegion;',data)
            self.assertEqual(hashlib.sha1(data[32:]).digest(),data[12:32])


class AdvancedRegionSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=Path('/workspace/.chits-tools/tiktok/mod-research/smali/Z/bc.smali')
        if not source.exists():raise unittest.SkipTest('Pinned mod preferences disassembly not cached')
        cls.original=source.read_text()

    def test_preserves_setter_and_reloads_index_after_editor_reuses_p1(self):
        changed=target.patch_mod_region_text(self.original)
        self.assertEqual(changed.replace(target.MOD_INSERT,'',1),self.original)
        self.assertEqual(changed.count(target.MOD_INSERT),1)
        self.assertIn('iget v0, p0, LZ/bc;->b:I\n\n    invoke-static {v0}',changed)
        self.assertNotIn('invoke-static {p1}, Lapp/weqrom/RequestRegion;->onModRegionSelected(I)V',changed)
        self.assertGreater(changed.index(target.MOD_INSERT),changed.index(target.MOD_TAIL.split('\n')[0]))

    def test_rejects_repeat_patch_unknown_body_and_keeps_files_on_failure(self):
        for invalid in (target.patch_mod_region_text(self.original),self.original.replace('.registers 6\n','.registers 7\n')):
            with self.assertRaises(target.RequestPatchError):target.patch_mod_region_text(invalid)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'Z/bc.smali';path.parent.mkdir()
            path.write_text(self.original.replace(target.MOD_METHOD,target.MOD_METHOD+'X'))
            old=path.read_bytes()
            with self.assertRaises(target.RequestPatchError):target.patch_mod_region_selection(root)
            self.assertEqual(path.read_bytes(),old)

    def test_actual_preferences_class_assembles_after_callback(self):
        if not JAR.exists():self.skipTest('Verified Smali tool not cached')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);smali=root/'smali';path=smali/'Z/bc.smali';path.parent.mkdir(parents=True)
            path.write_text(target.patch_mod_region_text(self.original));output=root/'classes.dex'
            subprocess.run(['java','-cp',str(JAR),'com.android.tools.smali.smali.Main','a','-a','26',str(smali),'-o',str(output)],check=True,capture_output=True,text=True)
            data=output.read_bytes();self.assertIn(b'onModRegionSelected',data)
            self.assertEqual(hashlib.sha1(data[32:]).digest(),data[12:32])


if __name__=='__main__':unittest.main()
