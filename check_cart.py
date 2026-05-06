from dotenv import load_dotenv
from app import CPDBooker

load_dotenv()

with CPDBooker() as booker:
    booker.login()
    booker.page.goto("https://anc.apm.activecommunities.com/chicagoparkdistrict/quickreservation/checkout", wait_until="networkidle")
    booker.page.wait_for_timeout(3000)
    booker.page.screenshot(path="data/cart_state.png", full_page=True)
    print(f"URL: {booker.page.url}")
    text = booker.page.inner_text("body")
    print("\nPage text (first 1500 chars):")
    print(text[:1500])
