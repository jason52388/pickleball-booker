from datetime import datetime
from dotenv import load_dotenv
from app import CPDBooker, upcoming_weekend, format_slot

load_dotenv()

saturday, sunday = upcoming_weekend(datetime.now())
print(f"Targeting: Sunday {sunday.strftime('%Y-%m-%d')}\n")

with CPDBooker() as booker:
    booker.login()
    booker.open_target_day(sunday)
    sunday_slots = booker.scrape_slots(sunday, "Sunday")

    print("Available Sunday slots:")
    for s in sunday_slots:
        print(f"  {format_slot(s)}  (row={s.row_index}, col={s.col_index})")

    target = sunday_slots[0] if sunday_slots else None

    if not target:
        print("\nNo slots available.")
    else:
        print(f"\nBooking: {format_slot(target)}")
        success = booker.book_slot(target)
        print(f"book_slot returned: {success}")

        booker.page.wait_for_timeout(3000)
        booker.page.screenshot(path="data/book_final.png", full_page=True)
        print(f"Final URL: {booker.page.url}")
        print("Final screenshot saved: data/book_final.png")
