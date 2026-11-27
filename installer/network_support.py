"""System setup for Network API. Never changes NetworkManager connections or hostname."""
import json
import os
import pwd
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

TRANSACTIONS = Path("/srv/wb.srv/network-transactions")
HELPER = Path("/usr/lib/wb-srv/network-recover.py")
CONFIG = Path("/etc/wb.srv/wb-srv.properties")
UNITS = Path("/etc/systemd/system")
POLKIT = Path("/etc/polkit-1/rules.d/10-wb-network.rules")
TEST_UUID = "00000000-0000-0000-0000-000000000000"

TEMPLATE = """[Unit]
Description=WB Network recovery for transaction %i
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
TimeoutStartSec=infinity
ExecStart=/usr/bin/python3 /usr/lib/wb-srv/network-recover.py --wait /srv/wb.srv/network-transactions/%i.json
"""
BOOT = """[Unit]
Description=WB Network recovery scan after boot
After=NetworkManager.service
Before=wb-srv.service
Wants=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/lib/wb-srv/network-recover.py --recover-all /srv/wb.srv/network-transactions

[Install]
WantedBy=multi-user.target
"""
ORDER = """[Unit]
Requires=wb-network-recovery-boot.service
After=wb-network-recovery-boot.service
"""
RULE = r"""polkit.addRule(function(action, subject) {
    if (subject.user !== "wbui") return polkit.Result.NOT_HANDLED;
    var allowed = [
        "org.freedesktop.NetworkManager.network-control",
        "org.freedesktop.NetworkManager.settings.modify.system",
        "org.freedesktop.NetworkManager.checkpoint-rollback",
        "org.freedesktop.NetworkManager.wifi.scan",
        "org.freedesktop.hostname1.set-hostname",
        "org.freedesktop.hostname1.set-static-hostname"
    ];
    if (allowed.indexOf(action.id) !== -1) return polkit.Result.YES;
    if (action.id === "org.freedesktop.systemd1.manage-units") {
        var unit = action.lookup("unit"), verb = action.lookup("verb");
        if (typeof unit === "string" &&
            /^wb-network-recovery@[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\.service$/.test(unit) &&
            (verb === "start" || verb === "stop")) return polkit.Result.YES;
    }
    return polkit.Result.NOT_HANDLED;
});
"""

def check_dependencies():
    commands = ("nmcli", "systemctl", "hostnamectl", "python3", "pkaction", "pkcheck", "sudo")
    missing = [cmd for cmd in commands if shutil.which(cmd) is None]
    if missing:
        raise RuntimeError("Network API missing dependencies: " + ", ".join(missing))
    if subprocess.run(["/usr/bin/python3", "-c", "import dbus"], capture_output=True).returncode:
        raise RuntimeError("Network API requires python3-dbus (apt install python3-dbus)")
    if not Path("/etc/polkit-1/rules.d").is_dir():
        raise RuntimeError("Network API requires polkit rules directory")
    pwd.getpwnam("wbui")

def install_file(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError("Unsafe installation target: " + str(path))
    data = data.encode("utf-8") if isinstance(data, str) else data
    if path.exists() and path.read_bytes() == data:
        os.chown(path, 0, 0)
        path.chmod(mode)
        return
    fd, temporary = tempfile.mkstemp(prefix=".wb-network-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chown(temporary, 0, 0)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def update_properties(path=CONFIG):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError("Unsafe local config: " + str(path))
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    values = {
        "wb-srv.network.mutable": "true",
        "wb-srv.network.transaction-dir": str(TRANSACTIONS),
        "wb-srv.network.recovery-helper": str(HELPER),
    }
    lines = original.splitlines(keepends=True)
    for key, value in values.items():
        indices = [i for i, line in enumerate(lines)
                   if re.match(r"^\s*" + re.escape(key) + r"\s*[=:]", line)]
        if indices:
            lines[indices[-1]] = key + "=" + value + "\n"
            for i in reversed(indices[:-1]):
                del lines[i]
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(key + "=" + value + "\n")
    updated = "".join(lines)
    if updated == original:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".wb-network-config-", dir=path.parent)
    try:
        if path.exists():
            st = path.stat()
            os.fchmod(fd, st.st_mode & 0o777)
            os.fchown(fd, st.st_uid, st.st_gid)
        else:
            os.fchmod(fd, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(updated)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def install(helper):
    check_dependencies()
    account = pwd.getpwnam("wbui")
    if TRANSACTIONS.is_symlink() or (TRANSACTIONS.exists() and not TRANSACTIONS.is_dir()):
        raise RuntimeError("Unsafe transaction directory")
    TRANSACTIONS.mkdir(parents=True, exist_ok=True)
    os.chown(TRANSACTIONS, account.pw_uid, account.pw_gid)
    TRANSACTIONS.chmod(0o700)
    install_file(HELPER, helper, 0o755)
    install_file(UNITS / "wb-network-recovery@.service", TEMPLATE)
    install_file(UNITS / "wb-network-recovery-boot.service", BOOT)
    install_file(UNITS / "wb-srv.service.d/20-wb-network-recovery.conf", ORDER)
    install_file(POLKIT, RULE)
    update_properties()
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "wb-network-recovery-boot.service"], check=True)

def verify_permissions():
    result = subprocess.run(["sudo", "-n", "-u", "wbui", "nmcli", "general", "permissions"],
                            capture_output=True, text=True, check=True)
    permissions = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            permissions[fields[0]] = fields[1].lower()
    for suffix in ("checkpoint-rollback", "network-control", "settings.modify.system", "wifi.scan"):
        action = "org.freedesktop.NetworkManager." + suffix
        if permissions.get(action) != "yes":
            raise RuntimeError("Missing NetworkManager permission for wbui: " + action)
    subprocess.run(["sudo", "-n", "-u", "wbui", "systemctl", "--no-ask-password",
                    "start", "wb-network-recovery@" + TEST_UUID + ".service"],
                   check=True, timeout=30)

def verify_capabilities():
    # Existing authentication must remain unchanged; no credentials are invented.
    with urllib.request.urlopen("http://127.0.0.1:7777/api/network/capabilities", timeout=10) as response:
        result = json.load(response)
    if result.get("configurationWritable") is not True:
        raise RuntimeError("Network API configurationWritable is not true")
