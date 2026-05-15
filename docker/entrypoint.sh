#!/bin/bash
set -e

# Make env vars available to cron jobs (cron doesn't inherit the container environment)
printenv | grep -v "^_=" | grep -v "^SHLVL=" >> /etc/environment

# Clean up any leftover browser processes from previous runs
pkill -f chrome || true

# Start cron daemon
cron

echo "Starting Telegram bot listener..."
exec python3 bot_listener.py
