import os
from datetime import datetime, timedelta

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request


class GoogleCalendarService:
    def __init__(self, mixer):
        self.mixer = mixer

    def get_credentials(self):
        if not self.mixer.google_access_token:
            return None

        creds = Credentials(
            token=self.mixer.google_access_token,
            refresh_token=self.mixer.google_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ.get("GOOGLE_CLIENT_ID"),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
            scopes=["https://www.googleapis.com/auth/calendar.events"],
        )

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self.mixer.google_access_token = creds.token
                self.mixer.google_refresh_token = creds.refresh_token or self.mixer.google_refresh_token
                self.mixer.google_token_expiry = datetime.utcnow() + timedelta(minutes=55)
            except Exception as exc:
                raise RuntimeError(f"Google token refresh failed: {exc}")

        return creds

    def get_service(self):
        creds = self.get_credentials()
        if not creds:
            raise RuntimeError("Google Calendar is not connected")
        return build("calendar", "v3", credentials=creds)

    def list_events(self, start_dt, end_dt):
        service = self.get_service()
        return service.events().list(
            calendarId=self.mixer.google_calendar_id or "primary",
            timeMin=start_dt.isoformat() + "Z",
            timeMax=end_dt.isoformat() + "Z",
            singleEvents=True,
            orderBy="startTime",
        ).execute()

    def create_event(self, reservation):
        service = self.get_service()
        start_dt = datetime.combine(reservation.reservation_date, datetime.strptime(reservation.start_time, "%H:%M").time())
        end_dt = datetime.combine(reservation.reservation_date, datetime.strptime(reservation.end_time, "%H:%M").time())

        event = {
            "summary": f"Session — {reservation.client_name}",
            "description": f"Service: {reservation.service}\nContact: {reservation.client_contact}\nMixer: {self.mixer.name}",
            "start": {"dateTime": start_dt.isoformat()},
            "end": {"dateTime": end_dt.isoformat()},
        }

        created = service.events().insert(
            calendarId=self.mixer.google_calendar_id or "primary",
            body=event,
        ).execute()

        return created.get("id")

    def update_event(self, reservation):
        if not reservation.google_calendar_event_id:
            return None
        service = self.get_service()
        start_dt = datetime.combine(reservation.reservation_date, datetime.strptime(reservation.start_time, "%H:%M").time())
        end_dt = datetime.combine(reservation.reservation_date, datetime.strptime(reservation.end_time, "%H:%M").time())

        event = service.events().get(
            calendarId=self.mixer.google_calendar_id or "primary",
            eventId=reservation.google_calendar_event_id,
        ).execute()

        event["summary"] = f"Session — {reservation.client_name}"
        event["description"] = f"Service: {reservation.service}\nContact: {reservation.client_contact}\nMixer: {self.mixer.name}"
        event["start"] = {"dateTime": start_dt.isoformat()}
        event["end"] = {"dateTime": end_dt.isoformat()}

        return service.events().update(
            calendarId=self.mixer.google_calendar_id or "primary",
            eventId=reservation.google_calendar_event_id,
            body=event,
        ).execute()

    def delete_event(self, reservation):
        if not reservation.google_calendar_event_id:
            return True
        service = self.get_service()
        try:
            service.events().delete(
                calendarId=self.mixer.google_calendar_id or "primary",
                eventId=reservation.google_calendar_event_id,
            ).execute()
            return True
        except Exception:
            return False
