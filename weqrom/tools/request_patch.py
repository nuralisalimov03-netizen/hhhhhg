"""Checked request-common-parameter hook for the pinned TikTok 47.4.3 APK.

The source guide identifies ClientAI X.05W6; its only interface caller is
X.03kn.LIZIZ, which also assembles the legacy path. Patch the final map in that
caller after all merges. Account-model getters, response data, authentication,
cookies, headers, I18n/global region getters and transport security are untouched.
The two legacy URL appenders retain preexisting query values; their completed
StringBuilders get a separate hook after the original append operation.
"""
from pathlib import Path
import hashlib
import re

SOURCE_DEX = 'classes2.dex'
SOURCE_CLASS = 'X/03kn.smali'
SOURCE_DEX_SHA256 = '0cab28604d74e3e30015d113fea052f3f3091805cace0e216224d38e1ad905f4'
METHOD = '.method public final LIZIZ(Landroid/content/Context;ZLjava/util/Map;LX/03Jn;)V'
ORIGINAL_METHOD_SHA256 = '1ee02790927991e9fdf63b5688c344edbc7cb27f5b3c4409d77bbb262a7efefb'
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
URL_METHOD = '.method public final LIZ(Landroid/content/Context;Ljava/lang/StringBuilder;ZLX/03Jn;)Ljava/lang/String;'
URL_METHOD_SHA256 = 'a7ce3cf6f1925a75ec1e424108fae7dae39f647cb81eb04f91f29a79e633eb5d'
URL_TAIL = '''    iput v0, p0, LX/03kn;->LIZIZ:I

    invoke-virtual {p2}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object v0

    return-object v0
.end method'''
URL_INSERT = '''    invoke-static {p2}, Lapp/weqrom/RequestRegion;->rewriteUrl(Ljava/lang/StringBuilder;)V

'''
COMMON_URL_CLASS = 'X/03J2.smali'
COMMON_URL_METHOD = '.method public static LIZIZ(Ljava/lang/StringBuilder;ZLX/03Jn;)V'
COMMON_URL_METHOD_SHA256 = '026c770439e0aa08558a486aa8425b49eebfb4d5d6a2dbda62a67d729e2cf0e5'
COMMON_URL_TAIL = '''    invoke-virtual {p0, v0}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    :cond_6e
    return-void
.end method'''
COMMON_URL_INSERT = '''    invoke-static {p0}, Lapp/weqrom/RequestRegion;->rewriteUrl(Ljava/lang/StringBuilder;)V

'''


class RequestPatchError(ValueError):
    pass


def patch_request_text(text: str) -> str:
    """Keep the initial null-map return; rewrite only the final mutable map."""
    if (text.count('.class public final LX/03kn;\n') != 1
            or '->rewrite(Ljava/util/Map;)Ljava/util/HashMap;' in text):
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
        'rewritten_keys': ['region', 'carrier_region', 'op_region', 'sys_region',
                           'carrier_region_v2', 'mcc_mnc'],
        'account_model_and_responses_unchanged': True,
        'original_control_flow_retained': True,
        'extra_registers': 0,
        'guide': 'https://github.com/shebing8/tiktok-region-kit',
    }


def _pinned_url_method(text: str, *, owner: str, method: str, checksum: str,
                       anchors: tuple[str, ...], returns: str, return_count: int) -> tuple[re.Match, str]:
    if (text.count(f'.class public final {owner}\n') != 1
            or '->rewriteUrl(Ljava/lang/StringBuilder;)V' in text):
        raise RequestPatchError('Unexpected URL builder class or URL hook already present')
    matches = list(re.finditer(r'(?ms)^' + re.escape(method) + r'\n.*?^\.end method$', text))
    if len(matches) != 1:
        raise RequestPatchError('Expected one pinned URL builder method')
    match = matches[0]
    original = match.group()
    if hashlib.sha256(original.encode()).hexdigest() != checksum:
        raise RequestPatchError('Pinned URL builder method checksum differs')
    if any(original.count(anchor) != 1 for anchor in anchors):
        raise RequestPatchError('Expected URL builder guards/append shape differs')
    if original.count(returns) != return_count:
        raise RequestPatchError('Unexpected URL builder return paths')
    return match, original


def patch_request_url_text(text: str) -> str:
    """Rewrite final query values after append-only common-param assembly."""
    match, original = _pinned_url_method(
        text, owner='LX/03kn;', method=URL_METHOD, checksum=URL_METHOD_SHA256,
        anchors=(
            '    .registers 13\n',
            '    if-eqz v0, :cond_b\n\n    return-object v1',
            'invoke-virtual {p0, p1, p3, v4, p4}, LX/03kn;->LIZIZ(Landroid/content/Context;ZLjava/util/Map;LX/03Jn;)V',
            'invoke-interface {v7, v2}, Ljava/util/Set;->contains(Ljava/lang/Object;)Z',
            'invoke-virtual {p2, v0}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;',
            URL_TAIL,
        ), returns='return-object', return_count=2,
    )
    changed = original.replace(URL_TAIL, URL_TAIL.replace(
        '    invoke-virtual {p2}, Ljava/lang/StringBuilder;->toString()',
        URL_INSERT + '    invoke-virtual {p2}, Ljava/lang/StringBuilder;->toString()', 1), 1)
    return text[:match.start()] + changed + text[match.end():]


def patch_common_url_text(text: str) -> str:
    """Keep null/disabled/empty early exits; hook only a completed URL."""
    match, original = _pinned_url_method(
        text, owner='LX/03J2;', method=COMMON_URL_METHOD, checksum=COMMON_URL_METHOD_SHA256,
        anchors=(
            '    .registers 10\n',
            '    if-eqz v0, :cond_6e\n\n    if-eqz p0, :cond_6e',
            '    if-eqz v0, :cond_11\n\n    return-void',
            'invoke-static {v0, p1, p2}, LX/03J2;->LIZLLL(Ljava/util/Map;ZLX/03Jn;)V',
            'invoke-interface {v6, v2}, Ljava/util/Set;->contains(Ljava/lang/Object;)Z',
            COMMON_URL_TAIL,
        ), returns='return-void', return_count=2,
    )
    changed = original.replace(COMMON_URL_TAIL, COMMON_URL_TAIL.replace(
        '    :cond_6e\n', COMMON_URL_INSERT + '    :cond_6e\n', 1), 1)
    return text[:match.start()] + changed + text[match.end():]


def patch_request_urls_smali(smali_root: str | Path, *, dex_name: str = SOURCE_DEX) -> dict:
    """Validate both pinned methods before modifying either source file."""
    if dex_name != SOURCE_DEX:
        raise RequestPatchError('The pinned URL builders are in classes2.dex')
    root = Path(smali_root)
    changes = []
    for relative, patch in ((SOURCE_CLASS, patch_request_url_text),
                            (COMMON_URL_CLASS, patch_common_url_text)):
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise RequestPatchError('Missing regular pinned request URL class')
        changes.append((path, patch(path.read_text())))
    for path, patched in changes:
        path.write_text(patched)
    return {
        'source_dex': SOURCE_DEX,
        'source_methods': [
            {'class': 'LX/03kn;', 'method': URL_METHOD.removeprefix('.method public final '),
             'original_method_sha256': URL_METHOD_SHA256, 'builder_argument': 'p2'},
            {'class': 'LX/03J2;', 'method': COMMON_URL_METHOD.removeprefix('.method public static '),
             'original_method_sha256': COMMON_URL_METHOD_SHA256, 'builder_argument': 'p0'},
        ],
        'callback': 'RequestRegion.rewriteUrl(Ljava/lang/StringBuilder;)V',
        'request_only': True,
        'after_original_url_append': True,
        'initial_guards_and_early_returns_unchanged': True,
        'original_instructions_retained': True,
        'extra_registers': 0,
        'account_model_responses_and_transport_security_unchanged': True,
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
