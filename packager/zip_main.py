"""Entry point for a self-contained WB .wbpkg Python ZIP application."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

def main():
    package = Path(sys.argv[0]).resolve()
    with tempfile.TemporaryDirectory(prefix="wbpkg-") as tmp:
        root = Path(tmp)
        with zipfile.ZipFile(package) as archive:
            names = ("installer/install.py", "installer/experiment_storage.py",
                     "installer/setup-qr-uart.sh", "packager/build.py")
            for name in names:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root / "packager") + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.call([sys.executable, str(root / "installer/install.py"),
                                str(package), *sys.argv[1:]], env=env)

if __name__ == "__main__":
    sys.exit(main())
