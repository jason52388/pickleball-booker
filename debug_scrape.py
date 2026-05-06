import json
from datetime import datetime
from dotenv import load_dotenv
from app import CPDBooker, upcoming_weekend

load_dotenv()

saturday, sunday = upcoming_weekend(datetime.now())
print(f"Checking: Saturday {saturday.strftime('%Y-%m-%d')}, Sunday {sunday.strftime('%Y-%m-%d')}\n")

with CPDBooker() as booker:
    booker.login()
    booker.open_target_day(saturday)

    # Take a screenshot so we can see the actual page state
    booker.page.screenshot(path="data/debug_saturday.png", full_page=True)
    print("Screenshot saved to data/debug_saturday.png\n")

    # Dump raw JS extraction before any filtering
    raw = booker.page.evaluate("""
        () => {
          const table = document.querySelector('table');
          if (!table) return { error: 'no table found' };

          const headers = [...table.querySelectorAll('th.table-sticky-top')].map(h => (h.textContent || '').trim());
          const rows = [...table.querySelectorAll('tr')];
          const out = [];

          rows.forEach((tr, rowIndex) => {
            const nameEl = tr.querySelector('.resource-header-cell__name');
            const nameText = (nameEl?.textContent || tr.querySelector('th')?.textContent || '').trim();
            const fullName = (nameEl?.getAttribute('title') || nameText || '').trim();
            if (!fullName) return;

            const cells = [...tr.querySelectorAll('td.td-grid-cell, td.grid-cell')];
            cells.forEach((td, colIndex) => {
              const cls = (td.className || '').toString();
              out.push({
                rowIndex,
                colIndex,
                resourceName: fullName,
                timeLabel: (headers[colIndex] || '').trim(),
                classes: cls,
                disabled: cls.includes('disabled'),
              });
            });
          });

          return { headers, totalRows: rows.length, cells: out };
        }
    """)

    print(f"Table headers ({len(raw.get('headers', []))} time columns):")
    for h in raw.get('headers', []):
        if h:
            print(f"  '{h}'")

    print(f"\nAll cells found (before filtering): {len(raw.get('cells', []))}")
    print("\nCells broken down by resource:")
    by_resource = {}
    for cell in raw.get('cells', []):
        name = cell['resourceName']
        by_resource.setdefault(name, {'enabled': [], 'disabled': []})
        key = 'disabled' if cell['disabled'] else 'enabled'
        by_resource[name][key].append(cell['timeLabel'])

    for resource, data in by_resource.items():
        print(f"\n  {resource}")
        print(f"    Enabled (not disabled): {data['enabled'] or 'none'}")
        print(f"    Disabled: {len(data['disabled'])} slots")

    # Also dump the first few raw cells to see all CSS classes
    print("\n\nFirst 10 raw cells (to inspect CSS classes):")
    for cell in raw.get('cells', [])[:10]:
        print(f"  row={cell['rowIndex']} col={cell['colIndex']} resource='{cell['resourceName']}' "
              f"time='{cell['timeLabel']}' disabled={cell['disabled']} classes='{cell['classes'][:80]}'")
