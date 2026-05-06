from datetime import datetime
from dotenv import load_dotenv
from app import CPDBooker, upcoming_weekend

load_dotenv()

saturday, sunday = upcoming_weekend(datetime.now())

with CPDBooker() as booker:
    booker.login()
    booker.open_target_day(sunday)

    # Wait a bit more to let any lazy-loaded data settle
    booker.page.wait_for_timeout(3000)
    booker.page.screenshot(path="data/debug_sunday2.png", full_page=True)
    print("Screenshot saved: data/debug_sunday2.png")

    # Check current date shown in the picker
    current_date_value = booker.page.locator("input[aria-label*='Date picker']").input_value()
    print(f"Date picker shows: {current_date_value}")

    # Dump aria-labels for pickleball courts
    result = booker.page.evaluate("""
        () => {
          const table = document.querySelector('table');
          if (!table) return [];
          const rows = [...table.querySelectorAll('tr')];
          const out = [];
          rows.forEach((tr) => {
            const nameEl = tr.querySelector('.resource-header-cell__name');
            const fullName = (nameEl?.getAttribute('title') || nameEl?.textContent || '').trim();
            if (!fullName.toLowerCase().includes('pickleball2')) return;
            const cells = [...tr.querySelectorAll('td')];
            cells.forEach((td) => {
              const inner = td.querySelector('.grid-cell');
              if (!inner) return;
              const ariaLabel = (inner.getAttribute('aria-label') || '').trim();
              const innerCls = inner.className;
              if (ariaLabel) out.push({ resource: fullName, ariaLabel, innerCls });
            });
          });
          return out;
        }
    """)

    print()
    current = None
    for cell in result:
        if cell['resource'] != current:
            current = cell['resource']
            print(f"\n=== {current} ===")
        tag = " *** AVAILABLE ***" if cell['ariaLabel'].endswith('Available') else ""
        print(f"  {cell['ariaLabel'][:70]}{tag}")
