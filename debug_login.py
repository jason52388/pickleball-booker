from playwright.sync_api import sync_playwright
import os
from dotenv import load_dotenv
load_dotenv()

def dump_inputs(page, label):
    print(f"\n=== {label} ===")
    print("URL:", page.url)
    print("Title:", page.title())
    print("--- INPUTS ---")
    for el in page.query_selector_all("input"):
        attrs = {a: el.get_attribute(a) for a in ["type", "name", "id", "placeholder", "aria-label", "autocomplete"]}
        attrs = {k: v for k, v in attrs.items() if v}
        print(f"  {attrs}")
    print("--- BUTTONS ---")
    for el in page.query_selector_all("button"):
        text = el.inner_text().strip()[:80]
        btype = el.get_attribute("type")
        if text:
            print(f"  {repr(text)} | type={btype}")

print("Starting browser...")
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        url = os.environ["CPD_URL"]
        print(f"Navigating to: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        dump_inputs(page, "LANDING PAGE")

        print("\nClicking Sign In...")
        sign_in = page.locator("a:has-text('Sign In'), a:has-text('Sign in now')").first
        sign_in.wait_for(state="visible", timeout=10000)
        sign_in.click(timeout=10000, no_wait_after=True)
        page.wait_for_timeout(5000)
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        dump_inputs(page, "AFTER SIGN IN CLICK")

        print("\n=== IFRAMES ===")
        for frame in page.frames:
            if frame != page.main_frame:
                print(f"Frame: {frame.url}")
                for el in frame.query_selector_all("input"):
                    attrs = {a: el.get_attribute(a) for a in ["type", "name", "id", "placeholder", "aria-label"]}
                    attrs = {k: v for k, v in attrs.items() if v}
                    print(f"  {attrs}")
        print("\nDone.")
    except Exception as e:
        print("ERROR:", e)
    finally:
        browser.close()
