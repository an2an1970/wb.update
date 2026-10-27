#!/usr/bin/env python3
"""Offline WB package installer (backend + UI). ESP32 flashing intentionally unsupported."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

# Import verifier without depending on Python package installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packager"))
from build import verify

def run(cmd):
    subprocess.run(cmd, check=True)

def safe_extract(z, dest):
    names = set()
    for info in z.infolist():
        name = info.filename
        p = PurePosixPath(name)
        if not name or p.is_absolute() or ".." in p.parts or "\\" in name or name in names:
            raise ValueError("Unsafe ZIP entry: " + name)
        names.add(name)
        # Reject symlinks and special files.
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

def prepare(pkg, stage):
    import io
    manifest = verify(pkg)
    if manifest.get("schema") != 1 or manifest.get("target") != {"board": "wb-v4", "mcu": "esp32s3"}:
        raise ValueError("Unsupported package schema/target")
    expected = {"backend/wb.srv.jar", "ui/dist.zip", "firmware/v4/app.bin",
                "firmware/v4/bootloader.bin", "firmware/v4/partitions.bin"}
    if set(manifest["files"]) != expected:
        raise ValueError("Unexpected package contents")
    backend = stage / "backend"
    ui = stage / "ui"
    backend.mkdir()
    ui.mkdir()
    with zipfile.ZipFile(pkg) as z:
        with (backend / "wb.srv.jar").open("wb") as out:
            out.write(z.read("backend/wb.srv.jar"))
        with zipfile.ZipFile(io.BytesIO(z.read("ui/dist.zip"))) as ui_zip:
            safe_extract(ui_zip, ui)
    if not (ui / "index.html").is_file():
        raise ValueError("UI archive has no index.html at root")
    return manifest

def swap(src, target, backup):
    if target.is_symlink():
        raise ValueError("Refusing to replace symlink: " + str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    if existed:
        target.rename(backup)
    try:
        src.rename(target)
    except BaseException:
        if existed:
            backup.rename(target)
        raise
    return existed

def install(args):
    pkg = Path(args.package).resolve()
    backend = Path(args.backend_dir).resolve()
    ui = Path(args.ui_dir).resolve()
    if backend == ui or backend in ui.parents or ui in backend.parents:
        raise ValueError("Backend/UI destinations must be separate")
    if backend.is_symlink() or ui.is_symlink():
        raise ValueError("Symlink destinations forbidden")
    # Stage on the same filesystem as each destination, so rename is atomic.
    manifest = verify(pkg)
    if args.dry_run:
        print(json.dumps({"verified": True, "version": manifest["version"],
                          "backend_dir": str(backend), "ui_dir": str(ui),
                          "firmware": "not flashed"}, indent=2))
        return
    if os.geteuid() != 0:
        raise PermissionError("Installation requires root")
    if not args.allow_unsigned:
        raise ValueError("Package is unsigned. Explicit --allow-unsigned required for testing.")
    if backend.exists() and not backend.is_dir():
        raise ValueError("Backend target is not a directory")
    if ui.exists() and not ui.is_dir():
        raise ValueError("UI target is not a directory")
    backend.parent.mkdir(parents=True, exist_ok=True)
    ui.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".wb-update-", dir=backend.parent) as tmp_b, \
         tempfile.TemporaryDirectory(prefix=".wb-update-", dir=ui.parent) as tmp_u:
        stage_b, stage_u = Path(tmp_b), Path(tmp_u)
        # Extract all content before touching running services.
        with tempfile.TemporaryDirectory() as staging:
            prepare(pkg, Path(staging))
            if backend.exists():
                shutil.copytree(backend, stage_b / "new", symlinks=True)
                shutil.copy2(Path(staging) / "backend" / "wb.srv.jar", stage_b / "new" / "wb.srv.jar")
            else:
                shutil.copytree(Path(staging) / "backend", stage_b / "new")
            shutil.copytree(Path(staging) / "ui", stage_u / "new")
        marker = time.strftime("%Y%m%d-%H%M%S")
        backup_b = backend.with_name(backend.name + ".backup-" + marker)
        backup_u = ui.with_name(ui.name + ".backup-" + marker)
        for p in (backup_b, backup_u):
            if p.exists():
                raise FileExistsError(str(p))
        services = args.service
        swapped = []
        try:
            for service in services:
                run(["systemctl", "stop", service])
            for src, target, backup in ((stage_b / "new", backend, backup_b),
                                        (stage_u / "new", ui, backup_u)):
                existed = swap(src, target, backup)
                swapped.append((target, backup, existed))
            for service in services:
                run(["systemctl", "start", service])
            for service in services:
                run(["systemctl", "is-active", "--quiet", service])
        except BaseException:
            for service in services:
                try: run(["systemctl", "stop", service])
                except Exception: pass
            for target, backup, existed in reversed(swapped):
                if target.exists():
                    if target.is_dir(): shutil.rmtree(target)
                    else: target.unlink()
                if existed:
                    backup.rename(target)
            for service in services:
                try: run(["systemctl", "start", service])
                except Exception: pass
            raise
        print(json.dumps({"installed": manifest["version"], "backups": [str(b) for _, b, e in swapped if e],
                          "firmware": "unchanged"}, indent=2))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("package")
    p.add_argument("--backend-dir", default="/opt/wb.srv")
    p.add_argument("--ui-dir", default="/opt/wb.ui")
    p.add_argument("--service", action="append", default=None,
                   help="Systemd service to restart; repeat as needed")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--allow-unsigned", action="store_true",
                   help="TEST ONLY: accept SHA-256 integrity without trusted signature")
    a = p.parse_args()
    if a.service is None:
        a.service = ["wb-srv.service", "wb-ui.service"]
    install(a)

if __name__ == "__main__":
    main()
