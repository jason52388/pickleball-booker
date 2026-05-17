#!/bin/bash
# Wrapper invoked by cron. Runs from the project root so dotenv finds .env.
cd /root/pickleball-booker
BROWSER_HEADLESS=true CRON_MODE=true xvfb-run --auto-servernum /root/pickleball-booker/.venv/bin/python daily_runner.py >> /root/pickleball-booker/data/cron.log 2>&1
