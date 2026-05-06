"""
Run this once to authorize Google Calendar access.

Steps before running:
  1. Go to https://console.cloud.google.com
  2. Create a project (or select an existing one)
  3. Enable the Google Calendar API
  4. Go to Credentials → Create Credentials → OAuth client ID
  5. Application type: Desktop app
  6. Download the JSON and save it as:
       data/gcal_client_secret.json
  7. Then run:
       .venv/bin/python setup_gcal_auth.py

A browser window will open for you to authorize. The token is saved to
data/gcal_token.json and reused automatically from then on.
"""
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
DATA_DIR = Path("data")
CLIENT_SECRET = DATA_DIR / "gcal_client_secret.json"
TOKEN = DATA_DIR / "gcal_token.json"

if not CLIENT_SECRET.exists():
    print(f"ERROR: {CLIENT_SECRET} not found.")
    print(__doc__)
    raise SystemExit(1)

flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
creds = flow.run_local_server(port=0)
with open(TOKEN, "w") as f:
    f.write(creds.to_json())
print(f"Authorization complete. Token saved to {TOKEN}")
