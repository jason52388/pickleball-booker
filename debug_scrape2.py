import json
from datetime import datetime
from dotenv import load_dotenv
from app import CPDBooker, upcoming_weekend

load_dotenv()

saturday, sunday = upcoming_weekend(datetime.now())

with CPDBooker() as booker:
    booker.login()
    booker.open_target_day(saturday)

    # Get ALL unique CSS classes used on grid cells so we can understand the full state model
    cell_class_analysis = booker.page.evaluate("""
        () => {
          const table = document.querySelector('table');
          if (!table) return {};

          const classMap = {};
          const cells = [...table.querySelectorAll('td')];
          cells.forEach(td => {
            const cls = (td.className || '').trim();
            classMap[cls] = (classMap[cls] || 0) + 1;
          });
          return classMap;
        }
    """)

    print("All unique CSS class combinations on <td> cells (class => count):")
    for cls, count in sorted(cell_class_analysis.items(), key=lambda x: -x[1]):
        print(f"  ({count:3d}x) '{cls}'")

    # Get the specific Pickleball2B row and its 7 PM cell in detail
    pickleball2b_detail = booker.page.evaluate("""
        () => {
          const table = document.querySelector('table');
          const rows = [...table.querySelectorAll('tr')];
          const headers = [...table.querySelectorAll('th.table-sticky-top')].map(h => (h.textContent || '').trim());
          const result = [];

          rows.forEach((tr, rowIndex) => {
            const nameEl = tr.querySelector('.resource-header-cell__name');
            const fullName = (nameEl?.getAttribute('title') || nameEl?.textContent || '').trim();
            if (!fullName.includes('Pickleball2B')) return;

            const cells = [...tr.querySelectorAll('td')];
            cells.forEach((td, colIndex) => {
              result.push({
                colIndex,
                timeLabel: (headers[colIndex] || '').trim(),
                classes: td.className,
                innerText: (td.innerText || '').trim().slice(0, 80),
                innerHTML: td.innerHTML.slice(0, 200),
              });
            });
          });
          return result;
        }
    """)

    print("\n\nPickleball2B row — all cells:")
    for cell in pickleball2b_detail:
        print(f"\n  col={cell['colIndex']} time='{cell['timeLabel']}'")
        print(f"    classes: '{cell['classes']}'")
        print(f"    text: '{cell['innerText']}'")
        print(f"    html: '{cell['innerHTML'][:120]}'")
