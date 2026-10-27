#!/usr/bin/env bash
set -euo pipefail
export WB_RELEASE_TOKEN="${WB_RELEASE_TOKEN:-${GH_TOKEN:-}}"
: "${WB_RELEASE_TOKEN:?Set WB_RELEASE_TOKEN or GH_TOKEN}"
python3 scripts/fetch_releases.py \
  --backend-tag "4.0--g822c2d0-pi" \
  --backend-sha "822c2d0d8d783f0b576843632cd45737958f8305" \
  --ui-tag "0.3-190" \
  --ui-sha "7f09a972bf64bf29991d9b4c61f084f9b6f45918" \
  --firmware-tag "build-S3SIM_v1.9.158_dcaba88" \
  --firmware-sha "dcaba88158e4d72a6d42858e5a200212e02a8336"
python3 packager/build.py build \
  --version "${VERSION:-0.1.0}" \
  --backend downloaded/backend.jar --ui downloaded/ui.zip \
  --firmware downloaded/app.bin --bootloader downloaded/bootloader.bin \
  --partitions downloaded/partitions.bin \
  --backend-sha "822c2d0d8d783f0b576843632cd45737958f8305" \
  --ui-sha "7f09a972bf64bf29991d9b4c61f084f9b6f45918" \
  --firmware-sha "dcaba88158e4d72a6d42858e5a200212e02a8336" \
  --output "wb-${VERSION:-0.1.0}.wbpkg"
