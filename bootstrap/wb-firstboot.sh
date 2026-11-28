#!/bin/bash
set -Eeuo pipefail
exec >>/var/log/wb-firstboot.log 2>&1
echo "WB firstboot started: $(date -Is)"
BOOT=/boot/firmware
CMD="$BOOT/cmdline.txt"
PKG="$BOOT/wb-install/package.wbpkg"
UART="$BOOT/wb-install/setup-qr-uart.sh"
[[ -f "$PKG" && -f "$UART" ]] || { echo "Missing WB package or UART setup"; exit 1; }
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
