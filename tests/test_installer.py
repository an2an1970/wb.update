import io
from pathlib import Path
import tempfile
import unittest
import zipfile
from installer.install import safe_extract, swap

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

    def test_swap_backup(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            old, new, backup = p / "app", p / "new", p / "backup"
            old.mkdir()
            (old / "config").write_text("preserve")
            new.mkdir()
            (new / "jar").write_text("new")
            self.assertTrue(swap(new, old, backup))
            self.assertTrue((old / "jar").exists())
            self.assertTrue((backup / "config").exists())

if __name__ == "__main__":
    unittest.main()
