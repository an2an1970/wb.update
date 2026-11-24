import io
import ast
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
import zipfile
from argparse import Namespace
from packager.build import build, verify, INSTALLER

class PackageTests(unittest.TestCase):
    def test_zip_entrypoint_extracts_every_installer_file(self):
        source = (Path(__file__).resolve().parents[1] / 'packager' / 'zip_main.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        files = next(ast.literal_eval(node.value) for node in tree.body
                     if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'FILES'
                                                            for target in node.targets))
        self.assertEqual(set(files), set(INSTALLER) - {'__main__.py'})

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
            with zipfile.ZipFile(a) as package:
                for name in ('__main__.py', 'installer/install.py', 'installer/experiment_storage.py', 'installer/setup-qr-uart.sh', 'packager/build.py'):
                    self.assertIn(name, package.namelist())
            proc = subprocess.run([sys.executable, str(a), '--help'], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('verify', proc.stdout)
            self.assertIn('install', proc.stdout)
            verified = subprocess.run([sys.executable, str(a), 'verify'], capture_output=True, text=True)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertIn('"valid_checksums": true', verified.stdout)
            invalid = subprocess.run([sys.executable, str(a), 'verify', 'extra'], capture_output=True, text=True)
            self.assertNotEqual(invalid.returncode, 0)
            invalid = subprocess.run([sys.executable, str(a), 'bogus'], capture_output=True, text=True)
            self.assertNotEqual(invalid.returncode, 0)
            with zipfile.ZipFile(a) as z:
                entries = {n:z.read(n) for n in z.namelist()}
            entries['firmware/v4/app.bin'] = b'corrupt'
            with zipfile.ZipFile(p/'bad.wbpkg','w') as z:
                for n,data in entries.items():z.writestr(n,data)
            with self.assertRaises(ValueError):verify(p/'bad.wbpkg')
            bad = subprocess.run([sys.executable, str(p/'bad.wbpkg'), 'verify'], capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)

if __name__ == '__main__': unittest.main()
