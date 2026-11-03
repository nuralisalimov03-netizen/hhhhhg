"""Checked UI entry hooks for the pinned TikTok You 47.4.3 mod DEX.

Only the old welcome renderer and mod-settings click target are redirected.
Authentication, request/region logic, signature checks and updater verification
are deliberately outside this module. The caller must compile ModIntegration
and WeqromUi into the same dynamic DEX before assembling the result.
"""
from __future__ import annotations

import json
from pathlib import Path
import re


class WelcomePatchError(ValueError):
    """The expected pinned UI surface was not found exactly once."""


def _replace_method(text: str, signature: str, body: str, markers: tuple[str, ...]) -> str:
    pattern = re.compile(r"(?m)^" + re.escape(signature) + r"\n.*?^\.end method\s*$", re.DOTALL)
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise WelcomePatchError(f"Expected one {signature}, found {len(matches)}")
    original = matches[0].group()
    if any(marker not in original for marker in markers):
        raise WelcomePatchError(f"Pinned method body mismatch: {signature}")
    replacement = signature + "\n" + body.rstrip() + "\n.end method"
    return text[:matches[0].start()] + replacement + text[matches[0].end():]


def patch_welcome_text(text: str) -> str:
    """Redirect the old visible sheet; preserve its caller/retry scheduling."""
    return _replace_method(
        text,
        ".method public final a(Landroid/app/Activity;)V",
        """    .registers 2

    invoke-static {p1}, Lapp/weqrom/ModIntegration;->showWelcome(Landroid/app/Activity;)V

    return-void""",
        ("LZ/bc;->H:Z", "Ljava/util/concurrent/atomic/AtomicBoolean;->compareAndSet(ZZ)Z", "LZ/fc;"),
    )


def patch_settings_text(text: str) -> str:
    """Keep TikTok's injected settings row, redirect its click to Weqrom."""
    return _replace_method(
        text,
        ".method public static c([Ljava/lang/Object;)V",
        """    .registers 1

    invoke-static {p0}, Lapp/weqrom/ModIntegration;->showSettingsFromArgs([Ljava/lang/Object;)V

    return-void""",
        ("LZ/sf;", "Landroid/content/Context;->startActivity(Landroid/content/Intent;)V"),
    )


def patch_settings_labels(text: str) -> str:
    """Rename the two mod-menu labels while retaining credits elsewhere."""
    for original, replacement in (("Настройки мода", "Настройки Weqrom"), ("Mod Settings", "Weqrom Settings")):
        # Baksmali emits non-ASCII using Java \u escapes by default.
        variants = {json.dumps(original, ensure_ascii=True), json.dumps(original, ensure_ascii=False)}
        found = [(value, text.count(value)) for value in variants if value in text]
        if len(found) != 1 or found[0][1] != 1:
            raise WelcomePatchError(f"Expected exactly one mod-settings label: {original}")
        text = text.replace(found[0][0], json.dumps(replacement, ensure_ascii=True), 1)
    return text


def patch_welcome_smali(smali_root: str | Path) -> dict[str, object]:
    """Validate all touched files before changing any, return an audit record."""
    root = Path(smali_root)
    transforms = {
        "Z/dg.smali": patch_welcome_text,
        "Z/mi.smali": patch_settings_text,
        "Z/l7.smali": patch_settings_labels,
    }
    prepared: dict[Path, str] = {}
    for relative, transform in transforms.items():
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise WelcomePatchError(f"Missing regular pinned smali file: {relative}")
        prepared[path] = transform(path.read_text(encoding="utf-8"))
    for path, value in prepared.items():
        path.write_text(value, encoding="utf-8")
    return {
        "welcome_entry": "LZ/dg;->a(Landroid/app/Activity;)V",
        "settings_entry": "LZ/mi;->c([Ljava/lang/Object;)V",
        "settings_path": "Profile → ☰ → Settings and privacy → Weqrom Settings",
        "modified_smali": list(transforms),
        "ui_attribution_elsewhere_retained": True,
    }
