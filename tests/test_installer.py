import io
from pathlib import Path
import tempfile
import unittest
import zipfile
from installer.install import safe_extract, prepare

class InstallerTests(unittest.TestCase):
    def test_safe_extract(self):
        with tempfile.TemporaryDirectory() as td:
            for entry in ("../escape", "/absolute", "dir/../../escape", "x\\y"):
                b = io.BytesIO()
                with zipfile.ZipFile(b, "w") as z:
                    z.writestr(entry, "bad")
                b.seek(0)
                with zipfile.ZipFile(b) as z:
                    with self.assertRaises(ValueError):
                        safe_extract(z, Path(td))
            b = io.BytesIO()
            with zipfile.ZipFile(b, "w") as z:
                z.writestr("index.html", "ok")
            b.seek(0)
            with zipfile.ZipFile(b) as z:
                safe_extract(z, Path(td))
            self.assertEqual((Path(td) / "index.html").read_text(), "ok")

    def test_prepare_rejects_invalid_package(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            invalid = p / "bad.wbpkg"
            invalid.write_text("not a ZIP")
            with self.assertRaises(zipfile.BadZipFile):
                prepare(invalid, p / "stage")

if __name__ == "__main__":
    unittest.main()
