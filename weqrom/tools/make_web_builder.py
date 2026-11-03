#!/usr/bin/env python3
"""Create a small offline COPY/INSERT browser builder for a pre-signed APK.

Unchanged compressed ZIP payloads are copied from an exact user-owned input.
No APK code is executed, no signatures are changed, and no private key is used.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import struct
import tempfile
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED, BadZipFile

PROJECT = Path(__file__).resolve().parents[1]
GENERATOR = 'weqrom.offline-builder/v1'
MAX_APK = (1 << 32) - 1
MAX_INSERT = 64 * 1024 * 1024
MEMBERS = {'index.html', 'styles.css', 'builder.js', 'patch.js', 'README.txt', 'build-info.json'}


class BuilderError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: Path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def checked_file(raw: Path) -> Path:
    path = Path(os.path.abspath(raw))
    if path.is_symlink() or not path.is_file():
        raise BuilderError('Input must be a regular, non-symlink APK file')
    if not 22 <= path.stat().st_size <= MAX_APK:
        raise BuilderError('APK must fit the supported classic ZIP size range')
    return path


def entries(path: Path):
    try:
        with ZipFile(path) as archive:
            result = archive.infolist()
    except BadZipFile as error:
        raise BuilderError('Input is not a readable APK/ZIP') from error
    duplicate = [name for name, count in Counter(item.filename for item in result).items() if count > 1]
    if duplicate:
        raise BuilderError('APK contains duplicate ZIP entries')
    if len(result) > 100000 or any(item.flag_bits & 1 for item in result):
        raise BuilderError('Encrypted or overly large entry lists are not supported')
    return result


def payload(stream, info: ZipInfo, total: int):
    offset = info.header_offset
    if offset < 0 or offset + 30 > total:
        raise BuilderError('Invalid ZIP local-header offset')
    stream.seek(offset)
    header = stream.read(30)
    if len(header) != 30 or header[:4] != b'PK\x03\x04':
        raise BuilderError('Invalid ZIP local header')
    name_size, extra_size = struct.unpack_from('<HH', header, 26)
    start = offset + 30 + name_size + extra_size
    if info.compress_size < 0 or start + info.compress_size > total:
        raise BuilderError('ZIP compressed payload escapes the file')
    return start, info.compress_size


def equal_span(first, second, first_offset: int, second_offset: int, size: int) -> bool:
    first.seek(first_offset)
    second.seek(second_offset)
    remaining = size
    while remaining:
        count = min(1024 * 1024, remaining)
        a, b = first.read(count), second.read(count)
        if len(a) != count or len(b) != count or a != b:
            return False
        remaining -= count
    return True


def make_patch(source: Path, target: Path, max_insert: int = MAX_INSERT):
    source, target = checked_file(source), checked_file(target)
    if source == target:
        raise BuilderError('Source and target paths must differ')
    if not 0 <= max_insert <= MAX_INSERT:
        raise BuilderError('Invalid insertion-data limit')
    original_id, target_id = identity(source), identity(target)
    source_size, target_size = original_id[2], target_id[2]
    original_entries = {item.filename: item for item in entries(source)}
    target_entries = sorted(entries(target), key=lambda item: item.header_offset)
    instructions = []
    inserted = bytearray()
    position = 0
    copied = 0

    def copy(offset, length):
        nonlocal copied
        if not length:
            return
        copied += length
        if instructions and instructions[-1][0] == 'copy' and instructions[-1][1] + instructions[-1][2] == offset:
            instructions[-1][2] += length
        else:
            instructions.append(['copy', offset, length])

    with source.open('rb') as base, target.open('rb') as output:
        def insert(offset, length):
            if not length:
                return
            if length < 0 or len(inserted) + length > max_insert:
                raise BuilderError('Changed bytes exceed the small-builder limit; preserve compressed ZIP payloads when building the target')
            start = len(inserted)
            output.seek(offset)
            remaining = length
            while remaining:
                block = output.read(min(1024 * 1024, remaining))
                if not block:
                    raise BuilderError('Incomplete target APK')
                inserted.extend(block)
                remaining -= len(block)
            if instructions and instructions[-1][0] == 'insert' and instructions[-1][1] + instructions[-1][2] == start:
                instructions[-1][2] += length
            else:
                instructions.append(['insert', start, length])

        for item in target_entries:
            candidate = original_entries.get(item.filename)
            if candidate is None or (candidate.compress_type, candidate.compress_size, candidate.CRC) != (item.compress_type, item.compress_size, item.CRC):
                continue
            base_start, size = payload(base, candidate, source_size)
            target_start, _ = payload(output, item, target_size)
            if target_start < position or not equal_span(base, output, base_start, target_start, size):
                continue
            # ZIP alignment can change headers while leaving the expensive APK
            # payload untouched. Copy full local records only when byte-identical.
            base_header = base_start - candidate.header_offset
            target_header = target_start - item.header_offset
            if base_header == target_header and item.header_offset >= position and equal_span(base, output, candidate.header_offset, item.header_offset, base_header):
                base_start = candidate.header_offset
                target_start = item.header_offset
                size += base_header
            insert(position, target_start - position)
            copy(base_start, size)
            position = target_start + size
        insert(position, target_size - position)

    if identity(source) != original_id or identity(target) != target_id:
        raise BuilderError('An input changed during patch generation')
    source_hash, target_hash = sha256(source), sha256(target)
    if identity(source) != original_id or identity(target) != target_id:
        raise BuilderError('An input changed during hashing')
    patch = {
        'schema': 1,
        'source': {'name': source.name, 'size': source_size, 'sha256': source_hash},
        'target': {'name': 'Weqrom.apk', 'size': target_size, 'sha256': target_hash},
        'ops': instructions,
        'insertSize': len(inserted),
        'data': base64.b64encode(inserted).decode('ascii'),
    }
    if sum(item[2] for item in instructions) != target_size:
        raise BuilderError('Patch operations do not reconstruct the declared size')
    return patch, {'copied_bytes': copied, 'inserted_bytes': len(inserted), 'operations': len(instructions)}


def reconstruct(source: Path, patch: dict, target: Path):
    """Python verifier used by tests and build tooling, not by the browser UI."""
    if sha256(source) != patch['source']['sha256'] or source.stat().st_size != patch['source']['size']:
        raise BuilderError('Wrong source file')
    insert = base64.b64decode(patch['data'], validate=True)
    if len(insert) != patch['insertSize']:
        raise BuilderError('Invalid insertion data')
    digest = hashlib.sha256()
    written = 0
    with source.open('rb') as original, target.open('wb') as output:
        for operation, offset, length in patch['ops']:
            if not isinstance(offset, int) or not isinstance(length, int) or offset < 0 or length <= 0:
                raise BuilderError('Invalid patch span')
            if operation == 'copy':
                if offset + length > source.stat().st_size:
                    raise BuilderError('Copy exceeds source')
                original.seek(offset)
                remaining = length
                while remaining:
                    data = original.read(min(1024 * 1024, remaining))
                    if not data:
                        raise BuilderError('Incomplete copy')
                    output.write(data)
                    digest.update(data)
                    remaining -= len(data)
            elif operation == 'insert':
                if offset + length > len(insert):
                    raise BuilderError('Insert exceeds patch')
                data = insert[offset:offset + length]
                output.write(data)
                digest.update(data)
            else:
                raise BuilderError('Unknown patch operation')
            written += length
    if written != patch['target']['size'] or digest.hexdigest() != patch['target']['sha256']:
        raise BuilderError('Reconstructed APK checksum mismatch')


def output_path(raw: Path):
    path = Path(os.path.abspath(raw))
    if path.is_symlink() or path.is_file():
        raise BuilderError('Output must be a real directory')
    for parent in path.parents:
        if parent.is_symlink():
            raise BuilderError('Output parents must not be symlinks')
    return path


def verify_owned(path: Path):
    if not path.exists() or not any(path.iterdir()):
        return
    actual = {entry.name for entry in path.iterdir()}
    if actual != MEMBERS or any(entry.is_symlink() or not entry.is_file() for entry in path.iterdir()):
        raise BuilderError('Output contains files not owned by this generator')
    try:
        info = json.loads((path / 'build-info.json').read_text())
        if info.get('generator') != GENERATOR or set(info.get('file_sha256', {})) != MEMBERS - {'build-info.json'}:
            raise ValueError()
        for name, expected in info['file_sha256'].items():
            if sha256(path / name) != expected:
                raise ValueError()
    except (ValueError, OSError, KeyError, TypeError):
        raise BuilderError('Previous output was changed; refusing to overwrite it')


def package(source: Path, target: Path, destination: Path, archive: Path | None = None, *, templates: Path | None = None):
    destination = output_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    verify_owned(destination)
    source, target = checked_file(source), checked_file(target)
    for path in (source, target):
        if destination == path or destination in path.parents:
            raise BuilderError('Input must not be stored inside the output directory')
    patch, summary = make_patch(source, target)
    templates = templates or PROJECT / 'web'
    for name in ('index.html', 'styles.css', 'builder.js'):
        path = templates / name
        if not path.is_file() or path.is_symlink():
            raise BuilderError('Browser UI templates are missing')
    if archive is not None:
        archive = Path(os.path.abspath(archive))
        if archive.is_symlink() or archive in (source, target) or destination in archive.parents:
            raise BuilderError('Invalid ZIP output location')
        if archive.exists():
            try:
                with ZipFile(archive) as old:
                    info = json.loads(old.read('build-info.json'))
                    if info.get('generator') != GENERATOR:
                        raise ValueError()
            except (ValueError, OSError, KeyError, BadZipFile):
                raise BuilderError('ZIP output is not an existing generated builder')
        archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.weqrom-builder-', dir=destination.parent) as temporary:
        staged = Path(temporary) / 'ready'
        staged.mkdir()
        for name in ('index.html', 'styles.css', 'builder.js'):
            shutil.copyfile(templates / name, staged / name)
        # Embed everything in one HTML file: ordinary file:// browsers need no
        # fetch, module import, server, Internet or access to adjacent files.
        data_script = 'window.WEQROM_PATCH=' + json.dumps(patch, ensure_ascii=True, separators=(',', ':')).replace('<', '\\u003c') + ';\n'
        (staged / 'patch.js').write_text(data_script, encoding='utf-8')
        builder_script = (staged / 'builder.js').read_text(encoding='utf-8')
        css = (staged / 'styles.css').read_text(encoding='utf-8')
        def csp_hash(value):
            return "'sha256-" + base64.b64encode(hashlib.sha256(value.encode('utf-8')).digest()).decode('ascii') + "'"
        csp = ("default-src 'none'; script-src " + csp_hash(data_script) + ' ' + csp_hash(builder_script)
               + "; style-src " + csp_hash(css) + "; img-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'")
        html = (staged / 'index.html').read_text(encoding='utf-8')
        html, changed = re.subn(r'<meta http-equiv="Content-Security-Policy" content="[^"]*">',
                               lambda match: '<meta http-equiv="Content-Security-Policy" content="' + csp + '">', html)
        if changed != 1:
            raise BuilderError('Browser template has no unambiguous CSP')
        for old, new in [('<link rel="stylesheet" href="styles.css">', '<style>' + css + '</style>'),
                         ('<script src="patch.js"></script>', '<script>' + data_script + '</script>'),
                         ('<script src="builder.js"></script>', '<script>' + builder_script + '</script>')]:
            if html.count(old) != 1:
                raise BuilderError('Browser template asset references changed unexpectedly')
            html = html.replace(old, new)
        (staged / 'index.html').write_text(html, encoding='utf-8')
        (staged / 'README.txt').write_text(
            'Weqrom — локальная сборка готового подписанного APK\n\n'
            '1. Полностью распакуйте ZIP в папку.\n'
            '2. Откройте index.html в современном браузере на компьютере.\n'
            '3. Выберите исходный ' + patch['source']['name'] + ', затем нажмите «Собрать Weqrom».\n'
            '4. После проверки скачайте Weqrom.apk. Пароли, Python и Интернет для сборки не нужны.\n\n'
            'Файл остаётся на компьютере. Сборщик не выполняет код APK, а восстанавливает точные заранее подписанные байты.\n'
            'Сборка основана на предоставленном TikTok You 47.4.3. Безопасность, вход и работа региона не подтверждены.\n'
            'Ключ подписи новый: для замены старого TikTok может потребоваться его удаление. Сначала сохраните локальные черновики.\n', encoding='utf-8')
        public_info = {'generator': GENERATOR, 'source': patch['source'], 'target': patch['target'],
                       **summary, 'file_sha256': {name: sha256(staged / name) for name in sorted(MEMBERS - {'build-info.json'})}}
        (staged / 'build-info.json').write_text(json.dumps(public_info, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if destination.exists():
            verify_owned(destination)
            for path in destination.iterdir():
                path.unlink()
            destination.rmdir()
        staged.replace(destination)
    if archive is not None:
        handle, temporary_zip = tempfile.mkstemp(prefix='.weqrom-archive-', dir=archive.parent)
        os.close(handle)
        try:
            with ZipFile(temporary_zip, 'w', ZIP_DEFLATED, compresslevel=9) as output:
                for name in sorted(MEMBERS):
                    info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    output.writestr(info, (destination / name).read_bytes(), compresslevel=9)
            Path(temporary_zip).replace(archive)
        finally:
            Path(temporary_zip).unlink(missing_ok=True)
    return {'source': patch['source'], 'target': patch['target'], **summary,
            'output_directory': str(destination), 'zip_bytes': archive.stat().st_size if archive else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--zip', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(package(args.source, args.target, args.output_dir, args.zip), ensure_ascii=False, indent=2))
    except (BuilderError, OSError, ValueError) as error:
        parser.exit(1, f'Builder error: {error}\n')


if __name__ == '__main__':
    main()
