"""Compile the small Weqrom UI and apply checked hooks to the bundled mod DEX.

The three small stable-loader DEX files receive a consistent Weqrom module
identity. The checked classes2 request builder receives a client-region hook.
Other top-level DEX and native libraries are retained.
Authentication, integrity comparisons, remote signatures and their keys are not
changed. The bundled manifest hashes the newly built payload.
"""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED

try:
    from .setup_mod_tools import ensure_jar
    from .region_patch import patch_region_smali
    from .welcome_patch import patch_welcome_smali
    from .loader_patch import patch_loader_smali, WEQROM_STABLE_API
    from .diagnostic_patch import patch_main_text, patch_bootstrap_text
    from .request_patch import patch_request_smali, patch_mod_region_selection, patch_request_urls_smali, SOURCE_DEX_SHA256
except ImportError:
    from setup_mod_tools import ensure_jar
    from region_patch import patch_region_smali
    from welcome_patch import patch_welcome_smali
    from loader_patch import patch_loader_smali, WEQROM_STABLE_API
    from diagnostic_patch import patch_main_text, patch_bootstrap_text
    from request_patch import patch_request_smali, patch_mod_region_selection, patch_request_urls_smali, SOURCE_DEX_SHA256

PROJECT = Path(__file__).resolve().parents[1]
MOD_ENTRY = 'assets/tty/bundled/classes.dex'
MANIFEST_ENTRY = 'assets/tty/bundled/manifest.json'
MOD_SHA = 'bde52db9f754198d414fe5520840f8880bae586e303eebeb6edf03790d9f51a2'
START_HOOK = '    invoke-static {p1}, LZ/ac;->a(Landroid/content/Context;)V'
INITIALIZE = '    invoke-static {p1}, Lapp/weqrom/ModIntegration;->initialize(Landroid/content/Context;)V'
REGION_CONTEXT = '    sput-object v1, La/j;->a:Landroid/content/Context;'
REGION_INITIALIZE = '    invoke-static {v1}, Lapp/weqrom/RequestRegion;->initialize(Landroid/content/Context;)V'


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def patch_startup(text):
    if text.count(START_HOOK) != 1 or INITIALIZE in text:
        raise ValueError('Pinned mod initialization hook mismatch')
    return text.replace(START_HOOK, START_HOOK + '\n\n' + INITIALIZE, 1)


def patch_region_context(text):
    """Use the loader's canonical Context before optional/native mod startup."""
    if text.count(REGION_CONTEXT) != 1 or REGION_INITIALIZE in text:
        raise ValueError('Pinned early region context changed')
    return text.replace(REGION_CONTEXT, REGION_CONTEXT + '\n\n' + REGION_INITIALIZE, 1)


def refresh_manifest(raw, payload):
    manifest = json.loads(raw)
    if (manifest.get('targetDexSha256') != MOD_SHA or len(manifest.get('dexFiles', [])) != 1
            or manifest['dexFiles'][0].get('name') != 'classes.dex'):
        raise ValueError('Unexpected bundled DEX manifest')
    digest = hashlib.sha256(payload).hexdigest()
    manifest['targetDexSha256'] = digest
    manifest['targetSize'] = len(payload)
    manifest['dexFiles'][0]['targetSha256'] = digest
    manifest['dexFiles'][0]['targetSize'] = len(payload)
    manifest['hashes']['classes.dex'] = digest
    # A separate module compatibility identity prevents an upstream cached OTA
    # from taking precedence over the Weqrom fork. Comparisons/signatures stay
    # enabled and are updated consistently in all three stable loader surfaces.
    manifest['stableApiVersion'] = WEQROM_STABLE_API
    manifest['dynamicVersion'] = WEQROM_STABLE_API
    return json.dumps(manifest, separators=(',', ':')).encode()


def build_mod(source, tools, output):
    output = Path(output)
    # Only this tool's generated directory is refreshed; input and signing data
    # stay outside it. Sources and user-owned folders are never cleaned.
    output.mkdir(parents=True, exist_ok=True)
    jar = ensure_jar(tools)
    with ZipFile(source) as apk:
        original = apk.read(MOD_ENTRY)
        manifest = apk.read(MANIFEST_ENTRY)
    if hashlib.sha256(original).hexdigest() != MOD_SHA:
        raise ValueError('Unexpected bundled mod DEX checksum')
    original_path = output / 'original.dex'
    original_path.write_bytes(original)
    smali = output / 'smali'
    if smali.exists():
        shutil.rmtree(smali)
    run('java', '-cp', jar, 'com.android.tools.smali.baksmali.Main', 'd', original_path, '-o', smali)
    region = patch_region_smali(smali)
    region_selection = patch_mod_region_selection(smali)
    welcome = patch_welcome_smali(smali)
    main = smali / 'com/windukk/mod/Main.smali'
    main.write_text(patch_startup(patch_main_text(main.read_text())))

    classes = output / 'classes'
    if classes.exists():
        shutil.rmtree(classes)
    classes.mkdir()
    roots = [PROJECT / 'app/src/main/java', PROJECT / 'integration/src', PROJECT / 'integration/stubs']
    sources = sorted(str(p) for root in roots for p in root.rglob('*.java'))
    if not sources:
        raise ValueError('Weqrom Java sources missing')
    source_list = output / 'sources.txt'
    source_list.write_text('\n'.join(sources))
    run('java', '-jar', tools / 'ecj.jar', '-1.8', '-proc:none', '-warn:none',
        '-classpath', tools / 'platform/android-35/android.jar', '-d', classes, '@' + str(source_list))
    compiled = output / 'weqrom-ui.jar'
    with ZipFile(compiled, 'w', ZIP_DEFLATED) as archive:
        packaged = list((classes / 'app/weqrom').glob('ModIntegration*.class'))
        if not packaged:
            raise ValueError('Compiled Weqrom package missing')
        for path in sorted(packaged):
            archive.write(path, path.relative_to(classes).as_posix())
    parent_jar = output / 'weqrom-parent.jar'
    with ZipFile(parent_jar, 'w', ZIP_DEFLATED) as archive:
        for path in sorted((classes / 'app/weqrom').rglob('*.class')):
            if not path.name.startswith('ModIntegration'):
                archive.write(path, path.relative_to(classes).as_posix())
    parent_dex = output / 'parent-dex'
    parent_dex.mkdir(exist_ok=True)
    run(tools / 'build-tools/android-15/d8', '--min-api', '26', '--lib',
        tools / 'platform/android-35/android.jar', '--output', parent_dex, parent_jar)
    # Compile-only original-client API stubs under Z/ and other namespaces are
    # excluded above. The final DEX uses the actual retained original classes.
    dex_dir = output / 'new-dex'
    dex_dir.mkdir(exist_ok=True)
    run(tools / 'build-tools/android-15/d8', '--min-api', '26', '--lib',
        tools / 'platform/android-35/android.jar', '--classpath', parent_jar, '--output', dex_dir, compiled)
    ui_smali = output / 'ui-smali'
    if ui_smali.exists():
        shutil.rmtree(ui_smali)
    run('java', '-cp', jar, 'com.android.tools.smali.baksmali.Main', 'd', dex_dir / 'classes.dex', '-o', ui_smali)
    for path in ui_smali.rglob('*.smali'):
        relative = path.relative_to(ui_smali)
        if relative.parts[:2] != ('app', 'weqrom'):
            raise ValueError('Unexpected compiled class outside Weqrom package: ' + str(relative))
        target = smali / relative
        if target.exists():
            raise ValueError('New class collides with original mod')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    result = output / 'classes.dex'
    run('java', '-cp', jar, 'com.android.tools.smali.smali.Main', 'a', '-a', '26', smali, '-o', result)
    payload = result.read_bytes()
    replacements = {MOD_ENTRY: payload, MANIFEST_ENTRY: refresh_manifest(manifest, payload),
                    'classes57.dex': (parent_dex / 'classes.dex').read_bytes()}
    loader_reports = {}
    with ZipFile(source) as apk:
        for name in ('classes46.dex', 'classes50.dex', 'classes52.dex'):
            folder = output / name.removesuffix('.dex')
            folder.mkdir(exist_ok=True)
            original_loader = folder / 'original.dex'
            original_loader.write_bytes(apk.read(name))
            loader_smali = folder / 'smali'
            if loader_smali.exists():
                shutil.rmtree(loader_smali)
            run('java', '-cp', jar, 'com.android.tools.smali.baksmali.Main', 'd', original_loader, '-o', loader_smali)
            loader_reports[name] = patch_loader_smali(loader_smali, dex_name=name)
            if name == 'classes52.dex':
                bootstrap = loader_smali / 'com/windukk/stable/StableBootstrap.smali'
                bootstrap.write_text(patch_region_context(patch_bootstrap_text(bootstrap.read_text())))
            built_loader = folder / 'classes.dex'
            run('java', '-cp', jar, 'com.android.tools.smali.smali.Main', 'a', '-a', '26', loader_smali, '-o', built_loader)
            replacements[name] = built_loader.read_bytes()
        core = apk.read('classes2.dex')
        if hashlib.sha256(core).hexdigest() != SOURCE_DEX_SHA256:
            raise ValueError('Pinned core request DEX checksum differs')
    core_dir = output / 'request-core'
    core_dir.mkdir(exist_ok=True)
    core_input = core_dir / 'original.dex'
    core_input.write_bytes(core)
    core_smali = core_dir / 'smali'
    if core_smali.exists():
        shutil.rmtree(core_smali)
    # The pinned JADX bundle shades JCommander with an incompatible validator
    # annotation for explicit -j. Limit its default worker count through the JVM.
    run('java', '-Xmx6g', '-XX:ActiveProcessorCount=2', '-cp', jar,
        'com.android.tools.smali.baksmali.Main', 'd', core_input, '-o', core_smali)
    request_report = patch_request_smali(core_smali)
    request_urls_report = patch_request_urls_smali(core_smali)
    core_output = core_dir / 'classes.dex'
    run('java', '-Xmx6g', '-XX:ActiveProcessorCount=2', '-cp', jar,
        'com.android.tools.smali.smali.Main', 'a', '-a', '26', core_smali, '-o', core_output)
    core_bytes = core_output.read_bytes()
    import struct
    core_methods = struct.unpack_from('<I', core_bytes, 88)[0]
    if core_methods > 65535:
        raise ValueError('Core request DEX exceeds method-reference limit')
    replacements['classes2.dex'] = core_bytes
    request_report['result_method_ids'] = core_methods
    request_report['result_sha256'] = hashlib.sha256(core_bytes).hexdigest()
    audit = {'base_mod_sha256': MOD_SHA, 'mod_sha256': hashlib.sha256(payload).hexdigest(),
             'mod_bytes': len(payload), 'region': region, 'welcome': welcome,
             'source_region_selection': region_selection, 'request_region': request_report,
             'request_region_urls': request_urls_report,
             'stable_api_version': WEQROM_STABLE_API, 'loader': loader_reports,
             'startup_hook': 'Main.start immediately after ModSettings initialization',
             'independent_ui_dex': 'classes57.dex',
             'bootstrap_diagnostics': True,
             'signature_verification_retained': True, 'remote_public_key_retained': True,
             'android_runtime_tested': False}
    (output / 'mod-report.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    return replacements, audit
