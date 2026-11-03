#!/usr/bin/env python3
"""Fetch official, immutable Qwen GGUF files for the APK bundler.

TLS verification and the inherited urllib HTTPS proxy remain enabled. There is
no production mirror/base-URL override. A pinned local manifest lets a failed
download resume without mixing files from a newer model revision.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


FAST_REPO = 'Qwen/Qwen2.5-0.5B-Instruct-GGUF'
VISION_REPO = 'ggml-org/Qwen2.5-VL-3B-Instruct-GGUF'
COMPONENT_LIMIT = 4 * 1024 ** 3
PROFILE_LIMIT = 6 * 1024 ** 3
JSON_LIMIT = 4 * 1024 ** 2
ROLES = {'fast': ('text',), 'full': ('text', 'vision', 'projector')}
HEX64 = re.compile(r'[0-9a-f]{64}')
REVISION = re.compile(r'[0-9a-f]{40}')


class FetchError(ValueError):
    pass


class IntegrityError(FetchError):
    pass


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_url(address, allow_loopback=False):
    parsed = urllib.parse.urlsplit(address)
    if parsed.username is not None or parsed.password is not None or not parsed.hostname:
        raise FetchError('Credential-bearing or invalid URL refused')
    local = allow_loopback and parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1')
    if parsed.scheme != 'https' and not local:
        raise FetchError('Model requests require verified HTTPS')
    return parsed


class _VerifiedRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 8

    def __init__(self, allow_loopback=False):
        self.allow_loopback = allow_loopback

    def redirect_request(self, request, file, code, message, headers, new_url):
        parsed = _valid_url(new_url, self.allow_loopback)
        if urllib.parse.urlsplit(request.full_url).scheme == 'https' and parsed.scheme != 'https':
            raise FetchError('HTTPS-to-HTTP redirect refused')
        return super().redirect_request(request, file, code, message, headers, new_url)


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(data)
    return digest.hexdigest()


def _atomic_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.chits-model-', delete=False) as output:
            temporary = Path(output.name)
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _cache_directory(raw):
    path = Path(os.path.abspath(raw))
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise FetchError('Model cache must not be a symlink')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _entry(role, repo, revision, record):
    if not isinstance(record, dict) or not isinstance(record.get('lfs'), dict):
        raise FetchError('The official tree entry has no LFS metadata')
    name = record.get('path')
    size = record.get('size')
    digest = record['lfs'].get('oid')
    if not isinstance(name, str) or '/' in name or '\\' in name or '..' in name or not name.lower().endswith('.gguf'):
        raise FetchError('Unsafe GGUF filename in metadata')
    if not _integer(size) or not 8 <= size <= COMPONENT_LIMIT:
        raise FetchError('Invalid or oversized GGUF component')
    if not isinstance(digest, str) or not HEX64.fullmatch(digest):
        raise FetchError('Missing or invalid official LFS SHA-256')
    if 'size' in record['lfs'] and record['lfs']['size'] != size:
        raise FetchError('Inconsistent LFS file size')
    return {'role': role, 'repo': repo, 'revision': revision, 'path': name,
            'size': size, 'sha256': digest,
            'filename': f'{role}-{revision}-{digest[:16]}.gguf'}


def select_files(repo, revision, tree):
    """Select quantizations from actual authoritative tree metadata."""
    if repo not in (FAST_REPO, VISION_REPO) or not isinstance(revision, str) or not REVISION.fullmatch(revision):
        raise FetchError('Unknown model repository or unpinned revision')
    if not isinstance(tree, list):
        raise FetchError('Official tree response is not a file list')
    languages, projectors = [], []
    for record in tree:
        if not isinstance(record, dict) or not isinstance(record.get('path'), str):
            continue
        name = record['path'].lower()
        if '/' in name or '\\' in name or '..' in name or not name.endswith('.gguf'):
            continue
        if name.startswith('mmproj'):
            projectors.append(record)
        elif 'q4_k_m' in name:
            languages.append(record)
    if len(languages) != 1:
        raise FetchError('The official repository has no unambiguous Q4_K_M model')
    role = 'text' if repo == FAST_REPO else 'vision'
    result = [_entry(role, repo, revision, languages[0])]
    if repo == VISION_REPO:
        def rank(record):
            name = record['path'].lower()
            return (0 if 'q8_0' in name else 1 if 'f16' in name else 2, name)
        projectors.sort(key=rank)
        if not projectors or rank(projectors[0])[0] == 2:
            raise FetchError('The official repository has no supported Q8_0/F16 projector')
        result.append(_entry('projector', repo, revision, projectors[0]))
    return result


def validate_manifest(manifest, profile):
    if not isinstance(manifest, dict) or not _integer(manifest.get('schema')) or manifest.get('schema') != 1 or manifest.get('profile') != profile:
        raise FetchError('Invalid pinned cache manifest')
    entries = manifest.get('files')
    if not isinstance(entries, list) or len(entries) != len(ROLES[profile]):
        raise FetchError('Pinned manifest has an invalid component list')
    roles = set()
    revisions = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('role') not in ROLES[profile] or entry['role'] in roles:
            raise FetchError('Pinned manifest has invalid or duplicate roles')
        role = entry['role']
        roles.add(role)
        repo = FAST_REPO if role == 'text' else VISION_REPO
        revision = entry.get('revision')
        if entry.get('repo') != repo or not isinstance(revision, str) or not REVISION.fullmatch(revision):
            raise FetchError('Pinned manifest changed repository or revision')
        rebuilt = _entry(role, repo, revision, {'path': entry.get('path'), 'size': entry.get('size'),
                                             'lfs': {'oid': entry.get('sha256')}})
        if entry != rebuilt:
            raise FetchError('Pinned manifest has inconsistent or unexpected fields')
        name = entry['path'].lower()
        if role == 'projector':
            if not name.startswith('mmproj') or not ('q8_0' in name or 'f16' in name):
                raise FetchError('Invalid pinned projector')
        elif name.startswith('mmproj') or 'q4_k_m' not in name:
            raise FetchError('Invalid pinned language quantization')
        if repo in revisions and revisions[repo] != revision:
            raise FetchError('Vision weights and projector must share one revision')
        revisions[repo] = revision
    if roles != set(ROLES[profile]) or sum(entry['size'] for entry in entries) > PROFILE_LIMIT:
        raise FetchError('Pinned profile is missing components or exceeds 6 GiB')
    return manifest


class Fetcher:
    def __init__(self, output_dir, *, _base_url='https://huggingface.co', _allow_loopback_tests=False):
        # Private test injection, intentionally not exposed as a command-line flag.
        _valid_url(_base_url, _allow_loopback_tests)
        self.base = _base_url.rstrip('/')
        self.allow_loopback = _allow_loopback_tests
        self.output = _cache_directory(output_dir)
        self.opener = urllib.request.build_opener(_VerifiedRedirect(_allow_loopback_tests))

    def open(self, address, headers=None):
        _valid_url(address, self.allow_loopback)
        return self.opener.open(urllib.request.Request(address, headers={
            'User-Agent': 'ChitsMarket-ModelBundler/1.2', 'Accept-Encoding': 'identity',
            **(headers or {})}), timeout=45)

    def json(self, address):
        with self.open(address, {'Accept': 'application/json'}) as response:
            if response.status != 200:
                raise FetchError('Metadata request did not return HTTP 200')
            data = response.read(JSON_LIMIT + 1)
            if len(data) > JSON_LIMIT:
                raise FetchError('Metadata response exceeds 4 MiB')
            return json.loads(data)

    def manifest(self, profile):
        path = self.output / f'manifest-{profile}.json'
        if path.is_symlink():
            raise FetchError('Pinned manifest must not be a symlink')
        if path.exists():
            with path.open('rb') as stream:
                data = stream.read(JSON_LIMIT + 1)
            if len(data) > JSON_LIMIT:
                raise FetchError('Pinned manifest exceeds 4 MiB')
            return validate_manifest(json.loads(data), profile)
        entries = []
        for repo in ((FAST_REPO,) if profile == 'fast' else (FAST_REPO, VISION_REPO)):
            api = self.base + '/api/models/' + repo
            metadata = self.json(api + '/revision/main')
            revision = metadata.get('sha') if isinstance(metadata, dict) else None
            if not isinstance(revision, str) or not REVISION.fullmatch(revision):
                raise FetchError('Official revision is not an immutable commit SHA')
            tree = self.json(api + '/tree/' + revision + '?recursive=false&limit=1000')
            entries.extend(select_files(repo, revision, tree))
        manifest = validate_manifest({'schema': 1, 'profile': profile, 'files': entries}, profile)
        _atomic_json(path, manifest)
        return manifest

    def download(self, entry, progress=None):
        role = entry.get('role') if isinstance(entry, dict) else None
        if role not in ('text', 'vision', 'projector'):
            raise FetchError('Invalid download role')
        repo = FAST_REPO if role == 'text' else VISION_REPO
        revision = entry.get('revision')
        if entry.get('repo') != repo or not isinstance(revision, str) or not REVISION.fullmatch(revision):
            raise FetchError('Unpinned download source')
        rebuilt = _entry(role, repo, revision, {'path': entry.get('path'), 'size': entry.get('size'),
                                             'lfs': {'oid': entry.get('sha256')}})
        if entry != rebuilt:
            raise FetchError('Inconsistent model cache filename or metadata')
        destination = self.output / entry['filename']
        partial = self.output / (entry['filename'] + '.part')
        if destination.is_symlink() or partial.is_symlink():
            raise FetchError('Model output must not be a symlink')
        expected, sha = entry['size'], entry['sha256']
        if destination.exists():
            if not destination.is_file():
                raise FetchError('Model output is not a file')
            if destination.stat().st_size == expected and _hash(destination) == sha:
                self._check_magic(destination)
                if progress:
                    progress(expected, expected)
                return destination
            destination.unlink()
        if partial.exists() and not partial.is_file():
            raise FetchError('Partial model is not a file')
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > expected:
            partial.unlink()
            raise IntegrityError('Oversized partial model was removed')
        if offset == expected:
            return self._finish(partial, destination, expected, sha)
        digest = hashlib.sha256()
        if offset:
            with partial.open('rb') as stream:
                for data in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(data)
        address = self.base + '/' + entry['repo'] + '/resolve/' + entry['revision'] + '/' + urllib.parse.quote(entry['path'], safe='')
        headers = {'Range': f'bytes={offset}-'} if offset else {}
        with self.open(address, headers) as response:
            if response.headers.get('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                raise FetchError('Encoded model response refused')
            if response.status == 206:
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                if not match or tuple(map(int, match.groups())) != (offset, expected - 1, expected):
                    raise FetchError('Partial response has an invalid Content-Range')
            elif response.status == 200:
                offset = 0
                digest = hashlib.sha256()
            else:
                raise FetchError('Model request must return HTTP 200 or 206')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isdecimal() or int(length) != expected - offset):
                raise FetchError('Model Content-Length differs from pinned metadata')
            with partial.open('ab' if offset else 'wb') as stream:
                os.chmod(partial, 0o600)
                current = offset
                while True:
                    data = response.read(1024 * 1024)
                    if not data:
                        break
                    current += len(data)
                    if current > expected:
                        stream.close()
                        partial.unlink(missing_ok=True)
                        raise IntegrityError('Oversized model response was removed')
                    stream.write(data)
                    digest.update(data)
                    if progress:
                        progress(current, expected)
                stream.flush()
                os.fsync(stream.fileno())
            if current != expected:
                raise FetchError('Connection ended early; the partial model is retained for resume')
            if digest.hexdigest() != sha:
                partial.unlink(missing_ok=True)
                raise IntegrityError('SHA-256 mismatch; corrupted partial model was removed')
        return self._finish(partial, destination, expected, sha)

    @staticmethod
    def _check_magic(path):
        with path.open('rb') as stream:
            if stream.read(4) != b'GGUF':
                raise IntegrityError('Verified bytes are not a GGUF model')

    def _finish(self, partial, destination, expected, sha):
        if partial.stat().st_size != expected or _hash(partial) != sha:
            partial.unlink(missing_ok=True)
            raise IntegrityError('Saved model failed its pinned size/SHA-256 check')
        try:
            self._check_magic(partial)
        except IntegrityError:
            partial.unlink(missing_ok=True)
            raise
        partial.replace(destination)
        return destination

    def fetch(self, profile, progress=None):
        if profile not in ROLES:
            raise FetchError('Unknown model profile')
        manifest = self.manifest(profile)
        remaining = 0
        for entry in manifest['files']:
            destination = self.output / entry['filename']
            part = self.output / (entry['filename'] + '.part')
            present = destination.stat().st_size if destination.is_file() else part.stat().st_size if part.is_file() else 0
            remaining += max(0, entry['size'] - min(present, entry['size']))
        if shutil.disk_usage(self.output).free < remaining + 256 * 1024 ** 2:
            raise FetchError('Insufficient disk space for remaining weights plus a 256 MiB margin')
        paths = {'schema': 1, 'profile': profile}
        for entry in manifest['files']:
            callback = (lambda current, total, role=entry['role']: progress(role, current, total)) if progress else None
            path = self.download(entry, callback)
            paths[entry['role']] = str(path)
            if entry['role'] == 'vision':
                paths['visionRevision'] = entry['revision']
        _atomic_json(self.output / 'paths.json', paths)
        return paths


def bundle_command(paths):
    command = ['python3', 'tools/bundle-models.py', '--text', paths['text']]
    if paths['profile'] == 'full':
        command += ['--vision', paths['vision'], '--projector', paths['projector'],
                    '--vision-revision', paths['visionRevision']]
    command += ['--output', 'app/src/main/assets/models']
    return shlex.join(command)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=ROLES, default='fast')
    parser.add_argument('--output-dir', type=Path, default=Path('/workspace/.chits-tools/models'))
    args = parser.parse_args()
    last = [0.0]
    def progress(role, current, total):
        now = time.monotonic()
        if current == total or now - last[0] > 2:
            print(f'{role}: {current}/{total} bytes ({current * 100 // total}%)', flush=True)
            last[0] = now
    try:
        paths = Fetcher(args.output_dir).fetch(args.profile, progress)
    except (FetchError, OSError, urllib.error.URLError, http.client.HTTPException, ValueError) as error:
        print(f'Model preparation incomplete: {error}', file=sys.stderr)
        return 1
    print('Verified GGUF paths saved to', args.output_dir / 'paths.json')
    print('Next, from chits-market:')
    print(bundle_command(paths))
    print('Then run: bash tools/build-apk.sh')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
