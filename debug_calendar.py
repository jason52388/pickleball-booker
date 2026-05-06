from datetime import datetime
from dotenv import load_dotenv
from app import CPDBooker, upcoming_weekend

load_dotenv()

saturday, sunday = upcoming_weekend(datetime.now())
print(f"Target Saturday: {saturday.strftime('%Y-%m-%d')}\n")

with CPDBooker() as booker:
    booker.login()

    # Click the date picker input to open the calendar
    date_input = booker.page.get_by_label("Date picker, current date")
    date_input.click()
    booker.page.wait_for_timeout(1000)

    booker.page.screenshot(path="data/debug_calendar_open.png")
    print("Calendar screenshot saved.\n")

    # Capture the calendar popup HTML
    calendar_html = booker.page.evaluate("""
        () => {
          const candidates = [
            document.querySelector('.an-date-picker__popper'),
            document.querySelector('[class*="popper"]'),
            document.querySelector('[class*="calendar-popup"]'),
            document.querySelector('[class*="datepicker-popup"]'),
            document.querySelector('[role="dialog"]'),
            document.querySelector('[class*="picker__panel"]'),
          ];
          for (const el of candidates) {
            if (el) return { found: el.className, html: el.outerHTML.slice(0, 3000) };
          }
          return { found: null, html: null };
        }
    """)

    print(f"Calendar popup class: {calendar_html['found']}")
    print(f"\nCalendar HTML:\n{calendar_html['html']}")
