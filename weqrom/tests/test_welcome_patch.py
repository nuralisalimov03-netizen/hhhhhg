"""UI hook wiring fails closed and leaves unrelated mod logic untouched."""
from pathlib import Path
import shutil
import tempfile
import unittest

from weqrom.tools.welcome_patch import (
    WelcomePatchError,
    patch_settings_labels,
    patch_settings_text,
    patch_welcome_smali,
    patch_welcome_text,
)

WELCOME = '''.class public LZ/dg;
.method public static a()V
    invoke-static {}, Loriginal/Update;->verifiedCheck()V
    return-void
.end method
.method public final a(Landroid/app/Activity;)V
    .registers 22
    iget-boolean v3, v3, LZ/bc;->H:Z
    invoke-virtual {v3, v8, v4}, Ljava/util/concurrent/atomic/AtomicBoolean;->compareAndSet(ZZ)Z
    new-instance v0, LZ/fc;
    return-void
.end method
.method public final unused()V
    invoke-static {}, Loriginal/Signature;->verify()V
    return-void
.end method
'''
SETTINGS = '''.class public LZ/mi;
.method public static c([Ljava/lang/Object;)V
    .registers 5
    const-class v1, LZ/sf;
    invoke-virtual {p0, v0}, Landroid/content/Context;->startActivity(Landroid/content/Intent;)V
    return-void
.end method
.method public static untouched()V
    return-void
.end method
'''
LABELS = '''.class public LZ/l7;
const-string v0, "Настройки мода"
const-string v1, "Mod Settings"
const-string v2, "TikTok You development"
'''


class WelcomePatchTests(unittest.TestCase):
    def test_redirects_exact_entries_and_preserves_original_adjacent_methods(self):
        result = patch_welcome_text(WELCOME)
        self.assertIn('invoke-static {p1}, Lapp/weqrom/ModIntegration;->showWelcome(Landroid/app/Activity;)V', result)
        self.assertNotIn('LZ/fc;', result)
        for original in ('Loriginal/Update;->verifiedCheck()V', 'Loriginal/Signature;->verify()V'):
            self.assertIn(original, result)
        result = patch_settings_text(SETTINGS)
        self.assertIn('invoke-static {p0}, Lapp/weqrom/ModIntegration;->showSettingsFromArgs([Ljava/lang/Object;)V', result)
        self.assertIn('.method public static untouched()V\n    return-void\n.end method', result)

    def test_unknown_missing_and_duplicate_method_bodies_are_rejected(self):
        with self.assertRaises(WelcomePatchError):
            patch_welcome_text(WELCOME.replace('LZ/bc;->H:Z', 'LZ/bc;->changed:Z'))
        with self.assertRaises(WelcomePatchError):
            patch_settings_text(SETTINGS.replace('LZ/sf;', 'Lunknown/Target;'))
        with self.assertRaises(WelcomePatchError):
            patch_welcome_text(WELCOME + WELCOME)
        with self.assertRaises(WelcomePatchError):
            patch_welcome_text('')

    def test_label_patch_accepts_utf8_or_baksmali_unicode_and_keeps_attribution(self):
        for fixture in (LABELS, LABELS.replace('Настройки мода', r'\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 \u043c\u043e\u0434\u0430')):
            result = patch_settings_labels(fixture)
            self.assertIn('Weqrom Settings', result)
            self.assertIn('TikTok You development', result)
            self.assertNotIn('"Mod Settings"', result)
        with self.assertRaises(WelcomePatchError):
            patch_settings_labels(LABELS + '\nconst-string v4, "Mod Settings"')

    def test_directory_changes_are_validated_before_any_file_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'Z').mkdir()
            for name, content in [('dg.smali', WELCOME), ('mi.smali', SETTINGS), ('l7.smali', 'missing labels')]:
                (root/'Z'/name).write_text(content)
            with self.assertRaises(WelcomePatchError):
                patch_welcome_smali(root)
            self.assertEqual((root/'Z/dg.smali').read_text(), WELCOME)
            self.assertEqual((root/'Z/mi.smali').read_text(), SETTINGS)
            (root/'Z/l7.smali').write_text(LABELS)
            report = patch_welcome_smali(root)
            self.assertEqual(len(report['modified_smali']), 3)
            self.assertIn('Weqrom Settings', (root/'Z/l7.smali').read_text())

    def test_real_pinned_smali_hooks_if_cached(self):
        cached = Path('/workspace/.chits-tools/tiktok/mod-research/smali')
        if not (cached/'Z/dg.smali').is_file():
            self.skipTest('Pinned third-party DEX inspection cache unavailable')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'Z').mkdir()
            for name in ('dg.smali', 'mi.smali', 'l7.smali'):
                shutil.copyfile(cached/'Z'/name, root/'Z'/name)
            report = patch_welcome_smali(root)
            self.assertEqual(report['welcome_entry'], 'LZ/dg;->a(Landroid/app/Activity;)V')
            self.assertIn('Weqrom Settings', (root/'Z/l7.smali').read_text())


if __name__ == '__main__':
    unittest.main()
