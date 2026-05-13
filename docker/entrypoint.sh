#!/bin/bash
set -e

# Make env vars available to cron jobs (cron doesn't inherit the container environment)
printenv | grep -v "^_=" | grep -v "^SHLVL=" >> /etc/environment

# Start cron daemon
cron

echo "Starting Telegram bot listener..."
exec xvfb-run --auto-servernum python bot_listener.py
