# Pickleball Booker

Automates Chicago Park District booking checks for **McFetridge pickleball** courts and excludes **ball machines**.

## Behavior

- Checks weekend availability (Saturday and Sunday) each run.
- Auto-books first slot starting between `8:00 AM` and `11:00 AM`.
- If both Saturday and Sunday have preferred slots, it books **Saturday only**.
- If no preferred weekend slot exists, sends Twilio SMS with numbered fallback options.
- Waits for SMS reply (`1`, `2`, `3`, ... or `N`) before booking fallback.
- Prevents duplicate weekend bookings with a local weekend lock file.

## Setup

1. Create virtual env and install dependencies:
   - `python3 -m venv .venv`
   - `source .venv/bin/activate`
   - `pip install -r requirements.txt`
   - `python -m playwright install chromium`
2. Copy env template:
   - `cp .env.example .env`
3. Fill `.env` with:
   - CPD credentials
   - Twilio credentials and phone numbers
4. Configure Twilio webhook:
   - URL: `https://<your-public-host>/twilio/reply`
   - Method: `POST`
5. Start webhook server:
   - `python webhook.py`
6. Run booking job:
   - `TARGET_DATE=2026-05-04 python app.py`
   - If `TARGET_DATE` is unset, it checks the upcoming Saturday/Sunday automatically.

## Safety controls

- `BOOKING_LOCK_ENABLED=true`: once a weekend is booked, later daily runs skip duplicate booking for that weekend.
- `DRY_RUN=true`: never clicks final booking actions; sends SMS for what it *would* book.

## Scheduler (daily)

Run daily so it keeps checking weekend inventory:

- Start webhook server first:
  - `python webhook.py`
- Add cron entry (example: every day at 7:00 AM):
  - `0 7 * * * cd /Users/jaman/Documents/Projects/pickleball-booker && /Users/jaman/Documents/Projects/pickleball-booker/.venv/bin/python daily_runner.py >> /Users/jaman/Documents/Projects/pickleball-booker/data/cron.log 2>&1`

## SMS approval format

- Fallback SMS is sent with a run ID like `[A1B2C3D4]`.
- Reply format:
  - `[A1B2C3D4] 2` to book option 2
  - `[A1B2C3D4] N` to skip fallback booking
- If you reply with only `2` or `N`, the webhook still maps to the latest run automatically.
- In dry-run mode, fallback options are sent as an FYI message and no booking is submitted.

## Notes

- Site selectors on ActiveNet pages can change. `app.py` uses multiple selector strategies for login/date/booking, but tune them if the portal markup changes.
- Credentials are loaded from `.env`; never commit secrets.
