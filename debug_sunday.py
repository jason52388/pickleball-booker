from datetime import datetime
from dotenv import load_dotenv
from app import CPDBooker, upcoming_weekend

load_dotenv()

saturday, sunday = upcoming_weekend(datetime.now())
print(f"Navigating to Sunday: {sunday.strftime('%Y-%m-%d')}\n")

with CPDBooker() as booker:
    booker.login()
    booker.open_target_day(sunday)

    booker.page.screenshot(path="data/debug_sunday.png", full_page=True)
    print("Screenshot saved to data/debug_sunday.png\n")

    # Dump every cell's aria-label for all pickleball rows
    result = booker.page.evaluate("""
        () => {
          const table = document.querySelector('table');
          if (!table) return [];
          const rows = [...table.querySelectorAll('tr')];
          const out = [];
          rows.forEach((tr, rowIndex) => {
            const nameEl = tr.querySelector('.resource-header-cell__name');
            const fullName = (nameEl?.getAttribute('title') || nameEl?.textContent || '').trim();
            if (!fullName.toLowerCase().includes('pickleball')) return;

            const cells = [...tr.querySelectorAll('td')];
            cells.forEach((td, colIndex) => {
              const inner = td.querySelector('.grid-cell');
              const tdCls = td.className || '';
              const innerCls = inner ? (inner.className || '') : '';
              const ariaLabel = inner ? (inner.getAttribute('aria-label') || '') : '';
              out.push({
                resource: fullName,
                colIndex,
                tdClass: tdCls,
                innerClass: innerCls,
                ariaLabel: ariaLabel.trim(),
              });
            });
          });
          return out;
        }
    """)

    print(f"Total pickleball cells: {len(result)}\n")
    current_resource = None
    for cell in result:
        if cell['resource'] != current_resource:
            current_resource = cell['resource']
            print(f"\n=== {current_resource} ===")
        available_marker = " <-- AVAILABLE" if 'available' in cell['ariaLabel'].lower() else ""
        print(f"  col={cell['colIndex']:2d}  tdCls='{cell['tdClass'][:40]}'  aria='{cell['ariaLabel'][:60]}'{available_marker}")
