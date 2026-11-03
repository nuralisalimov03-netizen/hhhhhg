"""Production vision metadata checks without model weights or Android inference.

The small pinned org.json jar is a host-test dependency only. Android provides
org.json at runtime; it is not packaged into the APK. Official Maven checksum is
verified on every run, including cached artifacts.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]
TOOLS = Path(os.environ.get('CHITS_TOOLS_DIR', '/workspace/.chits-tools'))
JSON_NAME = 'json-20250107.jar'
JSON_SHA256 = '85d4c1ab192d3117fd02c7fff1ec0fe63ade45cf56def7fe950ef060cf06e99f'


class VisionManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.classes = Path(cls.folder.name)
        jar = TOOLS / JSON_NAME
        if not jar.exists():
            TOOLS.mkdir(parents=True, exist_ok=True)
            data = urllib.request.urlopen('https://repo.maven.apache.org/maven2/org/json/json/20250107/'+JSON_NAME, timeout=40).read()
            if hashlib.sha256(data).hexdigest() != JSON_SHA256:
                raise RuntimeError('Official org.json checksum mismatch')
            jar.write_bytes(data)
        if hashlib.sha256(jar.read_bytes()).hexdigest() != JSON_SHA256:
            raise RuntimeError('Cached org.json checksum mismatch')
        android = TOOLS / 'platform/android-35/android.jar'
        cls.classpath = os.pathsep.join([str(cls.classes), str(jar), str(android)])
        sources = [PROJECT/'app/src/main/java/market/chits'/name for name in ['VisionModelManager.java','ModelDownloader.java','OfflineAi.java']]
        sources.append(PROJECT/'tests/java-vision/VisionManifestHarness.java')
        subprocess.run(['java','-jar',str(TOOLS/'ecj.jar'),'-1.8','-proc:none','-warn:none','-classpath',os.pathsep.join([str(jar),str(android)]),'-d',str(cls.classes),*[str(p) for p in sources]], check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def run_case(self, name):
        result = subprocess.run(['java','-cp',self.classpath,'market.chits.VisionManifestHarness',name],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('PASS '+name, result.stdout)


for case in ['official-pair','prefer-q8-projector','reject-ambiguous-language','reject-missing-projector','reject-unknown-projector','reject-missing-lfs-hash','reject-bad-hash','reject-oversize','reject-total-oversize','reject-cached-traversal','reject-wrong-repo','reject-unpinned-revision']:
    setattr(VisionManifestTests,'test_'+case.replace('-','_'),lambda self, name=case:self.run_case(name))

if __name__ == '__main__':
    unittest.main(verbosity=2)
