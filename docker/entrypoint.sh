#!/bin/bash
set -e

if [ "${BROWSER_HEADLESS,,}" = "false" ]; then
  export DISPLAY="${DISPLAY:-:99}"
fi

# Make env vars available to cron jobs (cron doesn't inherit the container environment)
printenv | grep -v "^_=" | grep -v "^SHLVL=" >> /etc/environment

# Clean up any leftover browser processes and stale lock files from previous runs
pkill -f chrome || true
find /app/data/browser_profile -name "SingletonLock" -delete 2>/dev/null || true

if [ "${BROWSER_HEADLESS,,}" = "false" ]; then
  if ! pgrep -f "Xvfb ${DISPLAY}" >/dev/null 2>&1; then
    Xvfb "$DISPLAY" -screen 0 1280x800x24 -nolisten tcp &
    echo "Started Xvfb on DISPLAY=$DISPLAY"
  fi
fi

# Start cron daemon
cron

echo "Starting Telegram bot listener..."
exec /app/docker/run_python.sh bot_listener.py
