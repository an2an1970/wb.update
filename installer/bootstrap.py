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
After=wb-srv.service graphical.target
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
            "lightdm", "curl", "network-manager", "policykit-1", "python3-dbus",
            "sudo", "python3-serial", "python3-pip", "device-tree-compiler")
    if not shutil.which("pinctrl"):
        command("apt-get", "install", "-y", "raspi-utils")
    if not shutil.which("esptool") and not shutil.which("esptool.py"):
        command("apt-get", "install", "-y", "esptool")
    if not shutil.which("esptool") and shutil.which("esptool.py"):
        Path("/usr/local/bin/esptool").write_text('#!/bin/sh\nexec /usr/bin/esptool.py "$@"\n')
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
    os.chown(ROOT, user.pw_uid, user.pw_gid)
    write_if_absent("/etc/systemd/system/wb-srv.service", SERVER_UNIT)
    write_if_absent("/etc/systemd/system/wb-ui.service", UI_UNIT)
    write_if_absent(ROOT / "boot.sh", BOOT, 0o755)
    write_if_absent(ROOT / "reset.sh", RESET, 0o755)
    write_if_absent("/etc/lightdm/lightdm.conf.d/12-wb-autologin.conf",
                    "[Seat:*]\nautologin-user=wbui\nautologin-user-timeout=0\nuser-session=LXDE-pi\n")
    if shutil.which("raspi-config"):
        command("raspi-config", "nonint", "do_serial_cons", "1")
        command("raspi-config", "nonint", "do_serial_hw", "0")
    command("systemctl", "set-default", "graphical.target")
    command("systemctl", "daemon-reload")
    command("systemctl", "enable", "wb-srv.service", "wb-ui.service")
