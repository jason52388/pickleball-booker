from playwright.sync_api import sync_playwright
import os
from dotenv import load_dotenv
load_dotenv()

print("Starting browser...")
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        url = os.environ["CPD_URL"]
        print(f"Navigating to: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        print("URL:", page.url)
        print("Title:", page.title())
        print("\n=== LINKS ===")
        for el in page.query_selector_all("a"):
            text = el.inner_text().strip()[:80]
            href = el.get_attribute("href")
            if text:
                print(f"  {repr(text)} | {href}")
        print("\n=== BUTTONS ===")
        for el in page.query_selector_all("button"):
            text = el.inner_text().strip()[:80]
            btype = el.get_attribute("type")
            if text:
                print(f"  {repr(text)} | type={btype}")
        print("\nDone.")
    except Exception as e:
        print("ERROR:", e)
    finally:
        browser.close()
