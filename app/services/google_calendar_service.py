import os
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import current_app
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.extensions import db
from app.models import GoogleCalendarAccount

MONTREAL_TZ = ZoneInfo("America/Montreal")
GOOGLE_CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
]


class GoogleCalendarService:
    @staticmethod
    def get_primary_account_email():
        return (
            current_app.config.get("GOOGLE_CALENDAR_ACCOUNT_EMAIL", "")
            or os.environ.get("GOOGLE_CALENDAR_ACCOUNT_EMAIL", "")
            or os.environ.get("GOOGLE_MASTER_ACCOUNT_EMAIL", "")
            or ""
        ).strip()

    @staticmethod
    def get_shared_account():
        account_email = GoogleCalendarService.get_primary_account_email()
        if account_email:
            account = GoogleCalendarAccount.query.filter_by(account_email=account_email).first()
            if account:
                return account
            account = GoogleCalendarAccount(
                account_email=account_email,
                calendar_id="primary",
            )
            db.session.add(account)
            db.session.commit()
            return account

        account = GoogleCalendarAccount.query.order_by(GoogleCalendarAccount.id.asc()).first()
        if account:
            return account

        fallback_email = os.environ.get("GOOGLE_MASTER_ACCOUNT_EMAIL") or "studio-admin@localhost"
        account = GoogleCalendarAccount(account_email=fallback_email, calendar_id="primary")
        db.session.add(account)
        db.session.commit()
        return account

    def __init__(self, mixer=None, account=None):
        self.mixer = mixer
        self.account = account or self.get_shared_account()

    def calendar_id(self):
        return self.account.calendar_id or "primary"

    def get_credentials(self):
        access_token = self.account.access_token or os.environ.get("GOOGLE_ACCESS_TOKEN")
        refresh_token = self.account.refresh_token or os.environ.get("GOOGLE_REFRESH_TOKEN")
        if not access_token and not refresh_token:
            current_app.logger.warning("Google credentials unavailable: shared account or environment tokens missing")
            return None

        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ.get("GOOGLE_CLIENT_ID"),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
            scopes=GOOGLE_CALENDAR_SCOPES,
            expiry=self.account.token_expiry,
        )

        if (not creds.token or creds.expired) and creds.refresh_token:
            try:
                current_app.logger.info("Refreshing Google access token for shared calendar account")
                creds.refresh(Request())
                self.account.access_token = creds.token
                self.account.refresh_token = creds.refresh_token or self.account.refresh_token
                self.account.token_expiry = creds.expiry
                db.session.commit()
                current_app.logger.info("Google access token refreshed successfully")
            except Exception as exc:
                raise RuntimeError(f"Google token refresh failed: {exc}")

        if self.account.access_token != creds.token:
            self.account.access_token = creds.token
            self.account.token_expiry = creds.expiry
            db.session.commit()

        return creds

    def get_service(self):
        creds = self.get_credentials()
        if not creds:
            raise RuntimeError("Google Calendar is not connected")
        current_app.logger.info("Google Calendar API client initialized for calendar_id=%s", self.calendar_id())
        return build("calendar", "v3", credentials=creds)

    @staticmethod
    def window_datetimes(reservation_date, start_time, end_time):
        if isinstance(reservation_date, str):
            reservation_date = datetime.strptime(reservation_date, "%Y-%m-%d").date()

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
        response = self.get_service().freebusy().query(body={
            "timeMin": start_dt.isoformat(),
            "timeMax": end_dt.isoformat(),
            "timeZone": "America/Montreal",
            "items": [{"id": self.calendar_id()}],
        }).execute()
        busy = response.get("calendars", {}).get(self.calendar_id(), {}).get("busy", [])
        return not busy

    def is_available(self, reservation):
        return self.is_available_for_window(
            reservation.reservation_date,
            reservation.start_time,
            reservation.end_time,
        )

    def create_event(self, reservation, mixer=None):
        service = self.get_service()
        start_dt, end_dt = self.reservation_datetimes(reservation)
        effective_mixer = mixer or self.mixer or reservation.mixer
        event = {
            "summary": f"RESTLESS STUDIO — {reservation.client_instagram} (Mixer: {effective_mixer.name})",
            "description": (
                f"📸 Client Instagram : {reservation.client_instagram}\n"
                f"🎛️ Mixer attribué : {effective_mixer.name}\n"
                f"🎙️ Service : {reservation.service}\n"
                f"📅 Date & Heure : {reservation.reservation_date} ({reservation.start_time} - {reservation.end_time})"
            ),
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Montreal"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Montreal"},
        }

        current_app.logger.info(
            "Creating Google Calendar event: booking_id=%s mixer=%s calendar_id=%s",
            reservation.id,
            effective_mixer.name,
            self.calendar_id(),
        )
        created = service.events().insert(
            calendarId=self.calendar_id(),
            body=event,
        ).execute()

        event_id = created.get("id")
        if not event_id:
            raise RuntimeError("Google Calendar API returned no event ID")
        return event_id

    def update_event(self, reservation, mixer=None):
        if not reservation.google_calendar_event_id:
            return None
        service = self.get_service()
        start_dt, end_dt = self.reservation_datetimes(reservation)
        effective_mixer = mixer or self.mixer or reservation.mixer

        event = service.events().get(
            calendarId=self.calendar_id(),
            eventId=reservation.google_calendar_event_id,
        ).execute()

        event["summary"] = f"RESTLESS STUDIO — {reservation.client_instagram} (Mixer: {effective_mixer.name})"
        event["description"] = (
            f"📸 Client Instagram : {reservation.client_instagram}\n"
            f"🎛️ Mixer attribué : {effective_mixer.name}\n"
            f"🎙️ Service : {reservation.service}\n"
            f"📅 Date & Heure : {reservation.reservation_date} ({reservation.start_time} - {reservation.end_time})"
        )
        event["start"] = {"dateTime": start_dt.isoformat(), "timeZone": "America/Montreal"}
        event["end"] = {"dateTime": end_dt.isoformat(), "timeZone": "America/Montreal"}

        return service.events().update(
            calendarId=self.calendar_id(),
            eventId=reservation.google_calendar_event_id,
            body=event,
        ).execute()

    def delete_event(self, reservation):
        if not reservation.google_calendar_event_id:
            return True
        service = self.get_service()
        try:
            service.events().delete(
                calendarId=self.calendar_id(),
                eventId=reservation.google_calendar_event_id,
            ).execute()
            return True
        except Exception:
            return False

    def list_events(self, start_dt, end_dt):
        service = self.get_service()
        return service.events().list(
            calendarId=self.calendar_id(),
            timeMin=start_dt.isoformat() + "Z",
            timeMax=end_dt.isoformat() + "Z",
            singleEvents=True,
            orderBy="startTime",
        ).execute()
