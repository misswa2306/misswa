import os

from flask import current_app
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from app.extensions import db
from app.services.google_calendar_service import GOOGLE_CALENDAR_SCOPES, GoogleCalendarService


class MixerGoogleCalendarService:
    def __init__(self, mixer):
        self.mixer = mixer
        self.shared_service = GoogleCalendarService(mixer=mixer)

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
        access_token = self.mixer.google_access_token
        refresh_token = self.mixer.google_refresh_token

        if not access_token and not refresh_token:
            return self.shared_service.get_credentials()

        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ.get("GOOGLE_CLIENT_ID"),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
            scopes=GOOGLE_CALENDAR_SCOPES,
            expiry=self.mixer.google_token_expiry,
        )

        if (not creds.token or creds.expired) and creds.refresh_token:
            try:
                creds.refresh(Request())
                self.mixer.google_access_token = creds.token
                self.mixer.google_refresh_token = creds.refresh_token or self.mixer.google_refresh_token
                self.mixer.google_token_expiry = creds.expiry
                db.session.commit()
            except Exception:
                raise

        if self.mixer.google_access_token != creds.token:
            self.mixer.google_access_token = creds.token
            self.mixer.google_token_expiry = creds.expiry
            db.session.commit()

        return creds

    def get_service(self):
        try:
            return self.shared_service.get_service()
        except Exception:
            if self.mixer and (self.mixer.google_access_token or self.mixer.google_refresh_token):
                creds = self.get_credentials()
                if creds:
                    return GoogleCalendarService(mixer=self.mixer).get_service()
            raise

    def calendar_id(self):
        return self.shared_service.calendar_id()

    @staticmethod
    def window_datetimes(reservation_date, start_time, end_time):
        return GoogleCalendarService.window_datetimes(reservation_date, start_time, end_time)

    @classmethod
    def reservation_datetimes(cls, reservation):
        return GoogleCalendarService.reservation_datetimes(reservation)

    def is_available_for_window(self, reservation_date, start_time, end_time):
        try:
            start_dt, end_dt = self.window_datetimes(reservation_date, start_time, end_time)
            response = self.get_service().freebusy().query(body={
                "timeMin": start_dt.isoformat(),
                "timeMax": end_dt.isoformat(),
                "timeZone": "America/Montreal",
                "items": [{"id": self.calendar_id()}],
            }).execute()
            busy = response.get("calendars", {}).get(self.calendar_id(), {}).get("busy", [])
            return not busy
        except Exception as exc:
            self.log_google_error("freebusy query", exc)
            raise

    def is_available(self, reservation):
        return self.is_available_for_window(
            reservation.reservation_date,
            reservation.start_time,
            reservation.end_time,
        )

    def create_event(self, reservation, mixer=None):
        try:
            effective_mixer = mixer or self.mixer
            service = self.get_service()
            start_dt, end_dt = self.reservation_datetimes(reservation)
            body = {
                "summary": f"RESTLESS STUDIO — {reservation.client_name} (Mixer: {effective_mixer.name})",
                "description": (
                    f"Service: {reservation.service}\n"
                    f"Contact: {reservation.client_contact}\n"
                    f"Mixer attribué: {effective_mixer.name}"
                ),
                "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Montreal"},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Montreal"},
            }
            created = service.events().insert(calendarId=self.calendar_id(), body=body).execute()
            event_id = created.get("id")
            if not event_id:
                raise RuntimeError("Google Calendar API returned no event ID")
            return event_id
        except Exception as exc:
            self.log_google_error("event creation", exc)
            raise

    def update_event(self, reservation, mixer=None):
        if not reservation.google_calendar_event_id:
            return None
        try:
            effective_mixer = mixer or self.mixer
            service = self.get_service()
            start_dt, end_dt = self.reservation_datetimes(reservation)
            event = service.events().get(
                calendarId=self.calendar_id(),
                eventId=reservation.google_calendar_event_id,
            ).execute()
            event["summary"] = f"RESTLESS STUDIO — {reservation.client_name} (Mixer: {effective_mixer.name})"
            event["description"] = (
                f"Service: {reservation.service}\n"
                f"Contact: {reservation.client_contact}\n"
                f"Mixer attribué: {effective_mixer.name}"
            )
            event["start"] = {"dateTime": start_dt.isoformat(), "timeZone": "America/Montreal"}
            event["end"] = {"dateTime": end_dt.isoformat(), "timeZone": "America/Montreal"}
            return service.events().update(
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
            return self.get_service().events().delete(
                calendarId=self.calendar_id(),
                eventId=reservation.google_calendar_event_id,
            ).execute() or True
        except Exception as exc:
            self.log_google_error("event deletion", exc)
            return False
