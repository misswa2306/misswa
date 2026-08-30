import os
from datetime import datetime

from flask import current_app
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

from app.extensions import db
from app.models import GoogleCalendarAccount


class GoogleCalendarService:
    @staticmethod
    def get_shared_account():
        account_email = current_app.config.get("GOOGLE_CALENDAR_ACCOUNT_EMAIL", "").strip()
        if not account_email:
            raise RuntimeError("GOOGLE_CALENDAR_ACCOUNT_EMAIL is not configured")
        return GoogleCalendarAccount.query.filter_by(account_email=account_email).first()

    def __init__(self, mixer, account=None):
        self.mixer = mixer
        self.account = account or self.get_shared_account()

    def get_credentials(self):
        if not self.account or not self.account.access_token:
            current_app.logger.warning("Google credentials unavailable: shared account or access token missing")
            return None

        creds = Credentials(
            token=self.account.access_token,
            refresh_token=self.account.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ.get("GOOGLE_CLIENT_ID"),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
            scopes=["https://www.googleapis.com/auth/calendar.events"],
            expiry=self.account.token_expiry,
        )

        if creds.expired and creds.refresh_token:
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

        return creds

    def get_service(self):
        creds = self.get_credentials()
        if not creds:
            raise RuntimeError("Google Calendar is not connected")
        current_app.logger.info("Google Calendar API client initialized for calendar_id=%s", self.account.calendar_id or "primary")
        return build("calendar", "v3", credentials=creds)

    def list_events(self, start_dt, end_dt):
        service = self.get_service()
        return service.events().list(
            calendarId=self.account.calendar_id or "primary",
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
            "summary": f"RESTLESS STUDIO — {reservation.client_name}",
            "description": f"Service: {reservation.service}\nContact: {reservation.client_contact}\nMixer: {self.mixer.name}",
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": os.environ.get("GOOGLE_TIME_ZONE", "America/Montreal"),
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": os.environ.get("GOOGLE_TIME_ZONE", "America/Montreal"),
            },
        }

        current_app.logger.info(
            "Creating Google Calendar event: booking_id=%s mixer=%s calendar_id=%s",
            reservation.id,
            self.mixer.name,
            self.account.calendar_id or "primary",
        )
        created = service.events().insert(
            calendarId=self.account.calendar_id or "primary",
            body=event,
        ).execute()

        event_id = created.get("id")
        if not event_id:
            raise RuntimeError("Google Calendar API returned no event ID")
        current_app.logger.info("Google Calendar event created: booking_id=%s event_id_present=%s", reservation.id, True)
        return event_id

    def update_event(self, reservation):
        if not reservation.google_calendar_event_id:
            return None
        service = self.get_service()
        start_dt = datetime.combine(reservation.reservation_date, datetime.strptime(reservation.start_time, "%H:%M").time())
        end_dt = datetime.combine(reservation.reservation_date, datetime.strptime(reservation.end_time, "%H:%M").time())

        event = service.events().get(
            calendarId=self.account.calendar_id or "primary",
            eventId=reservation.google_calendar_event_id,
        ).execute()

        event["summary"] = f"RESTLESS STUDIO — {reservation.client_name}"
        event["description"] = f"Service: {reservation.service}\nContact: {reservation.client_contact}\nMixer: {self.mixer.name}"
        event["start"] = {
            "dateTime": start_dt.isoformat(),
            "timeZone": os.environ.get("GOOGLE_TIME_ZONE", "America/Montreal"),
        }
        event["end"] = {
            "dateTime": end_dt.isoformat(),
            "timeZone": os.environ.get("GOOGLE_TIME_ZONE", "America/Montreal"),
        }

        return service.events().update(
            calendarId=self.account.calendar_id or "primary",
            eventId=reservation.google_calendar_event_id,
            body=event,
        ).execute()

    def delete_event(self, reservation):
        if not reservation.google_calendar_event_id:
            return True
        service = self.get_service()
        try:
            service.events().delete(
                calendarId=self.account.calendar_id or "primary",
                eventId=reservation.google_calendar_event_id,
            ).execute()
            return True
        except Exception:
            return False
