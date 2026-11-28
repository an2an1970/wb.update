#!/bin/bash
set -Eeuo pipefail
exec >>/var/log/wb-firstboot.log 2>&1
echo "WB firstboot started: $(date -Is)"
BOOT=/boot/firmware
CMD="$BOOT/cmdline.txt"
PKG="$BOOT/wb-install/package.wbpkg"
UART="$BOOT/wb-install/setup-qr-uart.sh"
[[ -f "$PKG" && -f "$UART" ]] || { echo "Missing WB package or UART setup"; exit 1; }
# Configure appliance locale and keyboard without interactive Raspberry Pi wizard.
hostnamectl set-hostname wb22
timedatectl set-timezone Europe/Jerusalem
if [[ -f /etc/default/keyboard ]]; then
  sed -i -E 's/^XKBLAYOUT=.*/XKBLAYOUT="us"/; s/^XKBVARIANT=.*/XKBVARIANT=""/' /etc/default/keyboard
fi
if [[ -f /etc/locale.gen ]]; then
  sed -i -E 's/^# ?(en_US.UTF-8 UTF-8)/\\1/' /etc/locale.gen
  locale-gen en_US.UTF-8
  update-locale LANG=en_US.UTF-8
fi
# Install SD-provided SSH public keys for the wb login user.
KEYS="$BOOT/wb-install/authorized_keys"
if [[ -s "$KEYS" ]]; then
  id wb >/dev/null 2>&1 || { echo "wb user not created by Raspberry Pi OS firstboot"; exit 1; }
  install -d -m 0700 -o wb -g wb /home/wb/.ssh
  install -m 0600 -o wb -g wb "$KEYS" /home/wb/.ssh/authorized_keys
  systemctl enable --now ssh.service
fi
python3 "$PKG" verify
python3 "$PKG" install --allow-unsigned
# The package stages its resume service and schedules a reboot.
# Cancel that delayed reboot while configuring the boot overlay.
shutdown -c || true
bash "$UART"
python3 - "$CMD" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
tokens=[t for t in s.split() if not (t.startswith("systemd.run=") or t.startswith("systemd.run_success_action=") or t.startswith("systemd.run_failure_action="))]
p.write_text(" ".join(tokens)+"\n")
PY
echo "WB firstboot complete: $(date -Is)"
shutdown -r +1 "WB bootstrap continuation"
