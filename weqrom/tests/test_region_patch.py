from pathlib import Path
import tempfile
import unittest

from weqrom.tools.region_patch import RegionPatchError, patch_default_region, patch_region_defaults


FIXTURE = '''.class public final LZ/bc;
.super Ljava/lang/Object;
.method static constructor <clinit>()V
    .registers 4
    sget-object v2, LZ/ue;->a:[LZ/te;
    iget-object v2, v2, LZ/te;->d:Ljava/lang/String;
    const-string v3, "kz"
    invoke-virtual {v2, v3}, Ljava/lang/String;->equalsIgnoreCase(Ljava/lang/String;)Z
    sput v0, LZ/bc;->I:I
    return-void
.end method
.method public another()V
    const-string v3, "kz"
    return-void
.end method
'''


class RegionPatchTests(unittest.TestCase):
    def test_only_checked_static_fallback_changes(self):
        result = patch_default_region(FIXTURE)
        self.assertEqual(result, FIXTURE.replace('const-string v3, "kz"', 'const-string v3, "by"', 1))
        self.assertEqual(result.count('"kz"'), 1)

    def test_unfamiliar_class_or_method_or_region_loop_is_refused(self):
        for original, changed in (
            ("LZ/bc;", "LZ/not_bc;"),
            ("<clinit>()V", "initialize()V"),
            ("LZ/ue;->a:[LZ/te;", "LZ/ue;->unknown:[LZ/te;"),
            ("LZ/te;->d:Ljava/lang/String;", "LZ/te;->c:Ljava/lang/String;"),
            ('"kz"', '"us"'),
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(RegionPatchError):
                    patch_default_region(FIXTURE.replace(original, changed, 1))

    def test_duplicated_constructor_and_already_patched_input_are_refused(self):
        with self.assertRaises(RegionPatchError):
            patch_default_region(FIXTURE + FIXTURE[FIXTURE.index('.method static'):])
        with self.assertRaises(RegionPatchError):
            patch_default_region(patch_default_region(FIXTURE))

    def test_file_is_untouched_when_validation_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'Z' / 'bc.smali'
            path.parent.mkdir()
            path.write_text(FIXTURE.replace('"kz"', '"us"'))
            before = path.read_bytes()
            with self.assertRaises(RegionPatchError):
                patch_region_defaults(Path(directory))
            self.assertEqual(path.read_bytes(), before)
            path.write_text(FIXTURE)
            report = patch_region_defaults(Path(directory))
            self.assertEqual(report['fallback'], 'by')
            self.assertEqual(path.read_text(), patch_default_region(FIXTURE))


if __name__ == '__main__':
    unittest.main()
