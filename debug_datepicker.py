from datetime import datetime
from dotenv import load_dotenv
from app import CPDBooker, upcoming_weekend

load_dotenv()

saturday, sunday = upcoming_weekend(datetime.now())

with CPDBooker() as booker:
    booker.login()

    # Inspect all inputs and interactive date-related elements on the page
    date_elements = booker.page.evaluate("""
        () => {
          const results = [];

          // All inputs
          document.querySelectorAll('input').forEach((el, i) => {
            results.push({
              type: 'input',
              index: i,
              inputType: el.type,
              name: el.name,
              id: el.id,
              placeholder: el.placeholder,
              ariaLabel: el.getAttribute('aria-label'),
              value: el.value,
              className: el.className.slice(0, 60),
              outerHTML: el.outerHTML.slice(0, 200),
            });
          });

          // Buttons that look date-related
          document.querySelectorAll('button').forEach((el, i) => {
            const text = (el.textContent || '').trim().slice(0, 40);
            const aria = (el.getAttribute('aria-label') || '').slice(0, 60);
            if (text || aria) {
              results.push({
                type: 'button',
                index: i,
                text,
                aria,
                className: el.className.slice(0, 60),
              });
            }
          });

          return results;
        }
    """)

    print("=== All inputs on page ===")
    for el in date_elements:
        if el['type'] == 'input':
            print(f"\n  input[{el['index']}] type={el['inputType']} name='{el['name']}' id='{el['id']}'")
            print(f"    placeholder='{el['placeholder']}' aria-label='{el['ariaLabel']}'")
            print(f"    value='{el['value']}'")
            print(f"    html: {el['outerHTML'][:150]}")

    print("\n\n=== Buttons (first 20) ===")
    buttons = [el for el in date_elements if el['type'] == 'button']
    for el in buttons[:20]:
        print(f"  button[{el['index']}] text='{el['text']}' aria='{el['aria']}' class='{el['className']}'")

    # Also check what the current date field looks like
    date_field_html = booker.page.evaluate("""
        () => {
          // Look for the date display area
          const candidates = [
            document.querySelector('[class*="date-picker"]'),
            document.querySelector('[class*="datepicker"]'),
            document.querySelector('[aria-label*="date"]'),
            document.querySelector('[class*="calendar"]'),
          ];
          return candidates.map(el => el ? el.outerHTML.slice(0, 300) : null);
        }
    """)
    print("\n\n=== Date picker candidates ===")
    for html in date_field_html:
        if html:
            print(f"  {html}\n")
