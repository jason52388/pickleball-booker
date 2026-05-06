from datetime import datetime
from dotenv import load_dotenv
from app import Slot, send_calendar_invite

load_dotenv()

# Sunday May 3, 3:00 PM - 4:00 PM, McFetridge Pickleball2B
slot = Slot(
    day_label="Sunday",
    label="Sunday 03:00 PM - 04:00 PM (McFetridge Pickleball2B)",
    start=datetime(2026, 5, 3, 15, 0),
    end=datetime(2026, 5, 3, 16, 0),
    locator_hint="McFetridge Pickleball2B",
    resource_name="McFetridge Pickleball2B",
    row_index=4,
    col_index=9,
)

print(f"Sending calendar invite for: {slot.label}")
send_calendar_invite(slot)
print("Done — check your inbox!")
