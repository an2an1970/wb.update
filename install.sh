#!/usr/bin/env bash
# WB first-install or update from one .wbpkg
set -euo pipefail
REPO="an2an1970/wb.update"
VERSION="${1:-latest}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
if [[ "$VERSION" == "latest" ]]; then
  VERSION="$(curl -fsSL --retry 3 "https://api.github.com/repos/${REPO}/releases/latest" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')"
else
  VERSION="v${VERSION#v}"
fi
PACKAGE="wb-${VERSION#v}.wbpkg"
curl -fL --retry 3 "https://github.com/${REPO}/releases/download/${VERSION}/${PACKAGE}" -o "$WORK/$PACKAGE"
python3 "$WORK/$PACKAGE" verify
sudo python3 "$WORK/$PACKAGE" install --dry-run
sudo python3 "$WORK/$PACKAGE" install --allow-unsigned
