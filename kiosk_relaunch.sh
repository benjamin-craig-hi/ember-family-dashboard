#!/bin/bash
# Relaunch the Ember dashboard kiosk browser over SSH.
#
# Chromium must run inside the dashboard user's Wayland session, so both
# WAYLAND_DISPLAY and XDG_RUNTIME_DIR have to be exported for the process,
# and --ozone-platform=wayland must be passed explicitly (without it Chromium
# defaults to X11 and dies with "Missing X server or $DISPLAY").
export XDG_RUNTIME_DIR=/run/user/1000
export WAYLAND_DISPLAY=wayland-0
export DISPLAY=
unset XAUTHORITY

pkill -f "kiosk http://localhost:8000" 2>/dev/null
sleep 2

nohup /usr/bin/chromium-browser \
  --ozone-platform=wayland \
  --noerrdialogs \
  --disable-session-crashed-bubble \
  --incognito \
  --kiosk \
  http://localhost:8000 \
  >/tmp/kiosk.log 2>&1 < /dev/null &

sleep 8
if pgrep -f "kiosk http://localhost:8000" >/dev/null; then
  echo "kiosk running: $(pgrep -fc 'kiosk http://localhost:8000') processes"
else
  echo "FAILED to start"; tail -12 /tmp/kiosk.log
fi
