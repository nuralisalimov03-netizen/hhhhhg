#!/usr/bin/env python3
"""Build Weqrom from the pinned user-provided APK and checked mod UI hooks.

No code is executed from the APK. Unchanged ZIP payloads retain their exact
compressed bytes, making a small, exact offline delivery patch possible.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

try:
    from .axml import rebrand_manifest, inspect_manifest, Manifest, NO_INDEX
except ImportError:
    from axml import rebrand_manifest, inspect_manifest, Manifest, NO_INDEX

PROJECT = Path(__file__).resolve().parents[1]
BASE_SHA = '9f34642dd5d62f96cc84bdf2a1e44f198f78fb5e813944ac1058714abef551bc'
BASE_CERT = '54ae4101cf1b18658c3b1cf0a49bea4d28a3b053a9622251dba92f74fc54dec7'
RELEASE_VERSION = '47.4.3-weqrom.1.3'
RELEASE_CODE = 2024704033
PNG_ICONS = {'res/ap/a.png':56,'res/am/b.png':84,'res/an/b.png':112,
             'res/ao/b.png':168,'res/aq/a.png':224}
XML_ICONS = ['res/ar/a.xml'] + [f'res/mipmap-anydpi-v26/ttmod_launcher_{name}.xml'
              for name in ('cheese','hlx','md','metal','nostalgic','parody')]
VECTOR = '''<vector xmlns:android="http://schemas.android.com/apk/res/android"
 android:width="108dp" android:height="108dp" android:viewportWidth="108" android:viewportHeight="108">
 <path android:fillColor="#080F0B" android:pathData="M25,8H83Q100,8 100,25V83Q100,100 83,100H25Q8,100 8,83V25Q8,8 25,8Z"/>
 <path android:fillColor="#8EFF66" android:pathData="M22,32H35L43,65L51,42H61L69,65L77,32H90L77,79H64L56,57L48,79H35Z"/>
 <path android:fillColor="#DCFFD1" android:pathData="M82,20A5,5 0,1 1,82 30A5,5 0,1 1,82 20Z"/>
</vector>'''


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def signing_entry(name):
    upper=name.upper()
    return upper in {'META-INF/MANIFEST.MF','STAMP-CERT-SHA256'} or bool(re.fullmatch(r'META-INF/[^/]+\.(SF|RSA|DSA|EC)',upper))


def _record_end(stream, info):
    stream.seek(info.header_offset)
    header=stream.read(30)
    if len(header)!=30 or header[:4]!=b'PK\x03\x04':raise ValueError('Invalid local ZIP header')
    values=struct.unpack('<4s5H3I2H',header)
    end=info.header_offset+30+values[-2]+values[-1]+info.compress_size
    if info.flag_bits&8:
        stream.seek(end);signature=stream.read(4)
        end+=16 if signature==b'PK\x07\x08' else 12
    return end


def _copy(stream,target,start,length):
    stream.seek(start)
    while length:
        block=stream.read(min(length,1024*1024))
        if not block:raise ValueError('Truncated input ZIP')
        target.write(block);length-=len(block)


def repack(source, target, replacements, added=None):
    """Copy raw local ZIP records for every unchanged active entry.

    ZipFile writes the new central directory only; it never recompresses the
    original DEX, native libraries or other unchanged compressed payloads.
    """
    added=added or {}
    if Path(source).resolve()==Path(target).resolve():raise ValueError('Output cannot replace input')
    with ZipFile(source) as original, Path(source).open('rb') as raw, ZipFile(target,'w') as output:
        names=[i.filename for i in original.infolist()]
        if len(set(names))!=len(names):raise ValueError('Duplicate ZIP entries')
        if set(replacements)-set(names):raise ValueError('Replacement entry is absent')
        if set(added)&set(names):raise ValueError('Added entry already exists')
        output.comment=original.comment
        for old in original.infolist():
            if signing_entry(old.filename):continue
            if old.filename in replacements:
                entry=ZipInfo(old.filename,old.date_time)
                entry.compress_type=old.compress_type
                entry.external_attr=old.external_attr
                output.writestr(entry,replacements[old.filename],compresslevel=9)
            else:
                entry=copy.copy(old);entry.header_offset=output.fp.tell()
                end=_record_end(raw,old)
                _copy(raw,output.fp,old.header_offset,end-old.header_offset)
                output.filelist.append(entry);output.NameToInfo[entry.filename]=entry
                output._didModify=True
                output.start_dir=output.fp.tell()
        for name,data in added.items():output.writestr(name,data,compress_type=ZIP_DEFLATED,compresslevel=9)


def rename_resource_literal(data, old='TikTok', new='Weqrom'):
    """Same-length replacement in the global resource-table string pool only."""
    if len(old)!=len(new):raise ValueError('Resource rename must retain character length')
    out=bytearray(data)
    kind,header_size,total=struct.unpack_from('<HHI',data)
    if kind!=2 or total!=len(data):raise ValueError('Invalid resource table')
    position=header_size
    while position+8<=len(data):
        kind,head,size=struct.unpack_from('<HHI',data,position)
        if size<head or position+size>len(data):raise ValueError('Invalid resource chunk')
        if kind==1:
            count,style_count,flags,start,styles=struct.unpack_from('<5I',data,position+8)
            def length(index,wide=False):
                if wide:
                    value=struct.unpack_from('<H',data,index)[0];index+=2
                    if value&0x8000:value=((value&0x7fff)<<16)|struct.unpack_from('<H',data,index)[0];index+=2
                else:
                    value=data[index];index+=1
                    if value&0x80:value=((value&0x7f)<<8)|data[index];index+=1
                return value,index
            changed=[]
            for index in range(count):
                offset=struct.unpack_from('<I',data,position+head+4*index)[0]
                item=position+start+offset
                chars,item=length(item,not bool(flags&0x100))
                if flags&0x100:
                    size_bytes,item=length(item);encoding='utf-8'
                else:size_bytes=chars*2;encoding='utf-16le'
                if item+size_bytes>position+size:raise ValueError('Invalid resource string')
                # Android resource pools may carry modified UTF-8 surrogate
                # sequences. Compare only the known ASCII branding bytes;
                # unrelated resource strings remain completely untouched.
                if data[item:item+size_bytes]==old.encode(encoding):
                    replacement=new.encode(encoding)
                    if len(replacement)!=size_bytes:raise ValueError('Resource byte lengths differ')
                    out[item:item+size_bytes]=replacement;changed.append(index)
            if len(changed)!=1:raise ValueError(f'Expected one global app-name literal, found {len(changed)}')
            return bytes(out),changed
        position+=size
    raise ValueError('Global resource string pool missing')


def png(size):
    from PIL import Image,ImageDraw
    scale=size*4/108
    image=Image.new('RGBA',(size*4,size*4),(0,0,0,0));draw=ImageDraw.Draw(image)
    draw.rounded_rectangle(tuple(round(x*scale) for x in (8,8,100,100)),radius=round(17*scale),fill='#080F0B')
    points=[(22,32),(35,32),(43,65),(51,42),(61,42),(69,65),(77,32),(90,32),(77,79),(64,79),(56,57),(48,79),(35,79)]
    draw.polygon([(round(x*scale),round(y*scale)) for x,y in points],fill='#8EFF66')
    draw.ellipse(tuple(round(x*scale) for x in (77,20,87,30)),fill='#DCFFD1')
    import io
    result=io.BytesIO();image.resize((size,size),Image.Resampling.LANCZOS).save(result,'PNG');return result.getvalue()


def release_manifest(data):
    document = Manifest(rebrand_manifest(data, label='Weqrom'))
    version = document.root.attr(document.pool, 'versionCode')
    name = document.root.attr(document.pool, 'versionName')
    sdk = next((n for n in document.nodes if document.pool.get(n.name) == 'uses-sdk' and n.depth == 1), None)
    minimum = sdk.attr(document.pool, 'minSdkVersion') if sdk else None
    if version is None or name is None or minimum is None or version.data != 2024704030:
        raise ValueError('Pinned release manifest attributes differ')
    version.raw, version.kind, version.data = NO_INDEX, 0x10, RELEASE_CODE
    name.set_text(document.pool, RELEASE_VERSION)
    # The original dynamically loaded mod already uses DEX038 (Android8).
    # Declare that real floor instead of promising Android6 support.
    minimum.raw, minimum.kind, minimum.data = NO_INDEX, 0x10, 26
    try:
        from .launcher_manifest import add_weqrom_launcher
    except ImportError:
        from launcher_manifest import add_weqrom_launcher
    return add_weqrom_launcher(document.build())


def checked(command,**kw):
    return subprocess.run([str(x) for x in command],check=True,**kw)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--tools',type=Path,default=Path('/workspace/.chits-tools'))
    parser.add_argument('--signing-dir',type=Path,default=Path('/workspace/.weqrom-signing'))
    args=parser.parse_args()
    source=args.source.resolve()
    if sha(source)!=BASE_SHA:raise SystemExit('Wrong source APK: SHA-256 differs from reviewed input')
    sdk=args.tools/'build-tools/android-15';android=args.tools/'platform/android-35/android.jar'
    verify=checked([sdk/'apksigner','verify','--min-sdk-version','24','--print-certs',source],capture_output=True,text=True)
    if BASE_CERT not in verify.stdout:raise SystemExit('Wrong source signing certificate')
    build=PROJECT/'build';dist=PROJECT/'dist';build.mkdir(exist_ok=True);dist.mkdir(exist_ok=True)
    try:
        from .mod_build import build_mod
    except ImportError:
        from mod_build import build_mod
    mod_replacements, mod_report = build_mod(source, args.tools, build/'mod')
    icon_res=build/'icon-res/drawable';icon_res.mkdir(parents=True,exist_ok=True)
    (icon_res/'weqrom.xml').write_text(VECTOR)
    manifest=build/'AndroidManifest.xml'
    manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="weqrom.icon"><uses-sdk android:minSdkVersion="23"/><application/></manifest>')
    icon_apk=build/'icon.apk'
    checked([sdk/'aapt','package','-f','-M',manifest,'-S',build/'icon-res','-I',android,'-F',icon_apk])
    with ZipFile(icon_apk) as z:vector=z.read('res/drawable/weqrom.xml')
    with ZipFile(source) as z:
        replacements={'AndroidManifest.xml':release_manifest(z.read('AndroidManifest.xml'))}
        replacements.update(mod_replacements)
        replacements['resources.arsc'],indices=rename_resource_literal(z.read('resources.arsc'))
        for name,size in PNG_ICONS.items():replacements[name]=png(size)
        for name in XML_ICONS:replacements[name]=vector
        before=inspect_manifest(z.read('AndroidManifest.xml'));after=inspect_manifest(replacements['AndroidManifest.xml'])
        if before['package']!=after['package']:raise SystemExit('Replacement package unexpectedly changed')
    notice=('Weqrom 1.3. Based on user-provided TikTok You 5.0 / TikTok47.4.3.\n'
            'Original TikTok DEX/native libraries retained. Bundled mod UI/settings hooks modified.\n'
            'New native greeting/settings, Belarus mod-region default, theme/accent controls.\n'
            'Not an official TikTok release. Android runtime/login/server region not verified.\n'
            'Base SHA256: '+BASE_SHA+'\n').encode()
    parent_dex = replacements.pop('classes57.dex')
    additions = {'assets/weqrom/NOTICE.txt': notice, 'classes57.dex': parent_dex}
    unsigned=build/'unsigned.apk';repack(source,unsigned,replacements,additions)
    aligned=build/'aligned.apk';checked([sdk/'zipalign','-P','16','-f','4',unsigned,aligned])
    signing=args.signing_dir;signing.mkdir(parents=True,exist_ok=True,mode=0o700)
    key=signing/'release.p12';password=signing/'password'
    if not key.exists():
        if password.exists():raise SystemExit('Signing password already exists without a key; preserve and inspect it separately')
        import secrets
        descriptor=os.open(password,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w') as f:f.write(secrets.token_urlsafe(32)+'\n')
        checked(['keytool','-genkeypair','-keystore',key,'-storetype','PKCS12','-alias','weqrom',
                 '-storepass:file',password,'-keypass:file',password,'-keyalg','RSA','-keysize','3072',
                 '-validity','10000','-dname','CN=Weqrom, O=Weqrom','-noprompt'])
    target=dist/'Weqrom.apk'
    checked([sdk/'apksigner','sign','--ks',key,'--ks-key-alias','weqrom','--ks-pass','file:'+str(password),
             '--v1-signer-name','WEQROM','--v1-signing-enabled','true','--v2-signing-enabled','true','--v3-signing-enabled','true',
             '--out',target,aligned])
    verification=checked([sdk/'apksigner','verify','--verbose','--print-certs',target],capture_output=True,text=True)
    (dist/'signature-report.txt').write_text(verification.stdout)
    checked([sdk/'zipalign','-c','-P','16','4',target])
    with ZipFile(source) as old,ZipFile(target) as new:
        if new.testzip() is not None:raise SystemExit('Final ZIP CRC failure')
        changed=[]
        for entry in old.infolist():
            if signing_entry(entry.filename):continue
            actual=new.getinfo(entry.filename)
            if entry.filename in replacements:changed.append(entry.filename);continue
            if (actual.CRC,actual.file_size)!=(entry.CRC,entry.file_size):raise SystemExit('Untouched payload metadata changed: '+entry.filename)
        for entry in old.infolist():
            if entry.filename.endswith(('.dex','.so')):
                if entry.filename in mod_replacements:continue
                if hashlib.sha256(old.read(entry)).digest()!=hashlib.sha256(new.read(entry.filename)).digest():raise SystemExit('Executable payload changed: '+entry.filename)
    report={'name':'Weqrom','package':before['package'],'base_sha256':BASE_SHA,'sha256':sha(target),
            'bytes':target.stat().st_size,'modified_entries':changed,'added_entries':list(additions),
            'version':RELEASE_VERSION,'version_code':RELEASE_CODE,'min_sdk':26,
            'resource_literal_indices':indices,'executables_unchanged':False,
            'modified_executable_entries':[name for name in mod_replacements if name.endswith('.dex')],
            'original_native_libraries_unchanged':True,'modified_mod':mod_report,'runtime_tested':False,
            'signature_verified':True,'source_attribution':'TikTok You 5.0 / TikTok47.4.3'}
    (dist/'build-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (dist/'SHA256SUMS.txt').write_text(report['sha256']+'  Weqrom.apk\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
