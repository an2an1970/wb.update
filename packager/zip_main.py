"""Entry point for a self-contained WB .wbpkg Python ZIP application.

Usage: python3 wb-VERSION.wbpkg verify
       sudo python3 wb-VERSION.wbpkg install --allow-unsigned
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

FILES = ("installer/install.py", "installer/experiment_storage.py",
         "installer/setup-qr-uart.sh", "installer/enable-wb-screen.sh",
         "installer/screen_setup.py", "installer/kiosk-lockdown.sh",
         "installer/network_support.py", "installer/bootstrap.py", "packager/build.py")

def main():
    package = Path(sys.argv[0]).resolve()
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        print("\nCommands: verify, install")
        print("Install options: --dry-run, --allow-unsigned, --screen-idle-confirmed")
        return 0 if args else 2
    command = args.pop(0)
    if command not in ("verify", "install"):
        print("Unknown command: " + command + " (use verify or install)", file=sys.stderr)
        return 2
    if command == "verify" and args:
        print("verify takes no options", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="wbpkg-") as tmp:
        root = Path(tmp)
        with zipfile.ZipFile(package) as archive:
            for name in FILES:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root / "packager") + os.pathsep + env.get("PYTHONPATH", "")
        if command == "verify":
            cmd = [sys.executable, str(root / "packager/build.py"), "verify", str(package)]
        else:
            cmd = [sys.executable, str(root / "installer/install.py"), str(package), *args]
        return subprocess.call(cmd, env=env)

if __name__ == "__main__":
    sys.exit(main())
