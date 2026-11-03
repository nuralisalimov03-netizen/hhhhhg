"""Diagnostic hooks retain the original runtime success/failure decisions."""
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from weqrom.tools.diagnostic_patch import (
    BOOTSTRAP_FILE, MAIN_FILE, BOOTSTRAP_METHOD, MAIN_METHOD,
    START_CALL, START_RECORD, FAIL_ANCHOR, FAIL_RECORD, HOOK_ANCHOR, HOOK_RECORD,
    OWNER, DiagnosticPatchError, patch_bootstrap_text, patch_main_text,
    patch_diagnostics_smali,
)


def bootstrap_fixture():
    return (".class public final Lcom/windukk/stable/StableBootstrap;\n.super Ljava/lang/Object;\n"
            + BOOTSTRAP_METHOD + "\n    .registers 12\n" + START_CALL + "\n"
            "    .catchall {:try_start_24 .. :try_end_9e} :catchall_a0\n"
            "    invoke-virtual {p1}, Ljava/lang/reflect/InvocationTargetException;->getCause()Ljava/lang/Throwable;\n"
            "    invoke-virtual {p1}, Ljava/lang/reflect/InvocationTargetException;->getCause()Ljava/lang/Throwable;\n"
            + FAIL_ANCHOR + "    invoke-virtual {v1, v2, p0}, La/g;->a(La/e;Ljava/lang/Throwable;)V\n"
            "    return-void\n.end method\n")


def main_fixture():
    return (".class public final Lcom/windukk/mod/Main;\n.super Ljava/lang/Object;\n"
            + MAIN_METHOD + "\n    .registers 12\n"
            "    sput-object p1, Lcom/windukk/mod/Main;->d:Landroid/content/Context;\n"
            "    invoke-static {p1}, Lcom/ss/android/ugc/aweme/utils/CommonUtils;->verifySignatureNative(Landroid/content/Context;)Z\n"
            "    .catch Ljava/lang/Exception; {:try_start_ae .. :try_end_1ae} :catch_1af\n"
            + HOOK_ANCHOR + "\n"
            "    invoke-static {v0, v2, p1}, LZ/g7;->a(ILjava/lang/String;Ljava/lang/Throwable;)V\n"
            "    return-void\n.end method\n")


class DiagnosticPatchTests(unittest.TestCase):
    def test_bootstrap_calls_preserve_all_existing_code_and_record_unwrapped_cause(self):
        original = bootstrap_fixture()
        changed = patch_bootstrap_text(original)
        self.assertEqual(changed.replace(START_RECORD, "").replace(FAIL_RECORD, ""), original)
        self.assertEqual(changed.count(OWNER), 2)
        self.assertLess(changed.index(START_CALL), changed.index("->started("))
        self.assertLess(changed.rindex("->getCause()"), changed.index("->failed("))
        self.assertLess(changed.index("->failed("), changed.index("La/g;->a(La/e;Ljava/lang/Throwable;)V"))
        self.assertIn("iget-object v3, v1, La/g;->a:Landroid/content/Context;", changed)

    def test_main_reports_existing_hook_error_without_removing_native_check_or_log(self):
        original = main_fixture()
        changed = patch_main_text(original)
        self.assertEqual(changed.replace(HOOK_RECORD, ""), original)
        self.assertIn("CommonUtils;->verifySignatureNative", changed)
        self.assertIn("LZ/g7;->a(ILjava/lang/String;Ljava/lang/Throwable;)V", changed)
        self.assertEqual(changed.count("->hookError("), 1)
        self.assertLess(changed.index("move-exception p1"), changed.index("->hookError("))

    def test_unknown_registers_methods_flows_or_repeat_patch_are_rejected(self):
        for transform, original in ((patch_bootstrap_text, bootstrap_fixture()),
                                    (patch_main_text, main_fixture())):
            for bad in (original.replace(".registers 12", ".registers 13"),
                        original.replace("start(Landroid/app/Application;", "renamed(Landroid/app/Application;"),
                        original.replace(".class public final", ".class public abstract"),
                        transform(original)):
                with self.subTest(transform=transform.__name__, bad=bad[:80]):
                    with self.assertRaises(DiagnosticPatchError):
                        transform(bad)
        for anchor in (START_CALL, FAIL_ANCHOR, "    .catchall {:try_start_24 .. :try_end_9e} :catchall_a0"):
            with self.assertRaises(DiagnosticPatchError):
                patch_bootstrap_text(bootstrap_fixture().replace(anchor, "", 1))
        with self.assertRaises(DiagnosticPatchError):
            patch_main_text(main_fixture().replace(HOOK_ANCHOR, "", 1))

    def test_both_files_are_validated_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boot, mod = root / "boot", root / "mod"
            first, second = boot / BOOTSTRAP_FILE, mod / MAIN_FILE
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_text(bootstrap_fixture())
            second.write_text("user data")
            with self.assertRaises(DiagnosticPatchError):
                patch_diagnostics_smali(boot, mod)
            self.assertEqual(first.read_text(), bootstrap_fixture())
            self.assertEqual(second.read_text(), "user data")
            second.write_text(main_fixture())
            report = patch_diagnostics_smali(boot, mod)
            self.assertEqual(report["extra_registers"], 0)
            self.assertTrue(report["original_catches_logging_and_rollback_retained"])

    def test_actual_pinned_classes_assemble_without_changing_original_instructions(self):
        jar = Path("/workspace/.chits-tools/jadx/lib/jadx-1.5.3-all.jar")
        originals = ((Path("/tmp/weqrom-loader-review/smali52") / BOOTSTRAP_FILE, patch_bootstrap_text),
                     (Path("/tmp/weqrom-region-research/smali") / MAIN_FILE, patch_main_text))
        if not jar.is_file() or any(not path.is_file() for path, _ in originals):
            self.skipTest("Pinned source disassembly/tool cache unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            smali = root / "smali"
            smali.mkdir()
            for index, (path, transform) in enumerate(originals):
                original = path.read_text()
                changed = transform(original)
                self.assertEqual(changed.replace(START_RECORD, "").replace(FAIL_RECORD, "").replace(HOOK_RECORD, ""), original)
                (smali / (str(index) + ".smali")).write_text(changed)
            output = root / "patched.dex"
            subprocess.run(["java", "-cp", str(jar), "com.android.tools.smali.smali.Main",
                            "a", str(smali), "--api", "26", "-o", str(output)],
                           check=True, capture_output=True)
            data = output.read_bytes()
            self.assertTrue(data.startswith(b"dex\n"))
            self.assertEqual(struct.unpack_from("<I", data, 96)[0], 2)
            self.assertIn(OWNER.encode(), data)


if __name__ == "__main__":
    unittest.main()
