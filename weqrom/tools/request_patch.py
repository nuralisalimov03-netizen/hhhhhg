"""Checked request-common-parameter hook for the pinned TikTok 47.4.3 APK.

The source guide identifies ClientAI X.05W6; its only interface caller is
X.03kn.LIZIZ, which also assembles the legacy path. Patch the final map in that
caller after all merges. Account-model getters, response data, authentication,
cookies, headers, I18n/global region getters and transport security are untouched.
"""
from pathlib import Path
import hashlib
import re

SOURCE_DEX = 'classes2.dex'
SOURCE_CLASS = 'X/03kn.smali'
SOURCE_DEX_SHA256 = '0cab28604d74e3e30015d113fea052f3f3091805cace0e216224d38e1ad905f4'
METHOD = '.method public final LIZIZ(Landroid/content/Context;ZLjava/util/Map;LX/03Jn;)V'
ORIGINAL_METHOD_SHA256 = '1ee02790927991e9fdf63b5688c344edbc7cb27f5b3c4409d77bbb262a7efefb'
OWNER = 'Lapp/weqrom/RequestRegion;'
TAIL = '''    invoke-virtual {v0}, Ljava/lang/Object;->getClass()Ljava/lang/Class;

    return-void
.end method'''
INSERT = '''    invoke-static {p3}, Lapp/weqrom/RequestRegion;->rewrite(Ljava/util/Map;)Ljava/util/HashMap;

    move-result-object v0

    invoke-interface {p3, v0}, Ljava/util/Map;->putAll(Ljava/util/Map;)V

'''
MOD_METHOD = '.method public final c(I)V'
MOD_METHOD_SHA256 = '983b0876b2b7c023aa16938b3cd96f015ee23e03e27010739e99e6c377a0e6f7'
MOD_TAIL = '''    invoke-interface {p1}, Landroid/content/SharedPreferences$Editor;->apply()V

    return-void
.end method'''
MOD_INSERT = '''    iget v0, p0, LZ/bc;->b:I

    invoke-static {v0}, Lapp/weqrom/RequestRegion;->onModRegionSelected(I)V

'''


class RequestPatchError(ValueError):
    pass


def patch_request_text(text: str) -> str:
    """Keep the initial null-map return; rewrite only the final mutable map."""
    if text.count('.class public final LX/03kn;\n') != 1 or OWNER in text:
        raise RequestPatchError('Unexpected class or request hook already present')
    matches = list(re.finditer(r'(?ms)^' + re.escape(METHOD) + r'\n.*?^\.end method$', text))
    if len(matches) != 1:
        raise RequestPatchError('Expected one pinned request-map method')
    match = matches[0]
    original = match.group()
    if hashlib.sha256(original.encode()).hexdigest() != ORIGINAL_METHOD_SHA256:
        raise RequestPatchError('Pinned request-map method checksum differs')
    for anchor in (
        '    .registers 16\n',
        '    if-nez p3, :cond_3\n\n    return-void',
        'invoke-interface {v0}, LX/05W5;->LIZ()Ljava/util/HashMap;',
        'invoke-static {v0, v4, p3}, LX/03kn;->LIZJ(ZLjava/util/Map;Ljava/util/Map;)V',
        TAIL,
    ):
        if original.count(anchor) != 1:
            raise RequestPatchError('Expected request merge/null-guard shape differs')
    if original.count('return-void') != 2:
        raise RequestPatchError('Unexpected request-map return paths')
    changed = original.replace(TAIL, TAIL.replace('    return-void', INSERT + '    return-void'), 1)
    return text[:match.start()] + changed + text[match.end():]


def patch_request_smali(smali_root: str | Path, *, dex_name: str = SOURCE_DEX) -> dict:
    if dex_name != SOURCE_DEX:
        raise RequestPatchError('The pinned common-request builder is in classes2.dex')
    path = Path(smali_root) / SOURCE_CLASS
    if not path.is_file() or path.is_symlink():
        raise RequestPatchError('Missing regular pinned request-map class')
    patched = patch_request_text(path.read_text())
    path.write_text(patched)
    return {
        'source_dex': SOURCE_DEX,
        'source_class': 'LX/03kn;',
        'source_method': METHOD.removeprefix('.method public final '),
        'request_only': True,
        'handles_clientai_and_legacy_paths': True,
        'initial_null_map_return_unchanged': True,
        'rewrite_at_final_normal_return': True,
        'rewritten_keys': ['region', 'carrier_region', 'op_region'],
        'account_model_and_responses_unchanged': True,
        'original_control_flow_retained': True,
        'extra_registers': 0,
        'guide': 'https://github.com/shebing8/tiktok-region-kit',
    }


def patch_mod_region_text(text: str) -> str:
    """Notify after persistence; p1 is an Editor, so reload canonical bc.b."""
    if text.count('.class public final LZ/bc;\n') != 1 or '->onModRegionSelected(I)V' in text:
        raise RequestPatchError('Unexpected preferences class or selection hook already present')
    matches = list(re.finditer(r'(?ms)^' + re.escape(MOD_METHOD) + r'\n.*?^\.end method$', text))
    if len(matches) != 1:
        raise RequestPatchError('Expected one pinned region setter')
    match = matches[0]; original = match.group()
    if hashlib.sha256(original.encode()).hexdigest() != MOD_METHOD_SHA256:
        raise RequestPatchError('Pinned region setter checksum differs')
    for anchor in ('    .registers 6\n', 'iput p1, p0, LZ/bc;->b:I',
                   'iget-object p1, p1, LZ/te;->d:Ljava/lang/String;', MOD_TAIL):
        if original.count(anchor) != 1:
            raise RequestPatchError('Region setter persistence/argument flow differs')
    if original.count('return-void') != 1:
        raise RequestPatchError('Unexpected region setter return paths')
    changed = original.replace(MOD_TAIL, MOD_TAIL.replace('    return-void', MOD_INSERT+'    return-void'), 1)
    return text[:match.start()] + changed + text[match.end():]


def patch_mod_region_selection(smali_root: str | Path) -> dict:
    path = Path(smali_root)/'Z/bc.smali'
    if not path.is_file() or path.is_symlink():
        raise RequestPatchError('Missing regular pinned region preferences class')
    changed = patch_mod_region_text(path.read_text()); path.write_text(changed)
    return {'source_class':'LZ/bc;', 'source_method':'c(I)V',
            'callback':'RequestRegion.onModRegionSelected(I)V',
            'after_real_preferences_apply':True, 'index_reloaded_from_live_field':True,
            'extra_registers':0, 'original_instructions_retained':True}
