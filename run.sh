#!/bin/bash
# Wrapper invoked by cron. Runs from the project root so dotenv finds .env.
cd /Users/jaman/Documents/Projects/pickleball-booker
/Users/jaman/Documents/Projects/pickleball-booker/.venv/bin/python daily_runner.py >> /Users/jaman/Documents/Projects/pickleball-booker/data/cron.log 2>&1
