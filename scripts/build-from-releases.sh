#!/usr/bin/env bash
set -euo pipefail
export WB_RELEASE_TOKEN="${WB_RELEASE_TOKEN:-${GH_TOKEN:-}}"
: "${WB_RELEASE_TOKEN:?Set WB_RELEASE_TOKEN or GH_TOKEN}"
python3 scripts/fetch_releases.py
readarray -t SHAS < <(python3 - <<'PY'
import json
d = json.load(open("downloaded/selected-releases.json"))
for repo in ("wb.srv", "wb.ui", "wb.arduino"):
    print(d[repo]["sha"])
PY
)
python3 packager/build.py build \
  --version "${VERSION:-0.1.0}" \
  --backend downloaded/backend.jar --ui downloaded/ui.zip \
  --firmware downloaded/app.bin --bootloader downloaded/bootloader.bin \
  --partitions downloaded/partitions.bin \
  --backend-sha "${SHAS[0]}" --ui-sha "${SHAS[1]}" --firmware-sha "${SHAS[2]}" \
  --output "wb-${VERSION:-0.1.0}.wbpkg"
