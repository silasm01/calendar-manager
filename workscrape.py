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
      
      times = page.query_selector_all("tr[data-user='755438'][class='cal-row']")[0].query_selector_all("div")
      
      print("Retrieved {} shifts".format(len(times)))
            
      only_shifts = [time for time in times if time.inner_text() != ""]
      
      print("Retrieved {} shifts with time".format(len(only_shifts)))
            
      only_real_shifts = [
          time for time in only_shifts
          if "#91F073" in (time.get_attribute("style") or "") or "#55AB43" in (time.get_attribute("style") or "")
      ]
      
      print("Retrieved {} real shifts".format(len(only_real_shifts)))
      
      client = DAVClient(
          url=APPROVED_CALENDAR_URL,
          username=USERNAME,
          password=PASSWORD
      )
      
      principal = client.principal()
      calendars = principal.calendars()
      
      calendar = calendars[0]
      
      for time in only_real_shifts:
          start_time_str = time.get_attribute("id").split(";")[-1] + " " + time.inner_text().split("-")[0].strip()
          end_time_str = time.get_attribute("id").split(";")[-1] + " " + time.inner_text().split("-")[1].split("\n")[0].strip()
          
          start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
          end_time = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M")
          
          existing_events = calendar.search(start=start_time, end=end_time)
          if existing_events:
              print(f"Event from {start_time_str} to {end_time_str} already exists. Skipping.")
              continue
              
          else:
              print(f"Adding event from {start_time_str} to {end_time_str}.")
          
          event = Event()
          event.add('summary', 'Arbejde')
          
          local_tz = pytz.timezone("Europe/Copenhagen")  # Change to your local timezone
          start_time_localized = local_tz.localize(start_time)
          end_time_localized = local_tz.localize(end_time)
          
          event.add('dtstart', start_time_localized.astimezone(pytz.utc))
          event.add('dtend', end_time_localized.astimezone(pytz.utc))
          
          calendar.add_event(event)
            
      browser.close()
