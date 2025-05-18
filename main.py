from dataclasses import dataclass
import datetime
import os.path
import re
from time import sleep
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/calendar"]
MY_CALENDARS = [
    "primary",
    "another@group.calendar.google.com"
]
TARGET_CALENDER = "target@group.calendar.google.com"
REVERSE_TARGET_CALENDER = "primary"



@dataclass
class CalendarTimeStamp:
    date: Optional[datetime.date] = None
    date_time: Optional[datetime.datetime] = None

    def get_google_dict(self):
        if self.date:
            return {"date": self.date, "dateTime": None}
        elif self.date_time:
            return {"dateTime": self.date_time, "date": None}

    def __eq__(self, other):
        if not isinstance(other, CalendarTimeStamp):
            return NotImplemented
        return (
            self.date == other.date and self.date_time == other.date_time
        )
    
    def __str__(self):
        if self.date:
            return self.date
        elif self.date_time:
            return self.date_time
        else:
            return "None"

@dataclass
class CalendarEvent:
    id: str
    start: CalendarTimeStamp
    end: CalendarTimeStamp
    originalID: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None

    def __hash__(self):
        return hash(self.id)
    
    def __eq__(self, other):
        if not isinstance(other, CalendarEvent):
            return NotImplemented
        return (
            self.id == other.id
            and self.start == other.start
            and self.end == other.end
            and self.summary == other.summary
            and self.description == other.description
        )
    
    def __str__(self):
        return f"Event ID: {self.id}, Start: {self.start}, End: {self.end}"


def get_calender_client():
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    service = build("calendar", "v3", credentials=creds)
    return service

def get_events(client, calendar_id):
    my_events = []

    now = datetime.datetime.now(tz=datetime.timezone.utc)
    events_result = (
        client.events()
        .list(
            calendarId=calendar_id,
            timeMin=now.isoformat(),
            timeMax=(now + datetime.timedelta(days=90)).isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    events = events_result.get("items", [])
    return events

def create_calender_event_object(event, keep_info=False):
    # Create a CalendarEvent object from the event dat
    id = event["id"]
    originalID = event["id"]

    match = re.search("fogTimeID: (.*)$", event.get("description", ""))
    if match:
        id = match.group(1)

    summary = None
    description = None
    if keep_info:
        summary = event["summary"]
        description = event.get("description", "")
    return CalendarEvent(
                id=id,
                originalID=originalID,
                start=CalendarTimeStamp(
                    date=event["start"].get("date"),
                    date_time=event["start"].get("dateTime"),
                ),
                end=CalendarTimeStamp(
                    date=event["end"].get("date"),
                    date_time=event["end"].get("dateTime"),
                ),
                summary=summary,
                description=description,
            )

def get_calendar_events(client, calendar_id, keep_infos=False):
    my_events = {}
    events = get_events(client, calendar_id)
    if not keep_infos:
        # Filter out events that are reverse syncs
        events = [event for event in events if "fogTimeID" not in event.get("description", "")]
    events = [create_calender_event_object(event, keep_infos) for event in events]
    for event in events:
        my_events[event.id] = event
    return my_events

def get_blockers(client, calendar_id):
    events = get_events(client, calendar_id)
    blockers = {}

    for event in events:
        if event["summary"] == "FogTime Blocker":
            my_blocker = create_calender_event_object(event)
            blockers[my_blocker.id] = my_blocker
    return blockers

def update_blocker(client, calendar_id, my_event, blocker):
    # Update the blocker event in the calendar
    event = {}
    event["start"] = my_event.start.get_google_dict()
    event["end"] = my_event.end.get_google_dict()
    if my_event.summary:
        event["summary"] = my_event.summary
    if my_event.description:
        event["description"] = my_event.description
    client.events().patch(
        calendarId=calendar_id, eventId=blocker.originalID, body=event
    ).execute()
    print("Updating Event" + str(blocker))

def create_event(client, calendar_id, blocker):
    # Create a new blocker event in the calendar
    print("Creating Event" + str(blocker))

    event = {
        "summary": blocker.summary,
        "description": blocker.description,
        "start": blocker.start.get_google_dict(),
        "end": blocker.end.get_google_dict(),
    }
    client.events().insert(
        calendarId=calendar_id, body=event
    ).execute()

def sync_blockers(client, calendar_id, my_events, blockers):
    for key, my_event in my_events.items():
        if key in blockers:
            if my_event != blockers[key]:
                my_event.description = f"This is a blocker event created by fogTime.\nfogTimeID: {my_event.id}"
                update_blocker(client, calendar_id, my_event, blockers[key])
            else:
                print("Event already exists" + str(my_event))
        else:
            my_event.summary = "FogTime Blocker"
            my_event.description = f"This is a blocker event created by fogTime.\nfogTimeID: {my_event.id}"
            create_event(client, calendar_id, my_event)
    
    for key, blocker in blockers.items():
        if key not in my_events:
            print("Deleting Event" + str(blockers[key]))
            client.events().delete(calendarId=calendar_id, eventId=blocker.originalID).execute()

def get_not_blocker(client, calendar_id):
    events = get_calendar_events(client, calendar_id, keep_infos=True)
    not_blockers = {}

    for key, event in events.items():
        if event.summary != "FogTime Blocker":
            not_blockers[key] = event
    return not_blockers

def sync_reverse(client, source_calendar_id, target_calendar_id):
    not_blockers = get_not_blocker(client, source_calendar_id)
    for event in not_blockers.values():
        event.description = f"{event.description}\nfogTimeID: {event.id}"
    original_appointments = get_calendar_events(client, target_calendar_id, keep_infos=True)
    for key, event in not_blockers.items():
        if key in original_appointments:
            if event != original_appointments[key]:
                update_blocker(client, target_calendar_id, event, original_appointments[key])
            else:
                print("Event already exists" + str(event))
        else:
            create_event(client, target_calendar_id, event)
    for key, event in original_appointments.items():
        if key not in not_blockers and "fogTimeID" in event.description:
            print("Deleting Event" + str(event))
            client.events().delete(calendarId=target_calendar_id, eventId=event.originalID).execute()
    

        
def main():
    client = get_calender_client()
    print("Welcome to fogTime!")
    while True:
        # Your application logic goes here
        try:
            my_events = {}
            for calendar_id in MY_CALENDARS:
                my_events.update(get_calendar_events(client, calendar_id))
            existing_blockers = get_blockers(client, TARGET_CALENDER)
            sync_blockers(client, TARGET_CALENDER, my_events, existing_blockers)
            print("Syncing Reverse")
            sync_reverse(client, TARGET_CALENDER, REVERSE_TARGET_CALENDER)
        # We catch all exceptions to avoid the program crashing
        except Exception as e:
            print("An error occurred, retry next iteration: " + str(e))
        sleep(300)



if __name__ == "__main__":
    main()