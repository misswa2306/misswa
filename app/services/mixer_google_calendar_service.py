from flask import current_app

from app.services.google_calendar_service import GoogleCalendarService


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
        return self.shared_service.get_credentials()

    def get_service(self):
        return self.shared_service.get_service()

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
            return self.shared_service.is_available_for_window(reservation_date, start_time, end_time)
        except Exception as exc:
            self.log_google_error("freebusy query", exc)
            raise

    def is_available(self, reservation):
        return self.is_available_for_window(
            reservation.reservation_date,
            reservation.start_time,
            reservation.end_time,
        )

    def create_event(self, reservation):
        try:
            return self.shared_service.create_event(reservation, mixer=self.mixer)
        except Exception as exc:
            self.log_google_error("event creation", exc)
            raise

    def update_event(self, reservation):
        if not reservation.google_calendar_event_id:
            return None
        try:
            return self.shared_service.update_event(reservation, mixer=self.mixer)
        except Exception as exc:
            self.log_google_error("event update", exc)
            raise

    def delete_event(self, reservation):
        if not reservation.google_calendar_event_id:
            return True
        try:
            return self.shared_service.delete_event(reservation)
        except Exception as exc:
            self.log_google_error("event deletion", exc)
            return False