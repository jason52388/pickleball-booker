#!/bin/bash
set -e

if [ "${BROWSER_HEADLESS,,}" = "false" ]; then
  export DISPLAY="${DISPLAY:-:99}"
  if ! pgrep -f "Xvfb ${DISPLAY}" >/dev/null 2>&1; then
    Xvfb "$DISPLAY" -screen 0 1280x800x24 -nolisten tcp &
    sleep 1
  fi
fi

exec python3 "$@"
