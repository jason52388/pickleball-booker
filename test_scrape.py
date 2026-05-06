from datetime import datetime, timedelta
from dotenv import load_dotenv
from app import CPDBooker, upcoming_weekend, format_slot

load_dotenv()

saturday, sunday = upcoming_weekend(datetime.now())
print(f"Checking slots for: Saturday {saturday.strftime('%Y-%m-%d')} and Sunday {sunday.strftime('%Y-%m-%d')}\n")

with CPDBooker() as booker:
    booker.login()
    print("Logged in successfully.\n")

    booker.open_target_day(saturday)
    sat_slots = booker.scrape_slots(saturday, "Saturday")
    print(f"Saturday slots ({len(sat_slots)} found):")
    for s in sat_slots:
        print(f"  {format_slot(s)}")

    booker.open_target_day(sunday)
    sun_slots = booker.scrape_slots(sunday, "Sunday")
    print(f"\nSunday slots ({len(sun_slots)} found):")
    for s in sun_slots:
        print(f"  {format_slot(s)}")
