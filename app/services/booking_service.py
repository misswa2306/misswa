from datetime import datetime
from zoneinfo import ZoneInfo

from flask import current_app

from app.extensions import db
from app.models import Reservation, Mixer

MONTREAL_TZ = ZoneInfo("America/Montreal")


def overlaps(start_a, end_a, start_b, end_b):
    return start_a < end_b and start_b < end_a


def get_montreal_now():
    return datetime.now(MONTREAL_TZ)


def parse_local_datetime(reservation_date, time_value):
    return datetime.strptime(f"{reservation_date} {time_value}", "%Y-%m-%d %H:%M").replace(tzinfo=MONTREAL_TZ)


def validate_no_conflict(mixer_id, reservation_date, start_time, end_time, exclude_reservation_id=None):
    start_dt = parse_local_datetime(reservation_date, start_time)
    end_dt = parse_local_datetime(reservation_date, end_time)
    if end_dt <= start_dt:
        return False, "End time must be after start time"

    now_mt = get_montreal_now()
    selected_date = datetime.strptime(reservation_date, "%Y-%m-%d").date()
    if selected_date < now_mt.date():
        return False, "Reservation date cannot be in the past"
    if selected_date == now_mt.date() and start_dt < now_mt:
        return False, "Reservation start time cannot be in the past"

    query = Reservation.query.filter(
        Reservation.mixer_id == mixer_id,
        Reservation.status != "cancelled",
    ).filter(
        Reservation.reservation_date == selected_date
    )

    if exclude_reservation_id:
        query = query.filter(Reservation.id != exclude_reservation_id)

    for booking in query.all():
        existing_start = parse_local_datetime(booking.reservation_date.isoformat(), booking.start_time)
        existing_end = parse_local_datetime(booking.reservation_date.isoformat(), booking.end_time)
        if overlaps(start_dt, end_dt, existing_start, existing_end):
            return False, f"Conflict with existing reservation for {booking.mixer.name}"

    return True, "ok"


def create_booking_from_payload(payload):
    mixer_name = (payload.get("mixer") or "").strip()
    mixer = Mixer.query.filter(Mixer.name.ilike(mixer_name)).first()
    if not mixer:
        return None, "Mixer not found"

    with db.session.begin_nested():
        valid, message = validate_no_conflict(
            mixer.id,
            payload["booking_date"],
            payload["start_time"],
            payload["end_time"],
        )
        if not valid:
            return None, message

        instagram = payload["client_instagram"].strip()
        if not instagram.startswith("@"):
            instagram = f"@{instagram}"

        booking = Reservation(
            client_name=payload["artist"].strip(),
            client_instagram=instagram,
            service=payload["service"].strip(),
            mixer_id=mixer.id,
            reservation_date=datetime.strptime(payload["booking_date"], "%Y-%m-%d").date(),
            start_time=payload["start_time"],
            end_time=payload["end_time"],
            status="confirmed",
            google_sync_status="pending",
        )
        db.session.add(booking)
        db.session.flush()
        return booking, None
