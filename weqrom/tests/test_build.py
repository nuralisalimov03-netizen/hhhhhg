import io
from pathlib import Path
import struct
import tempfile
import unittest
from zipfile import ZipFile,ZIP_DEFLATED

from weqrom.tools.build import repack,rename_resource_literal,signing_entry,png


class BuildTests(unittest.TestCase):
    def test_raw_zip_payload_preserved_and_signing_metadata_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.apk';target=Path(directory)/'target.apk'
            payload=(b'unchanged executable fixture '*4096)+bytes(range(256))
            with ZipFile(source,'w',ZIP_DEFLATED) as z:
                z.writestr('classes.dex',payload)
                z.writestr('lib/arm64-v8a/libfixture.so',payload[::-1])
                z.writestr('AndroidManifest.xml',b'old manifest fixture')
                z.writestr('META-INF/MANIFEST.MF',b'old v1 signing')
                z.writestr('META-INF/OLD.RSA',b'old cert')
                z.writestr('META-INF/versions/9/OSGI-INF/MANIFEST.MF',b'attribution metadata')
                z.writestr('stamp-cert-sha256',b'old stamp')
            repack(source,target,{'AndroidManifest.xml':b'new manifest fixture'}, {'assets/weqrom/NOTICE.txt':b'based on user input'})
            def compressed(path,info):
                with path.open('rb') as f:
                    f.seek(info.header_offset);head=f.read(30)
                    name,extra=struct.unpack_from('<HH',head,26)
                    f.seek(name+extra,1);return f.read(info.compress_size)
            with ZipFile(source) as old,ZipFile(target) as new:
                self.assertIsNone(new.testzip())
                self.assertEqual(new.read('AndroidManifest.xml'),b'new manifest fixture')
                for name in ['classes.dex','lib/arm64-v8a/libfixture.so','META-INF/versions/9/OSGI-INF/MANIFEST.MF']:
                    self.assertEqual(old.read(name),new.read(name))
                    self.assertEqual(compressed(source,old.getinfo(name)),compressed(target,new.getinfo(name)))
                self.assertNotIn('META-INF/MANIFEST.MF',new.namelist())
                self.assertNotIn('META-INF/OLD.RSA',new.namelist())
                self.assertNotIn('stamp-cert-sha256',new.namelist())
                self.assertEqual(new.read('assets/weqrom/NOTICE.txt'),b'based on user input')

    def test_resource_ascii_rename_preserves_android_modified_utf8(self):
        first=b'\x02\x06\xed\xa0\xbd\xed\xb8\x80\0'
        second=b'\x06\x06TikTok\0'
        raw=first+second;raw+=b'\0'*((-len(raw))%4)
        pool=struct.pack('<HHI5I2I',1,28,36+len(raw),2,0,0x100,36,0,0,len(first))+raw
        table=struct.pack('<HHII',2,12,12+len(pool),0)+pool
        changed,indices=rename_resource_literal(table)
        self.assertEqual(indices,[1])
        self.assertEqual(len(changed),len(table))
        self.assertIn(first,changed)
        self.assertIn(b'\x06\x06Weqrom\0',changed)
        self.assertEqual(table.replace(b'TikTok',b'Weqrom'),changed)

    def test_png_fallback_dimensions_and_old_signature_scope(self):
        from PIL import Image
        for size in (56,84,112,168,224):
            with Image.open(io.BytesIO(png(size))) as image:self.assertEqual(image.size,(size,size))
        self.assertTrue(signing_entry('META-INF/SIGNER.SF'))
        self.assertFalse(signing_entry('META-INF/versions/9/OSGI-INF/MANIFEST.MF'))


if __name__=='__main__':unittest.main()
