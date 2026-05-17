"""
Standalone debug script for the booking flow.

Bypasses the cron / bot_listener / Telegram path entirely. Logs in, finds an
available slot for the upcoming weekend, and runs book_slot in PREVIEW mode
(stops before payment) with BOOK_DEBUG=true so every step writes a screenshot
+ HTML dump to data/debug/.

Usage on the VPS:
    cd /root/pickleball-booker
    BROWSER_HEADLESS=true xvfb-run --auto-servernum \
        .venv/bin/python debug_book.py

Then pull the artifacts to your laptop:
    scp -r root@<vps>:/root/pickleball-booker/data/debug ./debug-out
    open debug-out/*.png        # macOS
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Force the booking flow into a safe, instrumented mode no matter what's in .env.
os.environ["BOOK_DEBUG"] = "true"
os.environ["PREVIEW_STOP_BEFORE_PAY"] = "true"
os.environ.pop("DRY_RUN", None)  # DRY_RUN short-circuits book_slot; we need the real path.

from dotenv import load_dotenv
load_dotenv()
# Re-assert overrides after dotenv (it doesn't overwrite existing env vars, but be explicit).
os.environ["BOOK_DEBUG"] = "true"
os.environ["PREVIEW_STOP_BEFORE_PAY"] = "true"
os.environ.pop("DRY_RUN", None)

from app import CPDBooker, upcoming_weekend, format_slot


def main() -> int:
    chicago = ZoneInfo("America/Chicago")
    saturday, sunday = upcoming_weekend(datetime.now(chicago))
    print(f"Target weekend: Sat {saturday.date()} / Sun {sunday.date()}", flush=True)

    with CPDBooker() as booker:
        booker.login()

        for day_label, day in (("Saturday", saturday), ("Sunday", sunday)):
            booker.open_target_day(day)
            slots = booker.scrape_slots(day, day_label)
            print(f"{day_label}: {len(slots)} slots", flush=True)
            if not slots:
                continue

            picked = slots[0]
            print(f"Booking (preview, stops before pay): {format_slot(picked)}", flush=True)
            booker.open_target_day(day)
            ok = booker.book_slot(picked)
            print(f"book_slot returned: {ok}", flush=True)
            print("Artifacts: data/debug/*.png and data/debug/*.html", flush=True)
            return 0 if ok else 1

    print("No slots available on either day — nothing to debug.", flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
