"""Pinned official JADX distribution also supplies the Smali CLI libraries."""
from pathlib import Path
import hashlib
import urllib.request
from zipfile import ZipFile

URL = 'https://github.com/skylot/jadx/releases/download/v1.5.3/jadx-1.5.3.zip'
SHA256 = '8280f3799c0273fe797a2bcd90258c943e451fd195f13d05400de5e6451d15ec'
MEMBER = 'lib/jadx-1.5.3-all.jar'


def ensure_jar(tools=Path('/workspace/.chits-tools')):
    tools = Path(tools)
    archive = tools / 'jadx-1.5.3.zip'
    if not archive.exists():
        tools.mkdir(parents=True, exist_ok=True)
        partial = archive.with_suffix('.zip.partial')
        with urllib.request.urlopen(URL, timeout=60) as response, partial.open('wb') as target:
            while block := response.read(1024 * 1024):
                target.write(block)
        if hashlib.sha256(partial.read_bytes()).hexdigest() != SHA256:
            raise ValueError('Official JADX archive checksum mismatch')
        partial.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise ValueError('Cached JADX archive checksum mismatch')
    with ZipFile(archive) as package:
        expected = package.read(MEMBER)
    jar = tools / 'jadx' / MEMBER
    if not jar.exists() or hashlib.sha256(jar.read_bytes()).digest() != hashlib.sha256(expected).digest():
        jar.parent.mkdir(parents=True, exist_ok=True)
        jar.write_bytes(expected)
    return jar


if __name__ == '__main__':
    print(ensure_jar())
