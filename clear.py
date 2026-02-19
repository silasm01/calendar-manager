from caldav import DAVClient
import os
from dotenv import load_dotenv

load_dotenv()

APPROVED_CALENDAR_URL = os.getenv("APPROVED_CALENDAR_URL")
USERNAME = os.getenv("RADICALE_USERNAME")
PASSWORD = os.getenv("RADICALE_PASSWORD")

client = DAVClient(
    url=APPROVED_CALENDAR_URL,
    username=USERNAME,
    password=PASSWORD
)

principal = client.principal()
calendar = principal.calendars()[0]  # or select by name

events = calendar.events()

print(f"Found {len(events)} events. Deleting...")

for event in events:
    event.delete()

print("Calendar cleared successfully.")