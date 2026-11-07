#!/usr/bin/env bash
# Run on the Pi as root. Enable CDP for the existing kiosk, bound to loopback.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
service=wb-ui.service
user="$(systemctl show "$service" -p User --value)"
[[ -n "$user" && "$user" != root ]] || { echo 'Expected a non-root kiosk user' >&2; exit 1; }
home="$(getent passwd "$user" | cut -d: -f6)"
profile="$home/.config/wb-screen-chromium"
start="$(systemctl cat "$service" | awk '/^ExecStart=/{v=substr($0,11)} END{print v}')"
[[ "$start" == *--kiosk* ]] || { echo 'Kiosk ExecStart not found' >&2; exit 1; }
if [[ "$start" == *--remote-debugging-port=9222* ]]; then
  if curl --silent --fail --max-time 3 http://127.0.0.1:9222/json/list >/dev/null; then
    echo 'Screen debugging already active'
    exit 0
  fi
  echo 'CDP configured but not listening; requesting one non-blocking kiosk restart' >&2
  systemctl restart --no-block "$service"
  for attempt in $(seq 1 45); do
    if curl --silent --fail --max-time 2 http://127.0.0.1:9222/json/list >/dev/null; then
      echo "CDP became ready after kiosk restart (${attempt}s)"
      exit 0
    fi
    sleep 1
  done
  echo 'CDP did not start within 45 seconds after wb-ui restart' >&2
  systemctl status "$service" --no-pager >&2 || true
  journalctl -u "$service" -n 40 --no-pager >&2 || true
  exit 1
fi
[[ "$start" != *--user-data-dir* ]] || { echo 'Existing custom profile: review ExecStart first' >&2; exit 1; }
flags="--kiosk --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir=$profile"
start="${start/--kiosk/$flags}"
systemctl stop "$service"
# Preserve existing kiosk local storage and preferences when moving to a custom
# profile (required by newer Chromium for remote debugging).
if [[ ! -d "$profile" ]]; then
  install -d -o "$user" -g "$(id -gn "$user")" "$profile"
  if [[ -d "$home/.config/chromium" ]]; then
    rsync -a --exclude='Singleton*' --exclude='DevToolsActivePort' "$home/.config/chromium/" "$profile/"
  fi
fi
install -d /etc/systemd/system/wb-ui.service.d
printf '[Service]\nExecStart=\nExecStart=%s\n' "$start" > /etc/systemd/system/wb-ui.service.d/60-wb-screen.conf
systemctl daemon-reload
systemctl start "$service"
echo 'Screen debugging enabled on 127.0.0.1:9222'
