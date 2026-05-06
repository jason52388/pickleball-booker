"""
Runs the full booking flow up to the payment page but does NOT click Pay.
Saves a screenshot of the final payment screen.
"""
import os
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

    # Navigate back to the correct day
    target_date = saturday if target.day_label == "Saturday" else sunday
    booker.open_target_day(target_date)

    # Click the slot cell
    row = booker.page.locator("tr").nth(target.row_index)
    cell = row.locator("td.td-grid-cell, td.grid-cell").nth(target.col_index)
    cell.scroll_into_view_if_needed(timeout=3000)
    cell.click(timeout=3000)
    booker.page.wait_for_timeout(1000)
    print("Clicked slot cell")

    # Fill event name
    try:
        name_input = booker.page.get_by_label(re.compile("event name", re.I)).first
        name_input.fill("Pickleball", timeout=3000)
        print("Filled event name")
    except Exception:
        pass

    # Confirm bookings
    booker._click_any([("role_button", r"confirm bookings?"), ("css", "button[class*='confirm']")])
    booker.page.wait_for_timeout(1500)
    print("Clicked confirm")

    # Disclaimer checkbox + save
    try:
        checkbox = booker.page.locator("input[type='checkbox']").first
        checkbox.wait_for(timeout=4000)
        if not checkbox.is_checked():
            checkbox.check()
        booker._click_any([("role_button", r"save"), ("css", "button[class*='save']")])
        booker.page.wait_for_timeout(1000)
        print("Accepted disclaimer")
    except Exception:
        pass

    # Reserve
    booker._click_any([("role_button", r"^reserve$"), ("css", "button.booking-detail__btn--continue")])
    booker.page.wait_for_load_state("networkidle")
    booker.page.wait_for_timeout(2000)
    print("Clicked reserve")

    # Waiver checkbox
    try:
        waiver = booker.page.locator("input[type='checkbox']").first
        if waiver.is_visible(timeout=2000) and not waiver.is_checked():
            waiver.check()
        booker.page.wait_for_timeout(500)
        print("Checked waiver")
    except Exception:
        pass

    # Take screenshot of payment page — do NOT click Pay
    booker.page.screenshot(path="data/preview_payment.png", full_page=True)
    print(f"\nStopped at payment page. Screenshot saved.")
    print(f"Current URL: {booker.page.url}")
    booker.page.wait_for_timeout(3000)
