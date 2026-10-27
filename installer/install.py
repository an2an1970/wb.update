#!/usr/bin/env python3
"""Install WB backend JAR and static UI on wb22. Firmware is not flashed."""
import argparse
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packager"))
from build import verify

ROOT = Path("/opt/wb.srv")
EXPECTED = {"backend/wb.srv.jar", "ui/dist.zip", "firmware/v4/app.bin",
            "firmware/v4/bootloader.bin", "firmware/v4/partitions.bin"}

def run(*args):
    subprocess.run(args, check=True)

def safe_extract(z, dest):
    names = set()
    for info in z.infolist():
        name = info.filename
        p = PurePosixPath(name)
        if not name or p.is_absolute() or ".." in p.parts or "\\" in name or name in names:
            raise ValueError("Unsafe ZIP entry: " + name)
        names.add(name)
        mode = (info.external_attr >> 16) & 0o170000
        if mode not in (0, 0o100000, 0o040000):
            raise ValueError("Unsupported ZIP entry: " + name)
        target = dest.joinpath(*p.parts)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)

def prepare(pkg, dest):
    manifest = verify(pkg)
    if manifest.get("schema") != 1 or manifest.get("target") != {"board": "wb-v4", "mcu": "esp32s3"}:
        raise ValueError("Unsupported package")
    if set(manifest["files"]) != EXPECTED:
        raise ValueError("Unexpected package contents")
    with zipfile.ZipFile(pkg) as z:
        (dest / "wb.srv.jar").write_bytes(z.read("backend/wb.srv.jar"))
        ui = dest / "static"
        ui.mkdir()
        with zipfile.ZipFile(io.BytesIO(z.read("ui/dist.zip"))) as ui_zip:
            safe_extract(ui_zip, ui)
    if not (dest / "static" / "index.html").is_file():
        raise ValueError("UI ZIP must contain index.html at root")
    return manifest

def install(args):
    root = Path(args.root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Invalid deployment root")
    jar, ui = root / "wb.srv.jar", root / "static"
    if jar.is_symlink() or ui.is_symlink() or not jar.is_file() or not ui.is_dir():
        raise ValueError("Expected regular JAR and static directory")
    if not (root / "lib").is_dir():
        raise ValueError("Missing lib directory required by systemd classpath")
    manifest = verify(args.package)
    if args.dry_run:
        print(json.dumps({"verified": True, "version": manifest["version"], "root": str(root),
                          "jar": str(jar), "ui": str(ui), "firmware": "unchanged"}, indent=2))
        return
    if os.geteuid() != 0:
        raise PermissionError("Installation requires root")
    if not args.allow_unsigned:
        raise ValueError("Unsigned package: --allow-unsigned required for test deployments")
    # All temporary and backup files reside on the same filesystem as live files.
    with tempfile.TemporaryDirectory(prefix=".wb-update-", dir=root) as tmp:
        stage = Path(tmp)
        manifest = prepare(args.package, stage)
        # Preserve permissions and ownership of existing deployment files.
        shutil.copymode(jar, stage / "wb.srv.jar")
        shutil.copystat(jar, stage / "wb.srv.jar")
        os.chown(stage / "wb.srv.jar", jar.stat().st_uid, jar.stat().st_gid)
        shutil.copystat(ui, stage / "static")
        for base, dirs, files in os.walk(stage / "static"):
            for name in dirs + files:
                p = Path(base) / name
                if not p.is_symlink():
                    os.chown(p, ui.stat().st_uid, ui.stat().st_gid)
        os.chown(stage / "static", ui.stat().st_uid, ui.stat().st_gid)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = root / "backup" / ("wb-update-" + stamp)
        if backup.exists():
            raise FileExistsError(backup)
        backup.mkdir(parents=True)
        swapped = []
        try:
            run("systemctl", "stop", "wb-srv.service")
            # wb-ui.service requires wb-srv and may be stopped by systemd; restart if needed.
            for live, staged in ((jar, stage / "wb.srv.jar"), (ui, stage / "static")):
                previous = backup / live.name
                live.rename(previous)
                swapped.append((live, previous))
                staged.rename(live)
            run("systemctl", "start", "wb-srv.service")
            run("systemctl", "is-active", "--quiet", "wb-srv.service")
            # Health probe is intentionally stronger than systemd active state.
            run("curl", "--fail", "--silent", "--show-error", "--max-time", "10",
                "http://127.0.0.1:7777/favicon.ico", "-o", "/dev/null")
            print(json.dumps({"installed": manifest["version"], "backup": str(backup),
                              "firmware": "unchanged"}, indent=2))
        except BaseException:
            try: run("systemctl", "stop", "wb-srv.service")
            except Exception: pass
            for live, previous in reversed(swapped):
                if live.exists():
                    if live.is_dir(): shutil.rmtree(live)
                    else: live.unlink()
                previous.rename(live)
            try: run("systemctl", "start", "wb-srv.service")
            except Exception: pass
            raise

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("package")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--allow-unsigned", action="store_true", help="TEST ONLY")
    install(p.parse_args())

if __name__ == "__main__":
    main()
