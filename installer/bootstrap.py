"""First-install provisioning for Raspberry Pi OS Bookworm with Desktop."""
import os
import pwd
import shutil
import subprocess
from pathlib import Path

ROOT = Path("/opt/wb.srv")
SERVER_UNIT = """[Unit]
Description=WB Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=wbui
Group=wbui
WorkingDirectory=/opt/wb.srv
ExecStart=/usr/bin/java -jar /opt/wb.srv/wb.srv.jar
Restart=always
RestartSec=2
SuccessExitStatus=143

[Install]
WantedBy=multi-user.target
"""
UI_UNIT = """[Unit]
Description=WB UI Browser
After=wb-srv.service lightdm.service
Requires=wb-srv.service
PartOf=wb-srv.service

[Service]
Type=simple
User=wbui
Group=wbui
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/wbui/.Xauthority
ExecStartPre=/bin/sh -c 'until /usr/bin/curl -fsS http://127.0.0.1:7777/favicon.ico >/dev/null; do sleep 1; done'
ExecStart=/usr/bin/chromium-browser --kiosk --incognito --noerrdialogs --disable-infobars --disable-session-crashed-bubble http://127.0.0.1:7777/
Restart=always
RestartSec=2

[Install]
WantedBy=graphical.target
"""
BOOT = """#!/bin/sh
set -eu
pinctrl set 10 op pn dh
pinctrl set 18 op pn dl
pinctrl set 17 op pn dh
sleep 0.05
pinctrl set 17 op pn dl
sleep 0.05
pinctrl set 17 op pn dh
"""
RESET = """#!/bin/sh
set -eu
pinctrl set 10 op pn dh
pinctrl set 18 op pn dh
pinctrl set 17 op pn dl
sleep 0.2
pinctrl set 17 op pn dh
"""

def command(*args):
    subprocess.run(args, check=True)

def preflight():
    if os.geteuid() != 0:
        raise PermissionError("Root required for first install")
    if not Path("/etc/os-release").exists():
        raise RuntimeError("Raspberry Pi OS required")
    data = Path("/etc/os-release").read_text()
    if "bookworm" not in data.lower() or "debian" not in data.lower():
        raise RuntimeError("First install currently supports Raspberry Pi OS Bookworm only")
    if not Path("/proc/device-tree/model").exists() or not Path("/proc/device-tree/model").read_text(errors="replace").startswith("Raspberry Pi 5 "):
        raise RuntimeError("Raspberry Pi hardware required")
    if not Path("/etc/lightdm").exists():
        raise RuntimeError("Raspberry Pi OS with Desktop (lightdm) required")
    if ROOT.is_symlink() or (ROOT.exists() and any(ROOT.iterdir())):
        raise RuntimeError("Refusing bootstrap over nonempty deployment; use update mode")

def ensure_packages():
    command("apt-get", "update")
    command("apt-get", "install", "-y", "openjdk-17-jre-headless", "chromium-browser",
            "lightdm", "openbox", "curl", "network-manager", "policykit-1", "python3-dbus",
            "sudo", "python3-serial", "python3-pip", "device-tree-compiler")
    if not shutil.which("pinctrl"):
        command("apt-get", "install", "-y", "raspi-utils")
    if not shutil.which("esptool") and not shutil.which("esptool.py"):
        # Raspberry Pi OS Bookworm does not necessarily ship an apt package named esptool.
        command("apt-get", "install", "-y", "python3-venv")
        command("python3", "-m", "venv", "/opt/wb-esptool-venv")
        command("/opt/wb-esptool-venv/bin/pip", "install", "esptool")
        Path("/usr/local/bin/esptool").symlink_to("/opt/wb-esptool-venv/bin/esptool")
    if not shutil.which("esptool") and shutil.which("esptool.py"):
        Path("/usr/local/bin/esptool").write_text('#!/bin/sh\\nexec /usr/bin/esptool.py "$@"\\n')
        Path("/usr/local/bin/esptool").chmod(0o755)
    for executable in ("java", "chromium-browser", "pinctrl", "esptool", "nmcli"):
        if not shutil.which(executable):
            raise RuntimeError("Missing bootstrap executable: " + executable)

def write_if_absent(path, contents, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError("Refusing symlink: " + str(path))
    if path.exists():
        if not path.is_file() or path.read_text() != contents:
            raise RuntimeError("Existing conflicting bootstrap file: " + str(path))
        return
    path.write_text(contents)
    path.chmod(mode)

def provision():
    preflight()
    ensure_packages()
    if not shutil.which("useradd"):
        raise RuntimeError("useradd missing")
    if not shutil.which("getent"):
        raise RuntimeError("getent missing")
    if subprocess.run(["getent", "group", "wbui"], stdout=subprocess.DEVNULL).returncode:
        command("groupadd", "--system", "wbui")
    try:
        pwd.getpwnam("wbui")
    except KeyError:
        groups = [g for g in ("audio", "video", "input", "render", "dialout") if subprocess.run(
            ["getent", "group", g], stdout=subprocess.DEVNULL).returncode == 0]
        args = ["useradd", "--system", "--create-home", "--home-dir", "/home/wbui", "--gid", "wbui", "--shell", "/bin/bash"]
        if groups:
            args += ["--groups", ",".join(groups)]
        command(*(args + ["wbui"]))
    ROOT.mkdir(parents=True, exist_ok=True)
    user = pwd.getpwnam("wbui")
    logs = Path("/var/log/wb.srv")
    logs.mkdir(parents=True, exist_ok=True)
    os.chown(logs, user.pw_uid, user.pw_gid)
    # Site-specific REST credentials must be supplied, never hardcoded in the package.
    site_config = Path("/boot/firmware/wb-install/application-local.properties")
    config = ROOT / "application-local.properties"
    if site_config.is_file() and not site_config.is_symlink():
        write_if_absent(config, site_config.read_text(), 0o640)
    else:
        write_if_absent(config,
                        "spring.profiles.active=console,hybrid,uart,pi,rpc,bluetooth,pi4j-hw-control\n")
    os.chown(config, user.pw_uid, user.pw_gid)
    config.chmod(0o640)
    settings = dict(line.split("=", 1) for line in config.read_text().splitlines()
                    if "=" in line and not line.lstrip().startswith("#"))
    missing = [key for key in ("rest.server", "rest.user", "rest.passwd")
               if not settings.get(key, "").strip()]
    if missing:
        raise RuntimeError("Missing required WB settings: " + ", ".join(missing) +
                           "; supply /boot/firmware/wb-install/application-local.properties")
    # Fixed appliance hostname for this deployment.
    command("hostnamectl", "set-hostname", "wb22")
    hosts = Path("/etc/hosts")
    hosts_text = hosts.read_text()
    import re
    hosts_lines = hosts_text.splitlines(keepends=True)
    for i, line in enumerate(hosts_lines):
        if re.match(r"^127\.0\.1\.1\s", line):
            hosts_lines[i] = "127.0.1.1\twb22\n"
            break
    else:
        hosts_lines.append("127.0.1.1\twb22\n")
    hosts.write_text("".join(hosts_lines))
    # The main LightDM configuration may override the kiosk drop-in.
    main_lightdm = Path("/etc/lightdm/lightdm.conf")
    if main_lightdm.is_file():
        original = main_lightdm.read_text()
        updated = re.sub(r"(?m)^(autologin-user\s*=)[^\n]*", r"\g<1>wbui", original)
        updated = re.sub(r"(?m)^(user-session\s*=)[^\n]*", r"\g<1>wb-kiosk", updated)
        if updated != original:
            main_lightdm.write_text(updated)
    # Hardware calibration is device-specific. Import the supplied settings
    # from the SD card on first install; never replace it during updates.
    settings_source = Path("/boot/firmware/wb-install/settings.json")
    settings_dest = ROOT / "settings.json"
    if not settings_source.is_file() or settings_source.is_symlink():
        raise RuntimeError("Missing /boot/firmware/wb-install/settings.json; "
                           "provide the device's hardware settings before first boot")
    import json
    settings = json.loads(settings_source.read_text())
    if not isinstance(settings, dict) or not isinstance(settings.get("hardware"), dict):
        raise ValueError("Invalid WB settings.json: missing hardware object")
    write_if_absent(settings_dest, settings_source.read_text(), 0o640)
    os.chown(settings_dest, user.pw_uid, user.pw_gid)
    settings_dest.chmod(0o640)
    script_source = Path("/boot/firmware/wb-install/script.js")
    script_dest = ROOT / "script.js"
    if not script_source.is_file() or script_source.is_symlink():
        raise RuntimeError("Missing /boot/firmware/wb-install/script.js; "
                           "provide the device's experiment script before first boot")
    write_if_absent(script_dest, script_source.read_text(), 0o640)
    os.chown(script_dest, user.pw_uid, user.pw_gid)
    script_dest.chmod(0o640)
    os.chown(ROOT, user.pw_uid, user.pw_gid)
    write_if_absent("/etc/systemd/system/wb-srv.service", SERVER_UNIT)
    write_if_absent("/etc/systemd/system/wb-ui.service", UI_UNIT)
    write_if_absent(ROOT / "boot.sh", BOOT, 0o755)
    write_if_absent(ROOT / "reset.sh", RESET, 0o755)
    write_if_absent("/usr/share/xsessions/wb-kiosk.desktop",
                    "[Desktop Entry]\nName=WB Kiosk\nComment=WB fullscreen interface\nExec=/usr/bin/openbox-session\nType=Application\n")
    write_if_absent("/etc/lightdm/lightdm.conf.d/12-wb-autologin.conf",
                    "[Seat:*]\nautologin-user=wbui\nautologin-user-timeout=0\nuser-session=wb-kiosk\n")
    if shutil.which("raspi-config"):
        command("raspi-config", "nonint", "do_serial_cons", "1")
        command("raspi-config", "nonint", "do_serial_hw", "0")
    command("systemctl", "set-default", "graphical.target")
    command("systemctl", "daemon-reload")
    command("systemctl", "enable", "wb-srv.service", "wb-ui.service")
