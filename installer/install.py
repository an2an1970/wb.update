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
import urllib.request

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
        raise ValueError("Missing existing lib directory")
    dropin_dir = Path("/etc/systemd/system/wb-srv.service.d")
    dropin = dropin_dir / "90-wb-update.conf"
    if dropin.exists() or dropin.is_symlink():
        raise FileExistsError("Refusing to overwrite existing override: " + str(dropin))
    manifest = verify(args.package)
    if args.dry_run:
        print(json.dumps({"verified": True, "version": manifest["version"], "root": str(root),
                          "jar": str(jar), "ui": str(ui), "firmware": "unchanged"}, indent=2))
        return
    if os.geteuid() != 0:
        raise PermissionError("Installation requires root")
    if not args.allow_unsigned:
        raise ValueError("Unsigned package: --allow-unsigned required for test deployments")
    if root != ROOT:
        raise ValueError("This installer only supports /opt/wb.srv; systemd override uses that path")
    # All temporary and backup files reside on the same filesystem as live files.
    with tempfile.TemporaryDirectory(prefix=".wb-update-", dir=root) as tmp:
        stage = Path(tmp)
        manifest = prepare(args.package, stage)
        with zipfile.ZipFile(stage / "wb.srv.jar") as new_jar:
            if "BOOT-INF/classes/net/an2an/wb/server/Application.class" not in new_jar.namelist():
                raise ValueError("Expected executable Spring Boot JAR with Application.class")
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
        override_installed = False
        try:
            dropin_dir.mkdir(parents=True, exist_ok=True)
            # systemd requires clearing the original ExecStart before replacing it.
            override_text = "[Service]\nExecStart=\nExecStart=/usr/bin/java -jar /opt/wb.srv/wb.srv.jar\n"
            run("systemctl", "stop", "wb-srv.service")
            dropin.write_text(override_text)
            override_installed = True
            run("systemctl", "daemon-reload")
            # wb-ui.service requires wb-srv and may be stopped by systemd; restart if needed.
            for live, staged in ((jar, stage / "wb.srv.jar"), (ui, stage / "static")):
                previous = backup / live.name
                live.rename(previous)
                swapped.append((live, previous))
                staged.rename(live)
            run("systemctl", "start", "wb-srv.service")
            # Wait for Spring Boot readiness, not merely process startup.
            ready = False
            for _ in range(60):
                try:
                    with urllib.request.urlopen("http://127.0.0.1:7777/favicon.ico", timeout=2) as response:
                        if response.status == 200:
                            ready = True
                            break
                except Exception:
                    pass
                time.sleep(1)
            if not ready:
                raise RuntimeError("Backend did not become HTTP-ready within 60 seconds")
            run("systemctl", "is-active", "--quiet", "wb-srv.service")
            # The kiosk service may have been stopped through Requires=.
            run("systemctl", "start", "wb-ui.service")
            run("systemctl", "is-active", "--quiet", "wb-ui.service")
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
            if override_installed:
                dropin.unlink(missing_ok=True)
                try: run("systemctl", "daemon-reload")
                except Exception: pass
            try: run("systemctl", "start", "wb-srv.service")
            except Exception: pass
            try: run("systemctl", "start", "wb-ui.service")
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
