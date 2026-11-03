"""The Weqrom ABI fork must cover all compatibility consumers and retain checks."""
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from weqrom.tools.loader_patch import (
    LoaderPatchError, ORIGINAL_STABLE_API, SURFACES, WEQROM_STABLE_API,
    patch_loader_smali, patch_loader_text,
)


def fixture(name):
    _relative, declaration, signature, register, anchors = SURFACES[name]
    return (declaration + '\n.super Ljava/lang/Object;\n' + signature + '\n    .registers 7\n'
        + '    const ' + register + ', ' + hex(ORIGINAL_STABLE_API) + '\n'
        + '\n'.join('    ' + value for value in anchors) + '\n.end method\n'
        + '.method public untouched()V\n    invoke-static {}, Lcrypto/Signature;->verify()V\n    return-void\n.end method\n')


class LoaderPatchTests(unittest.TestCase):
    def test_all_consumers_change_only_the_abi_constant_and_retain_comparisons(self):
        for name in SURFACES:
            original = fixture(name)
            patched = patch_loader_text(original, name)
            self.assertEqual(patched.replace(hex(WEQROM_STABLE_API), hex(ORIGINAL_STABLE_API)), original)
            self.assertEqual(patched.count(hex(WEQROM_STABLE_API)), 1)
            self.assertNotIn(hex(ORIGINAL_STABLE_API), patched)
            self.assertIn('Lcrypto/Signature;->verify()V', patched)
        # Cached-slot and signed-package validation still compare manifest ABI
        # to their expected constant; the original return/throw branches remain.
        validator = patch_loader_text(fixture('classes50.dex'), 'classes50.dex')
        self.assertIn('iget p1, p0, Lc/b;->c:I', validator)
        self.assertIn('if-ne v0, p1, :cond_12', validator)
        self.assertIn('throw p1', validator)
        self.assertNotEqual(ORIGINAL_STABLE_API, WEQROM_STABLE_API)

    def test_unknown_method_missing_duplicate_constant_and_removed_check_fail_closed(self):
        for name in SURFACES:
            original = fixture(name)
            with self.assertRaises(LoaderPatchError):
                patch_loader_text(original.replace(hex(ORIGINAL_STABLE_API), '0x123'), name)
            with self.assertRaises(LoaderPatchError):
                patch_loader_text(original + '\n# ' + hex(ORIGINAL_STABLE_API), name)
            with self.assertRaises(LoaderPatchError):
                patch_loader_text(original.replace(SURFACES[name][2], '.method public changed()V'), name)
        with self.assertRaises(LoaderPatchError):
            patch_loader_text(fixture('classes50.dex').replace('if-ne v0, p1, :cond_12', 'nop'), 'classes50.dex')

    def test_invalid_or_unchanged_compatibility_identity_is_rejected(self):
        for invalid in (ORIGINAL_STABLE_API, 0, -1, 2147483648, True, '2026110301', None):
            with self.assertRaises(LoaderPatchError):
                patch_loader_text(fixture('classes52.dex'), 'classes52.dex', invalid)
        with self.assertRaises(LoaderPatchError):
            patch_loader_text(fixture('classes52.dex'), 'unknown.dex')

    def test_combined_root_is_checked_transactionally_before_any_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, data in SURFACES.items():
                path = root/data[0]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(fixture(name))
            validator = root/SURFACES['classes50.dex'][0]
            validator.write_text('unexpected user data')
            with self.assertRaises(LoaderPatchError):
                patch_loader_smali(root)
            self.assertEqual((root/SURFACES['classes46.dex'][0]).read_text(), fixture('classes46.dex'))
            validator.write_text(fixture('classes50.dex'))
            report = patch_loader_smali(root)
            self.assertEqual(set(report['modified_dex']), set(SURFACES))
            self.assertFalse(report['old_tiktok_you_ota_compatible'])
            self.assertFalse(report['cache_files_manually_modified'])

    def test_real_pinned_classes_assemble_with_unchanged_other_bytecode(self):
        jar = Path('/workspace/.chits-tools/jadx/lib/jadx-1.5.3-all.jar')
        bases = {
            'classes46.dex': Path('/tmp/weqrom-loader-review/smali'),
            'classes50.dex': Path('/tmp/weqrom-loader-review/smali50'),
            'classes52.dex': Path('/tmp/weqrom-loader-review/smali52'),
        }
        if not jar.is_file() or any(not (bases[name]/data[0]).is_file() for name,data in SURFACES.items()):
            self.skipTest('Pinned loader disassembly/tool cache unavailable')
        for name, base in bases.items():
            with self.subTest(dex=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)/'smali'
                shutil.copytree(base, root)
                path = root/SURFACES[name][0]
                original = path.read_text()
                report = patch_loader_smali(root, dex_name=name)
                self.assertEqual(report['modified_dex'], [name])
                self.assertEqual(path.read_text().replace(hex(WEQROM_STABLE_API), hex(ORIGINAL_STABLE_API)), original)
                output = Path(temporary)/'patched.dex'
                subprocess.run(['java', '-cp', str(jar), 'com.android.tools.smali.smali.Main',
                    'a', str(root), '--api', '26', '-o', str(output)], check=True, capture_output=True)
                assembled = output.read_bytes()
                self.assertTrue(assembled.startswith(b'dex\n'))
                self.assertGreater(struct.unpack_from('<I', assembled, 96)[0], 0)
                self.assertEqual(assembled.count(struct.pack('<I', WEQROM_STABLE_API)), 1)
                self.assertEqual(assembled.count(struct.pack('<I', ORIGINAL_STABLE_API)), 0)


if __name__ == '__main__':
    unittest.main()
