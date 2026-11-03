"""Record outcomes of the pinned loader without changing its decision paths.

BootstrapStatus lives in an ordinary top-level application DEX, so it remains
available when loading or initializing the optional dynamic module fails. Its
public static recording methods must be nonthrowing and must not execute hooks.
No failure is converted into success, and the original logging, verification,
exception handlers and rollback paths are retained.
"""
from __future__ import annotations

from pathlib import Path
import re

OWNER = "Lapp/weqrom/BootstrapStatus;"
BOOTSTRAP_FILE = "com/windukk/stable/StableBootstrap.smali"
MAIN_FILE = "com/windukk/mod/Main.smali"
BOOTSTRAP_METHOD = ".method public static declared-synchronized start(Landroid/app/Application;Landroid/content/Context;)V"
MAIN_METHOD = ".method public static final start(Landroid/app/Application;Landroid/content/Context;)V"
START_CALL = "    invoke-static {p0, p1, v4}, Lcom/windukk/stable/StableBootstrap;->a(Landroid/app/Application;Landroid/content/Context;Ljava/lang/ClassLoader;)V"
START_RECORD = "\n\n    invoke-static {p1}, " + OWNER + "->started(Landroid/content/Context;)V"
FAIL_ANCHOR = "    :cond_b5\n"
FAIL_RECORD = ("    iget-object v3, v1, La/g;->a:Landroid/content/Context;\n\n"
               "    invoke-static {v3, p0}, " + OWNER + "->failed(Landroid/content/Context;Ljava/lang/Throwable;)V\n\n")
HOOK_ANCHOR = "    :catch_1af\n    move-exception p1"
HOOK_RECORD = ("\n\n    invoke-static {}, Lcom/windukk/mod/Main;->a()Landroid/content/Context;\n\n"
               "    move-result-object v3\n\n"
               "    invoke-static {v3, p1}, " + OWNER + "->hookError(Landroid/content/Context;Ljava/lang/Throwable;)V")


class DiagnosticPatchError(ValueError):
    """The input does not match the reviewed runtime diagnostic surfaces."""


def _method(text: str, declaration: str, signature: str):
    if text.count(declaration + "\n") != 1 or OWNER in text:
        raise DiagnosticPatchError("Unexpected class or diagnostics already installed")
    matches = list(re.finditer(r"(?m)^" + re.escape(signature) + r"\n.*?^\.end method$", text, re.DOTALL))
    if len(matches) != 1:
        raise DiagnosticPatchError("Expected one pinned startup method")
    match = matches[0]
    body = match.group()
    if body.count("    .registers 12\n") != 1:
        raise DiagnosticPatchError("Pinned startup register layout changed")
    return match, body


def patch_bootstrap_text(text: str) -> str:
    """Report after Main.start returns and after unwrapping its real exception.

    p1 is still the supplied Context at the success call. In the existing catch
    handler p1 has been reused; retrieve Context from the surviving a.g loader
    in v1 instead. v3 is scratch at that point, and all original registers remain
    unchanged for the existing Log.e and rollback calls.
    """
    match, body = _method(text, ".class public final Lcom/windukk/stable/StableBootstrap;", BOOTSTRAP_METHOD)
    for anchor in (START_CALL, FAIL_ANCHOR,
                   "    .catchall {:try_start_24 .. :try_end_9e} :catchall_a0",
                   "    invoke-virtual {p1}, Ljava/lang/reflect/InvocationTargetException;->getCause()Ljava/lang/Throwable;",
                   "    invoke-virtual {v1, v2, p0}, La/g;->a(La/e;Ljava/lang/Throwable;)V"):
        # getCause appears twice: the first checks null and the second unwraps.
        expected = 2 if "InvocationTargetException;->getCause" in anchor else 1
        if body.count(anchor) != expected:
            raise DiagnosticPatchError("Pinned bootstrap success/failure flow changed")
    changed = body.replace(START_CALL, START_CALL + START_RECORD, 1)
    changed = changed.replace(FAIL_ANCHOR, FAIL_ANCHOR + FAIL_RECORD, 1)
    return text[:match.start()] + changed + text[match.end():]


def patch_main_text(text: str) -> str:
    """Expose an exception already caught and logged by Main's hook block."""
    match, body = _method(text, ".class public final Lcom/windukk/mod/Main;", MAIN_METHOD)
    for anchor in (HOOK_ANCHOR,
                   "    .catch Ljava/lang/Exception; {:try_start_ae .. :try_end_1ae} :catch_1af",
                   "    sput-object p1, Lcom/windukk/mod/Main;->d:Landroid/content/Context;",
                   "    invoke-static {v0, v2, p1}, LZ/g7;->a(ILjava/lang/String;Ljava/lang/Throwable;)V"):
        if body.count(anchor) != 1:
            raise DiagnosticPatchError("Pinned Main hook-error flow changed")
    changed = body.replace(HOOK_ANCHOR, HOOK_ANCHOR + HOOK_RECORD, 1)
    return text[:match.start()] + changed + text[match.end():]


def patch_diagnostics_smali(bootstrap_root: str | Path, mod_root: str | Path) -> dict:
    """Validate both files before any write; callers assemble their own DEXes."""
    plans = ((Path(bootstrap_root) / BOOTSTRAP_FILE, patch_bootstrap_text),
             (Path(mod_root) / MAIN_FILE, patch_main_text))
    prepared = {}
    for path, transform in plans:
        if path.is_symlink() or not path.is_file():
            raise DiagnosticPatchError("Missing regular diagnostic target: " + str(path))
        prepared[path] = transform(path.read_text(encoding="utf-8"))
    for path, changed in prepared.items():
        path.write_text(changed, encoding="utf-8")
    return {
        "bootstrap_success": "Main.start returned",
        "bootstrap_failure": "unwrapped startup exception",
        "hook_error": "original Main hook-block exception",
        "recorder": "app.weqrom.BootstrapStatus in top-level DEX",
        "original_catches_logging_and_rollback_retained": True,
        "extra_registers": 0,
    }
