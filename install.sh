#!/usr/bin/env bash
# WB one-command installer. Unsigned test packages only.
set -euo pipefail
REPO="an2an1970/wb.update"
BASE="https://raw.githubusercontent.com/${REPO}/main"
API="https://api.github.com/repos/${REPO}/releases/latest"
VERSION="${1:-latest}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/packager"
curl -fsSL --retry 3 "$BASE/installer/install.py" -o "$WORK/install.py"
curl -fsSL --retry 3 "$BASE/installer/setup-qr-uart.sh" -o "$WORK/setup-qr-uart.sh"
curl -fsSL --retry 3 "$BASE/packager/build.py" -o "$WORK/packager/build.py"
if [[ "$VERSION" == "latest" ]]; then
  VERSION="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])' < <(curl -fsSL --retry 3 "$API"))"
else
  VERSION="v${VERSION#v}"
fi
PACKAGE="wb-${VERSION#v}.wbpkg"
URL="https://github.com/${REPO}/releases/download/${VERSION}/${PACKAGE}"
echo "Downloading ${PACKAGE}..."
curl -fL --retry 3 "$URL" -o "$WORK/$PACKAGE"
echo "Verifying package and local deployment..."
PYTHONPATH="$WORK/packager" python3 "$WORK/install.py" "$WORK/$PACKAGE" --dry-run
echo "Installing ${VERSION} (unsigned test package)..."
sudo env PYTHONPATH="$WORK/packager" python3 "$WORK/install.py" "$WORK/$PACKAGE" --allow-unsigned
systemctl is-active wb-srv wb-ui
curl -fsS --max-time 10 -o /dev/null http://127.0.0.1:7777/
echo "WB ${VERSION} installed successfully."
