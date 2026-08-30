import os
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import current_app
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.extensions import db


MONTREAL_TZ = ZoneInfo("America/Montreal")
GOOGLE_CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
]


class MixerGoogleCalendarService:
    def __init__(self, mixer):
        self.mixer = mixer

    def log_google_error(self, operation, exc):
        current_app.logger.error(
            "Google Calendar %s failed: mixer_id=%s mixer=%s calendar_id=%s error_type=%s error=%s",
            operation,
            self.mixer.id,
            self.mixer.name,
            self.calendar_id(),
            type(exc).__name__,
            str(exc),
            exc_info=True,
        )

    def get_credentials(self):
        if not self.mixer.google_access_token and not self.mixer.google_refresh_token:
            return None

        credentials = Credentials(
            token=self.mixer.google_access_token,
            refresh_token=self.mixer.google_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=current_app.config.get("GOOGLE_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID"),
            client_secret=current_app.config.get("GOOGLE_CLIENT_SECRET") or os.environ.get("GOOGLE_CLIENT_SECRET"),
            scopes=GOOGLE_CALENDAR_SCOPES,
            expiry=self.mixer.google_token_expiry,
        )

        if credentials.refresh_token and (not self.mixer.google_access_token or credentials.expired):
            try:
                current_app.logger.info(
                    "Refreshing Google access token: mixer_id=%s mixer=%s",
                    self.mixer.id,
                    self.mixer.name,
                )
                credentials.refresh(Request())
            except Exception as exc:
                self.log_google_error("token refresh", exc)
                return None
            self.mixer.google_access_token = credentials.token
            self.mixer.google_refresh_token = credentials.refresh_token or self.mixer.google_refresh_token
            self.mixer.google_token_expiry = credentials.expiry
            db.session.commit()

        return credentials

    def get_service(self):
        try:
            credentials = self.get_credentials()
            if not credentials:
                raise RuntimeError(f"Google Calendar is not connected for mixer {self.mixer.name}")
            return build("calendar", "v3", credentials=credentials)
        except Exception as exc:
            self.log_google_error("client initialization", exc)
            raise

    def calendar_id(self):
        return self.mixer.google_calendar_id or "primary"

    @staticmethod
    def window_datetimes(reservation_date, start_time, end_time):
        start = datetime.combine(
            reservation_date,
            datetime.strptime(start_time, "%H:%M").time(),
            tzinfo=MONTREAL_TZ,
        )
        end = datetime.combine(
            reservation_date,
            datetime.strptime(end_time, "%H:%M").time(),
            tzinfo=MONTREAL_TZ,
        )
        return start, end

    @classmethod
    def reservation_datetimes(cls, reservation):
        return cls.window_datetimes(
            reservation.reservation_date,
            reservation.start_time,
            reservation.end_time,
        )

    def is_available_for_window(self, reservation_date, start_time, end_time):
        start_dt, end_dt = self.window_datetimes(reservation_date, start_time, end_time)
        try:
            response = self.get_service().freebusy().query(body={
                "timeMin": start_dt.isoformat(),
                "timeMax": end_dt.isoformat(),
                "timeZone": "America/Montreal",
                "items": [{"id": self.calendar_id()}],
            }).execute()
        except Exception as exc:
            self.log_google_error("freebusy query", exc)
            raise
        busy = response.get("calendars", {}).get(self.calendar_id(), {}).get("busy", [])
        return not busy

    def is_available(self, reservation):
        return self.is_available_for_window(
            reservation.reservation_date,
            reservation.start_time,
            reservation.end_time,
        )

    def create_event(self, reservation):
        start_dt, end_dt = self.reservation_datetimes(reservation)
        event = {
            "summary": f"RESTLESS STUDIO - {reservation.client_name}",
            "description": f"Service: {reservation.service}\nContact: {reservation.client_contact}\nMixer: {self.mixer.name}",
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Montreal"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Montreal"},
        }
        try:
            created = self.get_service().events().insert(
                calendarId=self.calendar_id(),
                body=event,
            ).execute()
        except Exception as exc:
            self.log_google_error("event creation", exc)
            raise
        event_id = created.get("id")
        if not event_id:
            raise RuntimeError("Google Calendar returned no event ID")
        return event_id

    def update_event(self, reservation):
        if not reservation.google_calendar_event_id:
            return None
        start_dt, end_dt = self.reservation_datetimes(reservation)
        try:
            event = self.get_service().events().get(
                calendarId=self.calendar_id(),
                eventId=reservation.google_calendar_event_id,
            ).execute()
            event.update({
                "summary": f"RESTLESS STUDIO - {reservation.client_name}",
                "description": f"Service: {reservation.service}\nContact: {reservation.client_contact}\nMixer: {self.mixer.name}",
                "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Montreal"},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Montreal"},
            })
            return self.get_service().events().update(
                calendarId=self.calendar_id(),
                eventId=reservation.google_calendar_event_id,
                body=event,
            ).execute()
        except Exception as exc:
            self.log_google_error("event update", exc)
            raise

    def delete_event(self, reservation):
        if not reservation.google_calendar_event_id:
            return True
        try:
            self.get_service().events().delete(
                calendarId=self.calendar_id(),
                eventId=reservation.google_calendar_event_id,
            ).execute()
            return True
        except Exception as exc:
            self.log_google_error("event deletion", exc)
            return False