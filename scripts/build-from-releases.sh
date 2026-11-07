#!/usr/bin/env bash
set -euo pipefail
export WB_RELEASE_TOKEN="${WB_RELEASE_TOKEN:-${GH_TOKEN:-}}"
: "${WB_RELEASE_TOKEN:?Set WB_RELEASE_TOKEN or GH_TOKEN}"
: "${VERSION:?VERSION must come from release tag}"
python3 scripts/validate-release.py --tag "v${VERSION}"
readarray -t PINS < <(python3 - <<'PY'
import json
d = json.load(open("release-components.json"))["components"]
for repo in ("wb.srv", "wb.ui", "wb.arduino"):
    print(d[repo]["tag"])
    print(d[repo]["sha"])
PY
)
python3 scripts/fetch_releases.py \
  --backend-tag "${PINS[0]}" --backend-sha "${PINS[1]}" \
  --ui-tag "${PINS[2]}" --ui-sha "${PINS[3]}" \
  --firmware-tag "${PINS[4]}" --firmware-sha "${PINS[5]}"
python3 packager/build.py build \
  --version "${VERSION}" \
  --backend downloaded/backend.jar --ui downloaded/ui.zip \
  --firmware downloaded/app.bin --bootloader downloaded/bootloader.bin \
  --partitions downloaded/partitions.bin \
  --backend-sha "${PINS[1]}" --ui-sha "${PINS[3]}" --firmware-sha "${PINS[5]}" \
  --output "wb-${VERSION}.wbpkg"
