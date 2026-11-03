"""Package public sources and the signed APK; never include local credentials."""
from pathlib import Path
import hashlib
import json
import os
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
EXCLUDED = {'build', 'dist', 'node_modules', '.git', '.local', '.local-data',
            '__pycache__', '.firebase', 'models'}
SUFFIXES = {'.py', '.java', '.cpp', '.h', '.sh', '.md', '.txt', '.json', '.rules',
            '.js', '.mjs', '.css', '.html', '.svg', '.apk', '.so'}
NAMES = {'.gitignore', 'Dockerfile', 'CMakeLists.txt'}


def main():
    DIST.mkdir(exist_ok=True)
    apk = DIST / 'Chits-Market.apk'
    if not apk.is_file():
        raise SystemExit('Run bash tools/build-apk.sh first')
    archive = DIST / 'Chits-Market-source.zip'
    count = 0
    with ZipFile(archive, 'w', ZIP_DEFLATED) as output:
        for current, directories, files in os.walk(ROOT):
            directories[:] = sorted(name for name in directories if name not in EXCLUDED)
            for name in sorted(files):
                path = Path(current) / name
                if path.is_symlink():
                    raise RuntimeError(f'Refusing symlink: {path.relative_to(ROOT)}')
                if path.suffix not in SUFFIXES and name not in NAMES:
                    continue
                if name.endswith('.service-account.json') or '-debug.' in name or name == '.env':
                    continue
                if path.suffix == '.json':
                    data = json.loads(path.read_text())
                    if isinstance(data, dict) and (data.get('type') == 'service_account' or 'private_key' in data):
                        raise RuntimeError(f'Credential JSON cannot be published: {path.relative_to(ROOT)}')
                output.write(path, Path('chits-market') / path.relative_to(ROOT))
                count += 1
    with ZipFile(archive) as check:
        assert check.testzip() is None
    checksums = ''.join(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n'
                        for path in (apk, archive))
    (DIST / 'SHA256SUMS.txt').write_text(checksums)
    print(f'Public sources: {count} files; APK and ZIP checksums written to dist/SHA256SUMS.txt')


if __name__ == '__main__':
    main()
