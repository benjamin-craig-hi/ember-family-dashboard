#!/usr/bin/env bash
# Reload the kiosk browser after a deploy.
# Stops the existing kiosk Chromium and relaunches it as a systemd --user
# transient unit so it survives SSH session teardown (a plain `setsid ... &`
# gets killed when the deploy SSH session closes).
set -u

command -v chromium-browser >/dev/null 2>&1 || exit 0

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export WAYLAND_DISPLAY="wayland-0"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"

# Only act if there's an active Wayland session (kiosk is logged in).
[ -S "${XDG_RUNTIME_DIR}/wayland-0" ] || exit 0

# Wait briefly for the backend to be reachable (it may be mid-restart).
for _ in $(seq 1 15); do
  curl -sf -m 2 http://localhost:8000/ >/dev/null 2>&1 && break
  sleep 1
done

# Stop any existing kiosk browser unit, then kill stragglers.
systemctl --user stop ember-kiosk.service 2>/dev/null
pkill -f "kiosk http://localhost:8000" 2>/dev/null
sleep 1

# Relaunch as a transient user unit so it survives this session.
systemd-run --user --unit=ember-kiosk \
  --description="Ember kiosk browser" \
  --setenv=XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR}" \
  --setenv=WAYLAND_DISPLAY="${WAYLAND_DISPLAY}" \
  --setenv=DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS}" \
  chromium-browser --ozone-platform=wayland --noerrdialogs \
  --disable-session-crashed-bubble --incognito --kiosk \
  http://localhost:8000 >/dev/null 2>&1

exit 0
