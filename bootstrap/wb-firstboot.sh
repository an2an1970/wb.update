#!/bin/bash
set -Eeuo pipefail
exec >>/var/log/wb-firstboot.log 2>&1
echo "WB firstboot started: $(date -Is)"
BOOT=/boot/firmware
CMD="$BOOT/cmdline.txt"
PKG="$BOOT/wb-install/package.wbpkg"
if [[ ! -f "$PKG" ]]; then
  echo "Missing $PKG"; exit 1
fi
# Remove only our systemd.run trigger, preserving OS kernel arguments.
python3 - "$CMD" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
tokens=s.split()
tokens=[t for t in tokens if not t.startswith("systemd.run=") and not t.startswith("systemd.run_success_action=") and not t.startswith("systemd.run_failure_action=")]
p.write_text(" ".join(tokens)+"\n")
PY
python3 "$PKG" verify
python3 "$PKG" install --allow-unsigned
echo "WB firstboot handoff complete: $(date -Is)"
