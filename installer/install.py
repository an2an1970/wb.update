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
try:
    from .bootstrap import provision as bootstrap_provision, preflight as bootstrap_preflight
except ImportError:
    from bootstrap import provision as bootstrap_provision, preflight as bootstrap_preflight
try:
    from .network_support import install as install_network, check_dependencies, verify_permissions, verify_capabilities
except ImportError:
    from network_support import install as install_network, check_dependencies, verify_permissions, verify_capabilities
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
    if not EXPECTED.issubset(set(manifest["files"])):
        raise ValueError("Missing deployment payload")
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
    for file in (boot, reset):
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
            *(["0xe000", str(boot_app)] if boot_app.is_file() and not boot_app.is_symlink() else []),
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
    # Linux-only dependencies are loaded when installing, not during package tests.
    try:
        from .experiment_storage import configure as configure_experiment_storage
        from .screen_setup import enable_screen
    except ImportError:
        from experiment_storage import configure as configure_experiment_storage
        from screen_setup import enable_screen
    root = Path(args.root)
    fresh = not (root / "wb.srv.jar").is_file()
    first_bootstrap = fresh and not root.exists()
    if first_bootstrap and args.dry_run:
        bootstrap_preflight()
        manifest = verify(args.package)
        print(json.dumps({"verified": True, "version": manifest["version"], "mode": "fresh-install", "dependencies": "will install", "firmware": "will flash"}, indent=2))
        return
    if first_bootstrap:
        if not args.allow_unsigned:
            raise ValueError("Unsigned package: --allow-unsigned required")
        if root != ROOT:
            raise ValueError("First install supports /opt/wb.srv only")
        verify(args.package)
        bootstrap_provision()
        # UART pin mux and LightDM autologin require a reboot on a clean Pi.
        # Resume the verified, local package automatically after that reboot.
        resume_dir = Path("/var/lib/wb-bootstrap")
        resume_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        resume_dir.chmod(0o700)
        resume_pkg = resume_dir / "package.wbpkg"
        shutil.copyfile(args.package, resume_pkg)
        resume_pkg.chmod(0o600)
        resume_unit = Path("/etc/systemd/system/wb-bootstrap-resume.service")
        resume_unit.write_text("[Unit]\nDescription=Complete WB first installation after UART reboot\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 /var/lib/wb-bootstrap/package.wbpkg install --allow-unsigned\nTimeoutStartSec=infinity\n\n[Install]\nWantedBy=multi-user.target\n")
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", "wb-bootstrap-resume.service")
        run("shutdown", "-r", "+1", "WB first-install bootstrap completed")
        print("WB bootstrap provisioned; reboot scheduled; package installation resumes automatically.")
        return
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Invalid deployment root")
    jar, ui, script = root / "wb.srv.jar", root / "static", root / "script.js"
    if jar.is_symlink() or ui.is_symlink() or (not fresh and (not jar.is_file() or not ui.is_dir())):
        raise ValueError("Expected regular JAR and static directory")
    if not (root / "lib").is_dir():
        if fresh:
            (root / "lib").mkdir()
        else:
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
    check_dependencies()
    with zipfile.ZipFile(args.package) as archive:
        recovery_helper = archive.read("installer/network-recover.py")
    kiosk_script = Path(__file__).resolve().with_name("kiosk-lockdown.sh")
    if not kiosk_script.is_file() or kiosk_script.is_symlink():
        raise FileNotFoundError("Missing bundled kiosk lockdown script: " + str(kiosk_script))
    # All temporary and backup files reside on the same filesystem as live files.
    with tempfile.TemporaryDirectory(prefix=".wb-update-", dir=root) as tmp:
        stage = Path(tmp)
        manifest = prepare(args.package, stage)
        with zipfile.ZipFile(stage / "wb.srv.jar") as new_jar:
            if "BOOT-INF/classes/net/an2an/wb/server/Application.class" not in new_jar.namelist():
                raise ValueError("Expected executable Spring Boot JAR with Application.class")
        # Preserve permissions and ownership of existing deployment files.
        if jar.exists():
            shutil.copymode(jar, stage / "wb.srv.jar")
            shutil.copystat(jar, stage / "wb.srv.jar")
            os.chown(stage / "wb.srv.jar", jar.stat().st_uid, jar.stat().st_gid)
        else:
            account = __import__("pwd").getpwnam("wbui")
            os.chown(stage / "wb.srv.jar", account.pw_uid, account.pw_gid)
        # Device-provided script.js is authoritative on a first installation.
        # Release updates retain the existing script handling policy.
        script_changed = False if fresh and script.is_file() else stage_script(stage / "wb.srv.jar", stage, script)
        if fresh:
            with zipfile.ZipFile(stage / "wb.srv.jar") as archive:
                libs = [n for n in archive.namelist() if n.startswith("BOOT-INF/lib/") and n.endswith(".jar")]
                if not libs:
                    raise RuntimeError("Server JAR contains no BOOT-INF/lib dependencies")
                for name in libs:
                    destination = root / "lib" / Path(name).name
                    destination.write_bytes(archive.read(name))
                    os.chown(destination, account.pw_uid, account.pw_gid)
        if ui.exists():
            shutil.copystat(ui, stage / "static")
        account = __import__("pwd").getpwnam("wbui")
        for base, dirs, files in os.walk(stage / "static"):
            for name in dirs + files:
                p = Path(base) / name
                if not p.is_symlink():
                    os.chown(p, account.pw_uid if fresh else ui.stat().st_uid,
                             account.pw_gid if fresh else ui.stat().st_gid)
        account = __import__("pwd").getpwnam("wbui")
        os.chown(stage / "static", account.pw_uid if fresh else ui.stat().st_uid,
                 account.pw_gid if fresh else ui.stat().st_gid)
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
            install_network(recovery_helper)
            verify_permissions()
            if not fresh:
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
            verify_capabilities()
            # Do not synchronously start wb-ui from the resume oneshot:
            # graphical.target waits for multi-user.target, which waits for
            # this oneshot. Waiting for the kiosk here deadlocks the boot.
            run("systemctl", "enable", "wb-ui.service")
            screen_error = None
            try:
                screen_ok = enable_screen(Path(__file__).resolve().with_name("enable-wb-screen.sh"), args.screen_idle_confirmed)
            except Exception as exc:
                # Screen setup is post-install; do not roll back a healthy server/UI
                # or leave flashed firmware inconsistent because CDP verification failed.
                screen_ok = False
                screen_error = str(exc)
                print("WARNING: WB screen verification failed: " + screen_error, file=sys.stderr)
            # Apply keyboard lockdown after server/UI recovery, before the scheduled reboot.
            # Failure is reported and does not roll back already-flashed firmware.
            kiosk_status = "configured"
            try:
                run("bash", str(kiosk_script))
            except Exception as exc:
                kiosk_status = "failed: " + str(exc)
                print("WARNING: kiosk keyboard lockdown: " + str(exc), file=sys.stderr)
            print(json.dumps({"installed": manifest["version"], "backup": str(backup),
                              "firmware": "flashed", "script_changed": script_changed,
                              "qr_uart": "configured", "kiosk_lockdown": kiosk_status, "screen": ("verified" if screen_ok == "frame" else
                                         "ready (JPEG stream unverified)" if screen_ok == "ready" else
                                         "failed: " + screen_error if screen_error else "deferred until idle"), "reboot": "scheduled in 1 minute"}, indent=2))
            # A completed first install must not run again after the final reboot.
            if fresh and Path("/etc/systemd/system/wb-bootstrap-resume.service").exists():
                run("systemctl", "disable", "wb-bootstrap-resume.service")
                Path("/etc/systemd/system/wb-bootstrap-resume.service").unlink()
                Path("/var/lib/wb-bootstrap/package.wbpkg").unlink(missing_ok=True)
                run("systemctl", "daemon-reload")
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
            # Never wait for the kiosk from the bootstrap resume job.
            raise

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("package")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--screen-idle-confirmed", action="store_true", help="Allow kiosk restart to enable CDP only when instrument is idle")
    p.add_argument("--allow-unsigned", action="store_true", help="TEST ONLY")
    install(p.parse_args())

if __name__ == "__main__":
    main()
