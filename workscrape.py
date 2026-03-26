from time import sleep

from playwright.sync_api import sync_playwright
import os
from dotenv import load_dotenv

from caldav import DAVClient
from icalendar import Calendar, Event
from datetime import datetime, timedelta
import pytz

load_dotenv()

APPROVED_CALENDAR_URL = os.getenv("APPROVED_CALENDAR_URL")
USERNAME = os.getenv("RADICALE_USERNAME")
PASSWORD = os.getenv("RADICALE_PASSWORD")
SAMESYSTEM_LOGIN_URL = os.getenv("SAMESYSTEM_LOGIN_URL", "https://in.samesystem.com/login")
SAMESYSTEM_EMAIL = os.getenv("SAMESYSTEM_EMAIL")
SAMESYSTEM_PASSWORD = os.getenv("SAMESYSTEM_PASSWORD")
 
with sync_playwright() as p:
      browser = p.chromium.launch(headless=False)
      page = browser.new_page()
      page.goto(SAMESYSTEM_LOGIN_URL)
      page.fill('input[name="user_session[email]"]', SAMESYSTEM_EMAIL)
      page.fill('input[name="user_session[password]"]', SAMESYSTEM_PASSWORD)
      page.click("button[type=submit]")
      
      page.hover("text=Vagtplan")
      page.click("text=Hele perioden")

      def scrape_shifts(page):
          times = page.query_selector_all("tr[data-user='755438'][class='cal-row']")[0].query_selector_all("div")
          only_shifts = [t for t in times if t.inner_text() != ""]
          real = [
              t for t in only_shifts
              if "#91F073" in (t.get_attribute("style") or "") or "#55AB43" in (t.get_attribute("style") or "")
          ]
          # Extract data immediately before any navigation invalidates the handles
          return [(t.get_attribute("id"), t.inner_text()) for t in real]

      # Scrape current period
      current_period_shifts = scrape_shifts(page)
      print(f"Retrieved {len(current_period_shifts)} real shifts from current period.")

      # Navigate to next period and scrape again
      page.click("id=page.calendar.header.navigation")
      page.click("data-test-id=component.calendar.nextMonth")
      page.click("data-test-id=component.calendar.nextMonth")
      page.click("data-test-id=component.calendar.day-2026-05-01")

      next_period_shifts = scrape_shifts(page)
      print(f"Retrieved {len(next_period_shifts)} real shifts from next period.")

      only_real_shifts = current_period_shifts + next_period_shifts
      print(f"Retrieved {len(only_real_shifts)} real shifts total.")

      client = DAVClient(
          url=APPROVED_CALENDAR_URL,
          username=USERNAME,
          password=PASSWORD
      )
      
      principal = client.principal()
      calendars = principal.calendars()
      
      calendar = calendars[next(i for i, cal in enumerate(calendars) if cal.name == "Arbejde")]

      local_tz = pytz.timezone("Europe/Copenhagen")
      now = datetime.now(pytz.utc)

      # Build set of scraped shift times in UTC
      scraped_shifts = []
      for elem_id, elem_text in only_real_shifts:
          start_time_str = elem_id.split(";")[-1] + " " + elem_text.split("-")[0].strip()
          end_time_str = elem_id.split(";")[-1] + " " + elem_text.split("-")[1].split("\n")[0].strip()

          start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
          end_time = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M")

          start_utc = local_tz.localize(start_time).astimezone(pytz.utc)
          end_utc = local_tz.localize(end_time).astimezone(pytz.utc)
          scraped_shifts.append((start_utc, end_utc))

      scraped_set = set(scraped_shifts)

      # Remove future events no longer present in the scraped schedule
      if scraped_shifts:
          range_start = min(s[0] for s in scraped_shifts)
          range_end = max(s[1] for s in scraped_shifts)
          all_existing = calendar.search(start=range_start, end=range_end)
          print(f"Found {len(all_existing)} existing future events in calendar to check for removal.")
          for existing in all_existing:
              ical_obj = Calendar.from_ical(existing.data)
              for component in ical_obj.walk():
                  if component.name != "VEVENT":
                      continue
                  if str(component.get('summary', '')) != 'Arbejde':
                      continue
                  dtstart = component.get('dtstart').dt
                  dtend = component.get('dtend').dt
                  if not isinstance(dtstart, datetime):
                      continue
                  dtstart_utc = dtstart.astimezone(pytz.utc) if dtstart.tzinfo else pytz.utc.localize(dtstart)
                  dtend_utc = dtend.astimezone(pytz.utc) if dtend.tzinfo else pytz.utc.localize(dtend)
                  if dtstart_utc < now:
                      continue
                  print(f"Checking existing event {dtstart_utc} - {dtend_utc} (in scraped set: {(dtstart_utc, dtend_utc) in scraped_set})")
                  if (dtstart_utc, dtend_utc) not in scraped_set:
                      print(f"Removing stale event {dtstart_utc} - {dtend_utc}.")
                      existing.delete()
                  break

      # Add new events
      for start_utc, end_utc in scraped_shifts:
          existing_events = calendar.search(start=start_utc, end=end_utc)
          if existing_events:
              print(f"Event from {start_utc} to {end_utc} already exists. Skipping.")
              continue

          print(f"Adding event from {start_utc} to {end_utc}.")
          event = Event()
          event.add('summary', 'Arbejde')
          event.add('dtstart', start_utc)
          event.add('dtend', end_utc)
          calendar.add_event(event)

      browser.close()
