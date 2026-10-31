#!/usr/bin/env python3
"""Install WB backend, UI, and flash ESP32-S3 on WB v4 hardware."""
import argparse
import hashlib
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
from experiment_storage import configure as configure_experiment_storage

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

def stage_script(jar_path, stage, live_script):
    """Extract the shipped script; only replace it when bytes differ."""
    if live_script.is_symlink() or (live_script.exists() and not live_script.is_file()):
        raise ValueError("Unsafe existing script.js: " + str(live_script))
    with zipfile.ZipFile(jar_path) as archive:
        script_name = "BOOT-INF/classes/script.js"
        if script_name not in archive.namelist():
            raise ValueError("Server JAR is missing " + script_name)
        content = archive.read(script_name)
    if not content.strip():
        raise ValueError("Bundled script.js is empty")
    if live_script.exists() and hashlib.sha256(live_script.read_bytes()).digest() == hashlib.sha256(content).digest():
        return False
    staged = stage / "script.js"
    staged.write_bytes(content)
    if live_script.exists():
        shutil.copystat(live_script, staged)
        os.chown(staged, live_script.stat().st_uid, live_script.stat().st_gid)
    else:
        source_stat = (stage / "wb.srv.jar").stat()
        os.chown(staged, source_stat.st_uid, source_stat.st_gid)
        staged.chmod(0o644)
    return True

def flash_firmware(package, stage, root):
    """Always flash all ESP32-S3 images. Requires verified local WB hardware scripts."""
    boot = root / "boot.sh"
    reset = root / "reset.sh"
    boot_app = root / "boot_app0.bin"
    for file in (boot, reset, boot_app):
        if not file.is_file() or file.is_symlink():
            raise FileNotFoundError(f"Required firmware support file: {file}")
    if not shutil.which("esptool") or not shutil.which("pinctrl"):
        raise RuntimeError("esptool and pinctrl are required to flash ESP32-S3")
    firmware_dir = stage / "firmware"
    firmware_dir.mkdir()
    with zipfile.ZipFile(package) as pkg:
        for src, name in (("firmware/v4/app.bin", "app.bin"),
                          ("firmware/v4/bootloader.bin", "bootloader.bin"),
                          ("firmware/v4/partitions.bin", "partitions.bin")):
            (firmware_dir / name).write_bytes(pkg.read(src))
    run("bash", str(boot))
    try:
        run("esptool", "--chip", "esp32s3", "--port", "/dev/serial0",
            "--baud", "921600", "--before", "default-reset",
            "--after", "hard-reset", "write-flash", "-z",
            "--flash-mode", "dio", "--flash-freq", "80m",
            "--flash-size", "8MB",
            "0x0", str(firmware_dir / "bootloader.bin"),
            "0x8000", str(firmware_dir / "partitions.bin"),
            "0xe000", str(boot_app),
            "0x10000", str(firmware_dir / "app.bin"))
    finally:
        run("bash", str(reset))

def configure_qr_uart(dry_run=False):
    script = Path(__file__).resolve().with_name("setup-qr-uart.sh")
    if not script.is_file() or script.is_symlink():
        raise FileNotFoundError(f"Missing QR UART setup script: {script}")
    cmd = ["bash", str(script)]
    if dry_run:
        cmd.append("--dry-run")
    run(*cmd)

def install(args):
    root = Path(args.root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Invalid deployment root")
    jar, ui, script = root / "wb.srv.jar", root / "static", root / "script.js"
    if jar.is_symlink() or ui.is_symlink() or not jar.is_file() or not ui.is_dir():
        raise ValueError("Expected regular JAR and static directory")
    if not (root / "lib").is_dir():
        raise ValueError("Missing existing lib directory")
    dropin_dir = Path("/etc/systemd/system/wb-srv.service.d")
    dropin = dropin_dir / "90-wb-update.conf"
    debug_dropin = dropin_dir / "99-wb-debug.conf"
    existing_override = dropin.read_text() if dropin.is_file() and not dropin.is_symlink() else None
    expected_override = "[Service]\nExecStart=\nExecStart=/usr/bin/java -jar /opt/wb.srv/wb.srv.jar\n"
    if dropin.is_symlink() or (existing_override is not None and existing_override != expected_override):
        raise FileExistsError("Unknown systemd override: " + str(dropin))
    expected_debug = ("[Service]\nExecStart=\n"
                      "ExecStart=/usr/bin/java -cp /opt/wb.srv/wb.srv.jar:/opt/wb.srv/lib/* net.an2an.wb.server.Application\n")
    if debug_dropin.is_symlink() or (debug_dropin.exists() and
       (not debug_dropin.is_file() or debug_dropin.read_text() != expected_debug)):
        raise FileExistsError("Unknown debug systemd override: " + str(debug_dropin))
    manifest = verify(args.package)
    if args.dry_run:
        configure_qr_uart(dry_run=True)
        configure_experiment_storage(root, dry_run=True)
        print(json.dumps({"verified": True, "version": manifest["version"], "root": str(root),
                          "jar": str(jar), "ui": str(ui), "firmware": "will flash on installation"}, indent=2))
        return
    if os.geteuid() != 0:
        raise PermissionError("Installation requires root")
    if not args.allow_unsigned:
        raise ValueError("Unsigned package: --allow-unsigned required for test deployments")
    if root != ROOT:
        raise ValueError("This installer only supports /opt/wb.srv; systemd override uses that path")
    configure_qr_uart(dry_run=True)
    configure_experiment_storage(root, dry_run=True)
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
        script_changed = stage_script(stage / "wb.srv.jar", stage, script)
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
        debug_removed = False
        try:
            dropin_dir.mkdir(parents=True, exist_ok=True)
            # systemd requires clearing the original ExecStart before replacing it.
            override_text = expected_override
            run("systemctl", "stop", "wb-srv.service")
            flash_firmware(args.package, stage, root)
            configure_qr_uart()
            configure_experiment_storage(root)
            if existing_override is None:
                dropin.write_text(override_text)
                override_installed = True
            # Release mode must take precedence over a previous local debug deployment.
            if debug_dropin.is_file():
                debug_dropin.unlink()
                debug_removed = True
            run("systemctl", "daemon-reload")
            # wb-ui.service requires wb-srv and may be stopped by systemd; restart if needed.
            replacements = [(jar, stage / "wb.srv.jar"), (ui, stage / "static")]
            if script_changed:
                replacements.append((script, stage / "script.js"))
            for live, staged in replacements:
                previous = backup / live.name
                had_previous = live.exists()
                if had_previous:
                    live.rename(previous)
                swapped.append((live, previous if had_previous else None))
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
                              "firmware": "flashed", "script_changed": script_changed,
                              "qr_uart": "configured", "reboot": "scheduled in 1 minute"}, indent=2))
            # Delay reboot so SSH/bootstrap can receive the success response.
            run("shutdown", "-r", "+1", "WB update completed")
        except BaseException:
            try: run("systemctl", "stop", "wb-srv.service")
            except Exception: pass
            for live, previous in reversed(swapped):
                if live.exists():
                    if live.is_dir(): shutil.rmtree(live)
                    else: live.unlink()
                if previous is not None:
                    previous.rename(live)
            if override_installed:
                dropin.unlink(missing_ok=True)
            if debug_removed:
                debug_dropin.write_text(expected_debug)
            if override_installed or debug_removed:
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
