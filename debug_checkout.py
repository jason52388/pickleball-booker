from datetime import datetime
from dotenv import load_dotenv
from app import CPDBooker, upcoming_weekend, format_slot

load_dotenv()

saturday, _ = upcoming_weekend(datetime.now())

with CPDBooker() as booker:
    booker.login()
    booker.open_target_day(saturday)
    saturday_slots = booker.scrape_slots(saturday, "Saturday")

    target = next(
        (s for s in saturday_slots if "Pickleball2A" in s.resource_name and s.start.hour == 22),
        None,
    )

    if not target:
        print("Slot not found.")
    else:
        print(f"Booking: {format_slot(target)}")

        # Click cell + fill event name
        row = booker.page.locator("tr").nth(target.row_index)
        cell = row.locator("td.td-grid-cell, td.grid-cell").nth(target.col_index)
        cell.scroll_into_view_if_needed()
        cell.click()
        booker.page.wait_for_timeout(1000)

        event_name_input = booker.page.get_by_label("Event name", exact=False).first
        event_name_input.fill("Pickleball")

        # Confirm bookings
        import re
        booker.page.get_by_role("button", name=re.compile(r"confirm bookings?", re.I)).first.click()
        booker.page.wait_for_timeout(1500)

        # Accept disclaimer
        checkbox = booker.page.locator("input[type='checkbox']").first
        checkbox.wait_for(timeout=4000)
        if not checkbox.is_checked():
            checkbox.check()
        booker.page.get_by_role("button", name=re.compile(r"save", re.I)).first.click()
        booker.page.wait_for_timeout(1500)

        # Snapshot the checkout page
        booker.page.screenshot(path="data/checkout_page.png", full_page=True)

        # Read what buttons are available
        buttons = booker.page.evaluate("""
            () => [...document.querySelectorAll('button')].map(b => ({
                text: b.innerText.trim(),
                class: b.className.slice(0, 60),
                disabled: b.disabled,
            })).filter(b => b.text)
        """)
        print("\nButtons on checkout page:")
        for b in buttons:
            print(f"  '{b['text']}' disabled={b['disabled']} class='{b['class']}'")

        total = booker.page.evaluate("""
            () => {
                const el = document.querySelector('[class*="total"], [class*="price"], [class*="amount"]');
                return el ? el.innerText : null;
            }
        """)
        print(f"\nTotal element text: {total}")
        print("Screenshot: data/checkout_page.png")
