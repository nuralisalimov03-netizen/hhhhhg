"""Change the checked mod's fallback country, without changing its region table.

The runtime bridge performs a versioned migration through the mod's real setter.
This small smali change also covers invalid/missing stored countries before that
bridge runs. It deliberately refuses an unfamiliar class or method shape.
"""
from pathlib import Path
import re


class RegionPatchError(ValueError):
    pass


def patch_default_region(text: str) -> str:
    if not re.search(r"^\.class public final LZ/bc;\s*$", text, re.MULTILINE):
        raise RegionPatchError("Expected checked Z.bc preferences class")
    methods = list(re.finditer(
        r"^\.method static constructor <clinit>\(\)V\n.*?^\.end method\s*$",
        text, re.MULTILINE | re.DOTALL,
    ))
    if len(methods) != 1:
        raise RegionPatchError("Expected exactly one Z.bc static constructor")
    match = methods[0]
    body = match.group()
    for anchor in (
        "sget-object v2, LZ/ue;->a:[LZ/te;",
        "iget-object v2, v2, LZ/te;->d:Ljava/lang/String;",
        "invoke-virtual {v2, v3}, Ljava/lang/String;->equalsIgnoreCase(Ljava/lang/String;)Z",
        "sput v0, LZ/bc;->I:I",
    ):
        if body.count(anchor) != 1:
            raise RegionPatchError("Region fallback loop changed: " + anchor)
    old = 'const-string v3, "kz"'
    if body.count(old) != 1 or 'const-string v3, "by"' in body:
        raise RegionPatchError("Expected original single Kazakhstan fallback literal")
    changed = body.replace(old, 'const-string v3, "by"', 1)
    return text[:match.start()] + changed + text[match.end():]


def patch_region_defaults(smali_root: Path) -> dict:
    path = Path(smali_root) / "Z" / "bc.smali"
    original = path.read_text(encoding="utf-8")
    patched = patch_default_region(original)
    path.write_text(patched, encoding="utf-8")
    return {"class": "LZ/bc;", "method": "<clinit>()V", "fallback": "by"}


def patch_region_smali(smali_root: Path) -> dict:
    """Build-pipeline entry point; input must be a fresh checked disassembly."""
    return patch_region_defaults(smali_root)
