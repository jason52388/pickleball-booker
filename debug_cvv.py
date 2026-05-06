import re
from datetime import datetime
from dotenv import load_dotenv
from app import CPDBooker, upcoming_weekend, format_slot

load_dotenv()

saturday, sunday = upcoming_weekend(datetime.now())

with CPDBooker() as booker:
    booker.login()
    booker.open_target_day(sunday)
    sunday_slots = booker.scrape_slots(sunday, "Sunday")

    # Use whichever slot is still available
    target = sunday_slots[0] if sunday_slots else None
    if not target:
        print("No Sunday slots available.")
        exit()
    print(f"Available slots: {[format_slot(s) for s in sunday_slots]}")

    print(f"Booking up to checkout: {format_slot(target)}")

    # Navigate to checkout (replicate book_slot steps without CVV)
    row = booker.page.locator("tr").nth(target.row_index)
    cell = row.locator("td.td-grid-cell, td.grid-cell").nth(target.col_index)
    cell.scroll_into_view_if_needed()
    cell.click()
    booker.page.wait_for_timeout(1000)

    name_input = booker.page.get_by_label(re.compile("event name", re.I)).first
    name_input.fill("Pickleball", timeout=3000)

    booker.page.get_by_role("button", name=re.compile(r"confirm bookings?", re.I)).first.click()
    booker.page.wait_for_timeout(1500)

    checkbox = booker.page.locator("input[type='checkbox']").first
    checkbox.wait_for(timeout=4000)
    if not checkbox.is_checked():
        checkbox.check()
    booker.page.get_by_role("button", name=re.compile(r"^save$", re.I)).first.click()
    booker.page.wait_for_timeout(1000)

    booker.page.locator("button.booking-detail__btn--continue").click()
    booker.page.wait_for_load_state("networkidle")
    booker.page.wait_for_timeout(2000)

    print(f"At checkout URL: {booker.page.url}")

    # Inspect all inputs on the checkout page
    inputs = booker.page.evaluate("""
        () => [...document.querySelectorAll('input')].map(el => ({
            type: el.type,
            name: el.name,
            id: el.id,
            placeholder: el.placeholder,
            ariaLabel: el.getAttribute('aria-label'),
            label: (() => {
                if (el.id) {
                    const lbl = document.querySelector('label[for="' + el.id + '"]');
                    return lbl ? lbl.innerText.trim() : null;
                }
                return null;
            })(),
            outerHTML: el.outerHTML.slice(0, 150),
        }))
    """)

    booker.page.screenshot(path="data/debug_checkout2.png", full_page=True)
    print("Screenshot: data/debug_checkout2.png")

    print("\nAll inputs on checkout page:")
    for inp in inputs:
        print(f"  type={inp['type']} id='{inp['id']}' name='{inp['name']}'")
        print(f"    aria-label='{inp['ariaLabel']}' label='{inp['label']}'")
        print(f"    html: {inp['outerHTML']}")
        print()

    # Check for iframes (payment forms are typically inside iframes)
    frames = booker.page.frames
    print(f"\nFrames on page ({len(frames)} total):")
    for i, frame in enumerate(frames):
        print(f"  [{i}] url={frame.url}")
        try:
            frame_inputs = frame.evaluate("""
                () => [...document.querySelectorAll('input')].map(el => ({
                    type: el.type, id: el.id, name: el.name,
                    placeholder: el.placeholder,
                    ariaLabel: el.getAttribute('aria-label'),
                    outerHTML: el.outerHTML.slice(0, 150),
                }))
            """)
            for inp in frame_inputs:
                print(f"      input: type={inp['type']} id='{inp['id']}' placeholder='{inp['placeholder']}' aria='{inp['ariaLabel']}'")
        except Exception as e:
            print(f"      (could not inspect frame: {e})")
