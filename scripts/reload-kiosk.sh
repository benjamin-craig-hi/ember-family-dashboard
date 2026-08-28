#!/usr/bin/env bash
# Reload the kiosk browser after a deploy.
# Kills the existing kiosk Chromium and relaunches it in the Wayland session.
# Safe to run from any context (SSH deploy, git hook, manual); no-ops if the
# kiosk browser or an active Wayland session isn't present.
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

# Kill the existing kiosk browser (match the kiosk URL specifically).
pkill -f "kiosk http://localhost:8000" 2>/dev/null
sleep 1

# Relaunch detached so it survives this session.
setsid chromium-browser --ozone-platform=wayland --noerrdialogs \
  --disable-session-crashed-bubble --incognito --kiosk \
  http://localhost:8000 >/dev/null 2>&1 < /dev/null &

exit 0
