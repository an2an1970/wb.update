#!/usr/bin/env bash
# Raspberry Pi 5: sample QR RX on GPIO5, glass QR RX on GPIO9.
# GPIO4/GPIO8 stay available for QR triggers / multiplexed level inputs.
set -euo pipefail

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
  printf 'Usage: sudo bash %s [--dry-run]\n' "${0##*/}"
  printf 'Installs the WB QR RX-only overlay and its config.txt entry. Does not reboot.\n'
}

dry_run=false
case "${1:-}" in
  '') ;;
  --dry-run) dry_run=true ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { usage >&2; exit 2; }
[[ $EUID -eq 0 ]] || die 'Run with sudo.'

model=$(tr -d '\000' < /proc/device-tree/model)
[[ $model == 'Raspberry Pi 5 '* ]] || die "This overlay is for Raspberry Pi 5, found: $model"
command -v dtc >/dev/null || die 'dtc is required (Debian package: device-tree-compiler). Nothing installed automatically.'

if [[ -f /boot/firmware/config.txt ]]; then
  boot_dir=/boot/firmware
elif [[ -f /boot/config.txt ]]; then
  boot_dir=/boot
else
  die 'Cannot find /boot/firmware/config.txt or /boot/config.txt.'
fi
config=$(readlink -f "$boot_dir/config.txt")
overlay_dir="$boot_dir/overlays"
overlay="$overlay_dir/wb-qr-rx.dtbo"
[[ -d $overlay_dir ]] || die "Overlay directory not found: $overlay_dir"

# Full UART overlays may also claim TX GPIO4/GPIO8. Do not silently disable
# unrelated configuration: ask the operator to resolve such lines explicitly.
if grep -nE '^[[:space:]]*dtoverlay[[:space:]]*=[[:space:]]*uart[23](-pi5)?([[:space:],#]|$)' "$config"; then
  die 'Found a full UART2/UART3 overlay above. Disable those lines before installing RX-only QR UARTs.'
fi

work_dir=$(mktemp -d /tmp/wb-qr-uart.XXXXXX)
config_stage=''
overlay_stage=''
cleanup() {
  [[ -z $config_stage ]] || rm -f -- "$config_stage"
  [[ -z $overlay_stage ]] || rm -f -- "$overlay_stage"
  rm -f -- "$work_dir/wb-qr-rx.dts" "$work_dir/wb-qr-rx.dtbo" "$work_dir/config.txt"
  rmdir -- "$work_dir"
}
trap cleanup EXIT

# Standalone copy of deploy/overlays/wb-qr-rx.dts; keep these in sync.
cat > "$work_dir/wb-qr-rx.dts" <<'DTS'
/dts-v1/;
/plugin/;

/ {
    compatible = "brcm,bcm2712";
};

&rp1_gpio {
    qr_sample_rx: qr-sample-rx {
        pins = "gpio5";
        function = "uart2";
        bias-pull-up;
    };

    qr_glass_rx: qr-glass-rx {
        pins = "gpio9";
        function = "uart3";
        bias-pull-up;
    };
};

&uart2 {
    status = "okay";
    pinctrl-names = "default";
    pinctrl-0 = <&qr_sample_rx>;
};

&uart3 {
    status = "okay";
    pinctrl-names = "default";
    pinctrl-0 = <&qr_glass_rx>;
};
DTS
dtc -@ -I dts -O dtb -o "$work_dir/wb-qr-rx.dtbo" "$work_dir/wb-qr-rx.dts"

# Keep other settings and sections. Consolidate our entry into one global block.
awk '
  { sub(/\r$/, "") }
  $0 == "# BEGIN WB QR RX-ONLY UART" { managed = 1; next }
  $0 == "# END WB QR RX-ONLY UART" { managed = 0; next }
  managed { next }
  /^[[:space:]]*dtoverlay[[:space:]]*=[[:space:]]*wb-qr-rx([[:space:],#]|$)/ { next }
  { lines[++n] = $0 }
  END {
    if (managed) { print "Unclosed WB QR managed block" > "/dev/stderr"; exit 2 }
    while (n > 0 && lines[n] ~ /^[[:space:]]*$/) n--
    for (i = 1; i <= n; i++) print lines[i]
    print ""
    print "# BEGIN WB QR RX-ONLY UART"
    print "[all]"
    print "dtoverlay=wb-qr-rx"
    print "# END WB QR RX-ONLY UART"
  }
' "$config" > "$work_dir/config.txt"

config_changed=false
overlay_changed=false
cmp -s "$config" "$work_dir/config.txt" || config_changed=true
cmp -s "$overlay" "$work_dir/wb-qr-rx.dtbo" || overlay_changed=true
printf 'Model: %s\nConfig: %s\nOverlay: %s\n' "$model" "$config" "$overlay"
printf 'Config changed: %s; overlay changed: %s\n' "$config_changed" "$overlay_changed"

if $dry_run; then
  rc=0
  diff -u --label "$config (current)" --label "$config (proposed)" "$config" "$work_dir/config.txt" || rc=$?
  [[ $rc -le 1 ]] || die 'Cannot compare config files.'
  printf 'Dry run: boot files unchanged. Services and UART devices were not touched.\n'
  exit 0
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ).$$"
if $config_changed; then
  cp -- "$config" "$config.bak.qr-uart.$stamp"
  printf 'Config backup: %s\n' "$config.bak.qr-uart.$stamp"
fi
if $overlay_changed; then
  if [[ -e $overlay ]]; then
    cp -- "$overlay" "$overlay.bak.qr-uart.$stamp"
    printf 'Overlay backup: %s\n' "$overlay.bak.qr-uart.$stamp"
  fi
  overlay_stage=$(mktemp "$overlay_dir/.wb-qr-rx.dtbo.XXXXXX")
  install -m 0644 "$work_dir/wb-qr-rx.dtbo" "$overlay_stage"
  mv -f -- "$overlay_stage" "$overlay"
  overlay_stage=''
fi
if $config_changed; then
  config_stage=$(mktemp "${config}.qr-new.XXXXXX")
  cat "$work_dir/config.txt" > "$config_stage"
  chmod --reference="$config" "$config_stage"
  mv -f -- "$config_stage" "$config"
  config_stage=''
fi

printf 'Done. Reboot manually when ready; this script does not reboot or restart services.\n'
printf 'After reboot: sample QR RX GPIO5 -> /dev/ttyAMA2; glass QR RX GPIO9 -> /dev/ttyAMA3.\n'
printf 'GPIO4 and GPIO8 are not claimed by UART TX. Baud rate is configured by the application (9600).\n'
