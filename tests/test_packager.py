import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from argparse import Namespace
from packager.build import build, verify

class PackageTests(unittest.TestCase):
    def test_build_verify_determinism_and_corruption(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            for n in ('backend.jar','ui.zip'):
                with zipfile.ZipFile(p/n, 'w') as z:
                    z.writestr('index.html' if n=='ui.zip' else 'BOOT-INF/classes/test', b'hi')
            for n in ('app.bin','boot.bin','part.bin'):
                (p/n).write_bytes(n.encode())
            opts = dict(version='test',backend=str(p/'backend.jar'),ui=str(p/'ui.zip'),firmware=str(p/'app.bin'),bootloader=str(p/'boot.bin'),partitions=str(p/'part.bin'),backend_sha='a'*40,ui_sha='b'*40,firmware_sha='c'*40)
            a = build(Namespace(**opts, output=str(p/'a.wbpkg')))
            b = build(Namespace(**opts, output=str(p/'b.wbpkg')))
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertEqual(verify(a)['target']['board'], 'wb-v4')
            with zipfile.ZipFile(a) as z:
                entries = {n:z.read(n) for n in z.namelist()}
            entries['firmware/v4/app.bin'] = b'corrupt'
            with zipfile.ZipFile(p/'bad.wbpkg','w') as z:
                for n,data in entries.items():z.writestr(n,data)
            with self.assertRaises(ValueError):verify(p/'bad.wbpkg')

if __name__ == '__main__': unittest.main()
