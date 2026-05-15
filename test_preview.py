"""
Runs the full booking flow up to the payment page but does NOT click Pay.
Saves a screenshot of the final payment screen.

Timings mirror app.py's book_slot() — jittery _human_pause() ranges and per-key
typing — so the reCAPTCHA v3 score is comparable to the real cron flow.
"""
import os
import random
import re
from datetime import datetime
from dotenv import load_dotenv
from app import CPDBooker, upcoming_weekend

load_dotenv()

saturday, sunday = upcoming_weekend(datetime.now())
print(f"Targeting weekend: {saturday.strftime('%Y-%m-%d')} / {sunday.strftime('%Y-%m-%d')}\n")

with CPDBooker() as booker:
    booker.login()
    booker.open_target_day(saturday)
    slots = booker.scrape_slots(saturday, "Saturday")

    if not slots:
        booker.open_target_day(sunday)
        slots = booker.scrape_slots(sunday, "Sunday")

    if not slots:
        print("No slots found.")
        raise SystemExit(0)

    target = slots[0]
    print(f"Selected slot: {target.label}")

    target_date = saturday if target.day_label == "Saturday" else sunday
    booker.open_target_day(target_date)

    row = booker.page.locator("tr").nth(target.row_index)
    cell = row.locator("td.td-grid-cell, td.grid-cell").nth(target.col_index)
    cell.scroll_into_view_if_needed(timeout=2500)
    booker._human_pause(0.5, 1.2)
    cell.click(timeout=2500)
    booker._human_pause(1.5, 3.0)
    print("Clicked slot cell")

    event_name = os.getenv("BOOKING_EVENT_NAME", "Pickleball")
    try:
        name_input = booker.page.get_by_label(re.compile("event name", re.I)).first
        name_input.click(timeout=10000)
        booker._human_pause(0.3, 0.7)
        for ch in event_name:
            name_input.type(ch, delay=random.randint(60, 160))
        print("Filled event name")
    except Exception:
        pass

    booker._human_pause(0.8, 1.8)

    booker._click_any([("role_button", r"confirm bookings?"), ("css", "button[class*='confirm']")])
    booker._human_pause(2.0, 4.0)
    print("Clicked confirm")

    try:
        checkbox = booker.page.locator("input[type='checkbox']").first
        checkbox.wait_for(timeout=4000)
        if not checkbox.is_checked():
            booker._human_pause(0.5, 1.0)
            checkbox.check()
        booker._human_pause(0.5, 1.2)
        booker._click_any([("role_button", r"save"), ("css", "button[class*='save']")])
        booker._human_pause(1.5, 3.0)
        print("Accepted disclaimer")
    except Exception:
        pass

    booker._human_pause(1.0, 2.0)
    booker._click_any([("role_button", r"^reserve$"), ("css", "button.booking-detail__btn--continue")])
    try:
        booker.page.wait_for_load_state("networkidle", timeout=60000)
    except Exception:
        pass
    booker._human_pause(2.0, 4.0)
    print("Clicked reserve")

    try:
        waiver = booker.page.locator("input[type='checkbox']").first
        if waiver.is_visible(timeout=2000) and not waiver.is_checked():
            waiver.check()
        booker._human_pause(0.4, 0.8)
        print("Checked waiver")
    except Exception:
        pass

    booker.page.screenshot(path="data/preview_payment.png", full_page=True)
    print(f"\nStopped at payment page. Screenshot saved.")
    print(f"Current URL: {booker.page.url}")
    booker.page.wait_for_timeout(3000)
