#!/bin/bash
set -Eeuo pipefail
exec >>/var/log/wb-firstboot.log 2>&1
echo "WB firstboot started: $(date -Is)"
BOOT=/boot/firmware
CMD="$BOOT/cmdline.txt"
PKG="$BOOT/wb-install/package.wbpkg"
[[ -f "$PKG" ]] || { echo "Missing $PKG"; exit 1; }
# Do not clear the trigger before verification/provisioning succeeds.
python3 "$PKG" verify
python3 "$PKG" install --allow-unsigned
# First phase of the package installs a resume service and schedules a reboot.
# Remove the kernel trigger only after successful handoff.
python3 - "$CMD" <<'PY'
from pathlib import Path
import re, sys
p=Path(sys.argv[1])
s=p.read_text()
s=re.sub(r'\s+systemd\.run=(?:"[^"]*"|\S+)', '', s)
s=re.sub(r'\s+systemd\.run_(?:success|failure)_action=\S+', '', s)
p.write_text(s.strip()+'\n')
PY
echo "WB firstboot handoff complete: $(date -Is)"
