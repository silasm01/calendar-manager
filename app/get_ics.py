from icalendar import Calendar, Event as ICalEvent
import requests
from dateutil.rrule import rrulestr
from dateutil.parser import parse
from datetime import datetime, timedelta, timezone
import uuid
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import os
from dotenv import load_dotenv
# from .models import db, Event

load_dotenv()

ICS_URLS = {
    "family": os.getenv('FAMILY_CALENDAR_ICS_URL'),
    "Ronja": os.getenv('RONJA_CALENDAR_ICS_URL')
}

APPROVED_CALENDAR_URL = os.getenv('APPROVED_CALENDAR_URL')

BLOCKED_CALENDAR_URLS = {
    "family": os.getenv('FAMILY_BLOCKED_CALENDAR_URL'),
    "Ronja": os.getenv('RONJA_BLOCKED_CALENDAR_URL')
}

SYNC_WINDOW_DAYS = int(os.getenv('SYNC_WINDOW_DAYS', '90'))

def get_all_event_buffers():
    """Get all buffers from the database at once (more efficient than per-event queries)"""
    buffers = {}
    try:
        conn = sqlite3.connect('calmanage.db')
        cursor = conn.cursor()
        cursor.execute('SELECT event_uid, source, buffer_before, buffer_after FROM event_buffers')
        rows = cursor.fetchall()
        conn.close()
        
        for row in rows:
            uid = row[0]
            source = row[1]
            key = f"{uid}_{source}"
            buffers[key] = (row[2], row[3])
    except Exception as e:
        print(f"Error fetching buffers: {e}")
    
    return buffers


def get_buffer_for_event(uid, source, buffers_cache):
    """Get buffers for an event from in-memory cache"""
    key = f"{uid}_{source}"
    if key in buffers_cache:
        return buffers_cache[key]
    return 0, 0


def _fetch_url(url, timeout=10):
    """Helper function to fetch a URL. Returns tuple (url, content, error)"""
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return (url, response.content, None)
    except Exception as e:
        return (url, None, e)


def _parse_calendar_events(cal_content, source_name, approved_events, now, window_end, buffers_cache):
    """Parse calendar content and return list of events"""
    events = []
    try:
        cal = Calendar.from_ical(cal_content)
        
        for component in cal.walk():
            if component.name != "VEVENT":
                continue
            
            dtstart = component.get("dtstart").dt
            if isinstance(dtstart, datetime):
                dtstart = dtstart.astimezone(timezone.utc)
            else:
                dtstart = datetime.combine(dtstart, datetime.min.time(), tzinfo=timezone.utc)
                
            if dtstart < now or dtstart > window_end:
                continue
            
            dtend = component.get("dtend").dt if component.get("dtend") else dtstart
            if isinstance(dtend, datetime):
                dtend = dtend.astimezone(timezone.utc)
            else:
                dtend = datetime.combine(dtend, datetime.min.time(), tzinfo=timezone.utc)
            
            uid = str(component.get("uid", ""))
            
            # Check if this event is already approved and verify times
            status = "pending"
            if source_name in approved_events and uid in approved_events[source_name]:
                approved_start, approved_end = approved_events[source_name][uid]
                
                # Get the buffers from cache (no DB hit)
                buffer_before, buffer_after = get_buffer_for_event(uid, source_name, buffers_cache)
                
                # Remove buffers from approved times to get original times
                original_approved_start = approved_start + timedelta(minutes=buffer_before)
                original_approved_end = approved_end - timedelta(minutes=buffer_after)
                
                # Allow a small time difference (1 minute) for timezone/formatting variations
                time_diff = abs((dtstart - original_approved_start).total_seconds()) + abs((dtend - original_approved_end).total_seconds())
                if time_diff > 60:  # More than 1 minute difference
                    status = "time_changed"
                else:
                    status = "approved"
            
            events.append({
                "uid": uid,
                "source": source_name,
                "title": str(component.get("summary", "No Title")),
                "start": dtstart.isoformat(),
                "end": dtend.isoformat(),
                "location": str(component.get("location", "")),
                "description": str(component.get("description", "")),
                "status": status
            })
    except Exception as e:
        print(f"Error parsing calendar for {source_name}: {e}")
    
    return events


def _parse_blocked_calendar(cal_content, source_name):
    """Parse a blocked calendar and return dict of {uid: (start, end)}"""
    events_dict = {}
    try:
        cal = Calendar.from_ical(cal_content)
        
        for component in cal.walk():
            if component.name != "VEVENT":
                continue
            
            uid = str(component.get("uid", ""))
            if uid:
                # Store the start and end times
                dtstart = component.get("dtstart").dt
                if isinstance(dtstart, datetime):
                    dtstart = dtstart.astimezone(timezone.utc)
                else:
                    dtstart = datetime.combine(dtstart, datetime.min.time(), tzinfo=timezone.utc)
                
                dtend = component.get("dtend").dt if component.get("dtend") else dtstart
                if isinstance(dtend, datetime):
                    dtend = dtend.astimezone(timezone.utc)
                else:
                    dtend = datetime.combine(dtend, datetime.min.time(), tzinfo=timezone.utc)
                
                events_dict[uid] = (dtstart, dtend)
    except Exception as e:
        print(f"Error parsing blocked calendar for {source_name}: {e}")
    
    return events_dict


def fetch_and_update_ics():
    """
    Fetch Google ICS from multiple calendars and sync into Event table.
    Also fetch blocked calendars to determine which events are already approved.
    Uses concurrent requests for better performance.
    """
    start_time = time.time()
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(days=SYNC_WINDOW_DAYS)
    
    # Fetch all buffers at once (not per-event)
    buffers_start = time.time()
    buffers_cache = get_all_event_buffers()
    buffers_time = time.time() - buffers_start
    
    # Prepare list of URLs to fetch concurrently
    urls_to_fetch = []
    url_info = {}  # Map URL to metadata
    
    # Add blocked calendars (to check for approved events)
    for source_name in ICS_URLS.keys():
        for other_calendar_name in ICS_URLS.keys():
            if other_calendar_name == source_name:
                continue  # Skip own blocked calendar
            
            blocked_url = BLOCKED_CALENDAR_URLS[other_calendar_name]
            url_id = f"blocked_{source_name}_{other_calendar_name}"
            urls_to_fetch.append(blocked_url)
            url_info[blocked_url] = {
                'type': 'blocked',
                'source': source_name,
                'other_calendar': other_calendar_name,
                'url_id': url_id
            }
    
    # Add main calendar URLs
    for source_name, ics_url in ICS_URLS.items():
        urls_to_fetch.append(ics_url)
        url_info[ics_url] = {
            'type': 'main',
            'source': source_name
        }
    
    # Add work calendar
    urls_to_fetch.append(APPROVED_CALENDAR_URL)
    url_info[APPROVED_CALENDAR_URL] = {
        'type': 'work'
    }
    
    # Fetch all URLs concurrently
    fetch_start = time.time()
    results = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_url = {executor.submit(_fetch_url, url): url for url in urls_to_fetch}
        
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                url, content, error = future.result()
                if error:
                    print(f"Error fetching {url}: {error}")
                    results[url] = None
                else:
                    results[url] = content
            except Exception as e:
                print(f"Exception fetching {url}: {e}")
                results[url] = None
    
    fetch_time = time.time() - fetch_start
    
    # Build approved_events from blocked calendars
    parse_start = time.time()
    approved_events = {}
    for source_name in ICS_URLS.keys():
        approved_events[source_name] = {}
    
    for url, content in results.items():
        if content and url in url_info and url_info[url]['type'] == 'blocked':
            source = url_info[url]['source']
            try:
                blocked_dict = _parse_blocked_calendar(content, source)
                approved_events[source].update(blocked_dict)
            except Exception as e:
                print(f"Error processing blocked calendar: {e}")
    
    # Process main calendars and work calendar
    all_events = []
    
    for url, content in results.items():
        if not content or url not in url_info:
            continue
        
        info = url_info[url]
        
        if info['type'] == 'main':
            events = _parse_calendar_events(content, info['source'], approved_events, now, window_end, buffers_cache)
            all_events.extend(events)
        
        elif info['type'] == 'work':
            try:
                cal = Calendar.from_ical(content)
                
                for component in cal.walk():
                    if component.name != "VEVENT":
                        continue
                    
                    dtstart = component.get("dtstart").dt
                    if isinstance(dtstart, datetime):
                        dtstart = dtstart.astimezone(timezone.utc)
                    else:
                        dtstart = datetime.combine(dtstart, datetime.min.time(), tzinfo=timezone.utc)
                        
                    if dtstart < now or dtstart > window_end:
                        continue
                    
                    dtend = component.get("dtend").dt if component.get("dtend") else dtstart
                    if isinstance(dtend, datetime):
                        dtend = dtend.astimezone(timezone.utc)
                    else:
                        dtend = datetime.combine(dtend, datetime.min.time(), tzinfo=timezone.utc)
                    
                    uid = str(component.get("uid", ""))
                    
                    all_events.append({
                        "uid": uid,
                        "source": "Work",
                        "title": str(component.get("summary", "No Title")),
                        "start": dtstart.isoformat(),
                        "end": dtend.isoformat(),
                        "location": str(component.get("location", "")),
                        "description": str(component.get("description", "")),
                        "status": "approved"
                    })
            except Exception as e:
                print(f"Error fetching work calendar: {e}")
    
    parse_time = time.time() - parse_start
    total_time = time.time() - start_time
    
    print(f"[PERF] fetch_and_update_ics: buffers={buffers_time:.2f}s, network_requests={fetch_time:.2f}s, parsing={parse_time:.2f}s, total={total_time:.2f}s ({len(all_events)} events)")
    
    return all_events


def approve_event(uid, source, start, end, title, description, use_generic_title, use_generic_description, buffer_before, buffer_after):
    """
    Approve an event by creating blocked events in other calendars' blocked calendars.
    
    Args:
        uid: Event UID
        source: Source calendar name (where the event came from)
        start: Event start time (ISO format string)
        end: Event end time (ISO format string)
        title: Original event title
        description: Original event description
        use_generic_title: Whether to use "Busy" as title
        use_generic_description: Whether to use generic description
        buffer_before: Minutes to add before event
        buffer_after: Minutes to add after event
    
    Returns:
        dict with success status and message
    """
    try:
        # Parse times
        start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
        end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))
        
        # Apply buffers
        if buffer_before:
            start_dt = start_dt - timedelta(minutes=buffer_before)
        if buffer_after:
            end_dt = end_dt + timedelta(minutes=buffer_after)
        
        # Determine title and description based on privacy settings
        event_title = "Busy" if use_generic_title else (title or "Event")
        event_description = "Blocked time" if use_generic_description else description
        
        # Send blocked events to all OTHER calendars' blocked calendars
        for calendar_name in ICS_URLS.keys():
            if calendar_name == source:
                # Don't send to own calendar
                continue
            
            # Create blocked event
            cal = Calendar()
            cal.add('prodid', '-//CalManage//EN')
            cal.add('version', '2.0')
            
            event = ICalEvent()
            event.add('uid', uid)
            event.add('dtstamp', datetime.now(timezone.utc))
            event.add('dtstart', start_dt)
            event.add('dtend', end_dt)
            event.add('summary', event_title)
            if event_description:
                event.add('description', event_description)
            event.add('transp', 'OPAQUE')  # Mark as opaque/busy
            
            cal.add_component(event)
            
            # Send to blocked calendar
            blocked_url = BLOCKED_CALENDAR_URLS[calendar_name]
            try:
                response = requests.put(
                    f"{blocked_url}{uid}.ics",
                    data=cal.to_ical(),
                    timeout=10
                )
                if not response.ok:
                    print(f"Warning: Failed to send blocked event to {calendar_name}: {response.status_code}")
            except requests.RequestException as e:
                print(f"Error sending blocked event to {calendar_name}: {e}")
        
        return {
            'success': True,
            'message': f'Event approved and blocked events sent to other calendars'
        }
    
    except Exception as e:
        print(f"Error in approve_event: {e}")
        return {
            'success': False,
            'message': f'Error approving event: {str(e)}'
        }


def remove_approval(uid):
    """
    Remove an event from all blocked calendars.
    
    Args:
        uid: Event UID to remove
    
    Returns:
        dict with success status and message
    """
    try:
        # Remove from all blocked calendars
        for calendar_name in BLOCKED_CALENDAR_URLS.keys():
            blocked_url = BLOCKED_CALENDAR_URLS[calendar_name]
            try:
                response = requests.delete(
                    f"{blocked_url}{uid}.ics",
                    timeout=10
                )
                if not response.ok and response.status_code != 404:
                    print(f"Warning: Failed to remove event from {calendar_name}: {response.status_code}")
            except requests.RequestException as e:
                print(f"Error removing event from {calendar_name}: {e}")
        
        return {
            'success': True,
            'message': f'Event removed from all blocked calendars'
        }
    
    except Exception as e:
        print(f"Error in remove_approval: {e}")
        return {
            'success': False,
            'message': f'Error removing approval: {str(e)}'
        }
