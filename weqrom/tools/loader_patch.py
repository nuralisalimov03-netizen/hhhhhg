"""Fork the checked dynamic-loader compatibility identity for Weqrom.

The original loader selects healthy compatible OTA slots before bundled assets.
An APK versionCode or dynamicVersion change alone cannot prevent an old TikTok
You OTA from masking the new Weqrom module, including on the second launch.

All three original ABI consumers are changed together with the bundled manifest
(which the build caller owns). Their comparison branches, checksum/signature
validation, public key, and crash fallback logic remain bytecode-identical.
No forced cache reset or custom filesystem migration is added.
"""
from __future__ import annotations

from pathlib import Path
import re

ORIGINAL_STABLE_API = 1792428213
WEQROM_STABLE_API = 2026110302
SURFACES = {
    "classes46.dex": (
        "a/g.smali", ".class public final La/g;",
        ".method public constructor <init>(Landroid/content/Context;)V",
        "p1", ("iput p1, p0, La/g;->d:I",),
    ),
    "classes50.dex": (
        "c/f.smali", ".class public abstract Lc/f;",
        ".method public static a(Lc/b;Ljava/lang/String;)V",
        "v0", ("iget p1, p0, Lc/b;->c:I", "if-ne v0, p1, :cond_12", "throw p1"),
    ),
    "classes52.dex": (
        "com/windukk/stable/DynamicUpdates.smali",
        ".class public final Lcom/windukk/stable/DynamicUpdates;",
        ".method public static stableApiVersion()I",
        "v0", ("return v0",),
    ),
}


class LoaderPatchError(ValueError):
    """An ABI surface does not exactly match the checked input."""


def _abi(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 2147483647:
        raise LoaderPatchError("Stable API must be a positive signed 32-bit integer")
    if value == ORIGINAL_STABLE_API:
        raise LoaderPatchError("Weqrom must have a distinct loader compatibility identity")
    return value


def patch_loader_text(text: str, dex_name: str, stable_api_version: int = WEQROM_STABLE_API) -> str:
    """Change one checked ABI constant, preserving every comparison instruction."""
    stable_api_version = _abi(stable_api_version)
    if dex_name not in SURFACES:
        raise LoaderPatchError("Unknown loader DEX: " + dex_name)
    _path, declaration, signature, register, anchors = SURFACES[dex_name]
    if len(re.findall(r"(?m)^" + re.escape(declaration) + r"$", text)) != 1:
        raise LoaderPatchError("Wrong pinned loader class: " + dex_name)
    pattern = re.compile(r"(?m)^" + re.escape(signature) + r"\n.*?^\.end method(?=\s*$)", re.DOTALL)
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise LoaderPatchError("Expected exactly one pinned ABI method: " + signature)
    method = matches[0].group()
    old = "const " + register + ", " + hex(ORIGINAL_STABLE_API)
    if method.count(old) != 1 or text.count(hex(ORIGINAL_STABLE_API)) != 1:
        raise LoaderPatchError("Original ABI literal is missing, duplicated, or outside its method")
    if any(method.count(anchor) != 1 for anchor in anchors):
        raise LoaderPatchError("Pinned ABI comparison changed: " + signature)
    modified = method.replace(old, "const " + register + ", " + hex(stable_api_version), 1)
    return text[:matches[0].start()] + modified + text[matches[0].end():]


def patch_loader_smali(
    smali_root: str | Path,
    stable_api_version: int = WEQROM_STABLE_API,
    *,
    dex_name: str | None = None,
) -> dict[str, object]:
    """Patch a combined root, or one explicitly named separately-built DEX.

    The build must call this for all three original top-level DEX files and set
    bundled manifest.stableApiVersion to the same identity. A combined root is
    validated completely before any file is changed.
    """
    stable_api_version = _abi(stable_api_version)
    if dex_name is not None and dex_name not in SURFACES:
        raise LoaderPatchError("Unknown loader DEX: " + dex_name)
    names = (dex_name,) if dex_name is not None else tuple(SURFACES)
    root = Path(smali_root)
    prepared: dict[Path, str] = {}
    for name in names:
        relative = SURFACES[name][0]
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise LoaderPatchError("Missing regular pinned loader class: " + relative)
        prepared[path] = patch_loader_text(path.read_text(encoding="utf-8"), name, stable_api_version)
    for path, value in prepared.items():
        path.write_text(value, encoding="utf-8")
    return {
        "original_stable_api": ORIGINAL_STABLE_API,
        "weqrom_stable_api": stable_api_version,
        "modified_dex": list(names),
        "modified_smali": [SURFACES[name][0] for name in names],
        "checks_preserved": ["stable API comparison", "variant comparison", "SHA-256", "remote signature", "public key", "crash fallback"],
        "old_tiktok_you_ota_compatible": False,
        "cache_files_manually_modified": False,
    }
