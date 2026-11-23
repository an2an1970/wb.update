#!/usr/bin/env bash
# X11/Openbox kiosk keyboard lockdown. Run as root on the device.
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
USER_NAME="${APP_USER:-wbui}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
[[ -n "$HOME_DIR" && -d "$HOME_DIR" ]] || { echo "Missing kiosk user" >&2; exit 1; }
BACKUP="/var/lib/wb-kiosk-lockdown"
OPENBOX_DIR="$HOME_DIR/.config/openbox"
OPENBOX_FILE="$OPENBOX_DIR/lxde-pi-rc.xml"
XORG_FILE="/etc/X11/xorg.conf.d/98-wb-keyboard-lockdown.conf"
LIGHTDM_FILE="/etc/lightdm/lightdm.conf.d/90-wb-kiosk-lockdown.conf"
mkdir -p "$BACKUP" "$OPENBOX_DIR" "$(dirname "$XORG_FILE")" "$(dirname "$LIGHTDM_FILE")"
if [[ "${1:-}" == "--disable" ]]; then
  for item in "$OPENBOX_FILE" "$XORG_FILE" "$LIGHTDM_FILE"; do
    name="$(echo "$item" | tr / _)"
    if [[ -f "$BACKUP/$name" ]]; then cp "$BACKUP/$name" "$item"; else
      [[ "$item" == "$OPENBOX_FILE" ]] || rm -f "$item"
    fi
  done
  systemctl unmask ctrl-alt-del.target 2>/dev/null || true
  systemctl disable --now wb-kiosk-keyboard.service 2>/dev/null || true
  rm -f /etc/systemd/system/wb-kiosk-keyboard.service
  systemctl daemon-reload
  echo "Lockdown disabled; reboot to restore Xorg and LightDM behavior."
  exit 0
fi
for item in "$OPENBOX_FILE" "$XORG_FILE" "$LIGHTDM_FILE"; do
  name="$(echo "$item" | tr / _)"
  if [[ -f "$item" && ! -e "$BACKUP/$name" ]]; then cp -p "$item" "$BACKUP/$name"; fi
done
# Preserve window rules, themes and menus; remove only global keyboard bindings.
if [[ -f "$OPENBOX_FILE" ]]; then
  python3 - "$OPENBOX_FILE" <<'PY'
import pathlib, re, sys
p=pathlib.Path(sys.argv[1])
s=p.read_text()
s,n=re.subn(r'<keyboard(?:\s[^>]*)?>.*?</keyboard>', '<keyboard/>', s, count=1, flags=re.S)
if n!=1: raise SystemExit('Openbox keyboard section not found; refusing to overwrite config')
p.write_text(s)
PY
else
  echo "Openbox config missing: $OPENBOX_FILE" >&2
  exit 1
fi
chown "$USER_NAME:$(id -gn "$USER_NAME")" "$OPENBOX_FILE"
cat > "$XORG_FILE" <<'EOF'
Section "ServerFlags"
    Option "DontVTSwitch" "true"
    Option "DontZap" "true"
EndSection
EOF
cat > "$LIGHTDM_FILE" <<'EOF'
[Seat:*]
xserver-command=X -nolisten tcp -novtswitch
EOF
# Prevent systemd-logind from interpreting Ctrl+Alt+Del at the console.
systemctl mask ctrl-alt-del.target
# X11 grabs browser and desktop shortcuts before Chromium receives them.
cat > /usr/local/bin/wb-kiosk-keyboard <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export DISPLAY=:0
export XAUTHORITY=/home/${APP_USER:-wbui}/.Xauthority
command -v xbindkeys >/dev/null || { echo "xbindkeys not installed" >&2; exit 1; }
exec xbindkeys -n -f /etc/wb-kiosk-xbindkeysrc
EOF
chmod 755 /usr/local/bin/wb-kiosk-keyboard
# xbindkeys consumes shortcuts using a no-op command. Never grab plain typing keys.
{
  echo '# Managed by WB kiosk lockdown. Empty command consumes the key.'
  for key in 'Alt + Tab' 'Alt + Shift + Tab' 'Alt + F4' 'Alt + space' 'Control + Alt + Delete' 'Control + Alt + BackSpace' 'Control + l' 'Control + t' 'Control + w' 'Control + n' 'Control + Shift + n' 'Control + Shift + t' 'Control + Shift + i' 'Control + Shift + j' 'Control + Shift + Delete' 'Control + r' 'Control + F4' 'Control + Page_Up' 'Control + Page_Down' 'F11' 'F12' 'Super_L' 'Super_R'; do
    printf '"true"\n  %s\n\n' "$key"
  done
} > /etc/wb-kiosk-xbindkeysrc
cat > /etc/systemd/system/wb-kiosk-keyboard.service <<EOF
[Unit]
Description=WB kiosk X11 shortcut blocker
After=display-manager.service
Wants=display-manager.service
[Service]
Type=simple
User=$USER_NAME
Environment=DISPLAY=:0
Environment=XAUTHORITY=$HOME_DIR/.Xauthority
ExecStart=/usr/bin/xbindkeys -n -f /etc/wb-kiosk-xbindkeysrc
Restart=always
RestartSec=3
[Install]
WantedBy=graphical.target
EOF
if ! command -v xbindkeys >/dev/null; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y xbindkeys
fi
systemctl daemon-reload
systemctl enable --now wb-kiosk-keyboard.service
echo "Configured. Reboot to apply Xorg/LightDM changes."
echo "Rollback: sudo bash $0 --disable; sudo systemctl unmask ctrl-alt-del.target; sudo reboot"
