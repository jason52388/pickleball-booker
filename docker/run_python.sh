#!/bin/bash
set -e

if [ "${BROWSER_HEADLESS,,}" = "false" ]; then
  exec xvfb-run --auto-servernum --server-args="-screen 0 1280x800x24" python3 "$@"
fi

exec python3 "$@"
