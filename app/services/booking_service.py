from datetime import datetime
from flask import current_app

from app.extensions import db
from app.models import Reservation, Mixer


def overlaps(start_a, end_a, start_b, end_b):
    return start_a < end_b and start_b < end_a


def validate_no_conflict(mixer_id, reservation_date, start_time, end_time, exclude_reservation_id=None):
    start_dt = datetime.strptime(f"{reservation_date} {start_time}", "%Y-%m-%d %H:%M")
    end_dt = datetime.strptime(f"{reservation_date} {end_time}", "%Y-%m-%d %H:%M")

    query = Reservation.query.filter_by(mixer_id=mixer_id, status="confirmed").filter(
        Reservation.reservation_date == reservation_date
    )

    if exclude_reservation_id:
        query = query.filter(Reservation.id != exclude_reservation_id)

    for booking in query.all():
        existing_start = datetime.strptime(f"{booking.reservation_date} {booking.start_time}", "%Y-%m-%d %H:%M")
        existing_end = datetime.strptime(f"{booking.reservation_date} {booking.end_time}", "%Y-%m-%d %H:%M")
        if overlaps(start_dt.timestamp(), end_dt.timestamp(), existing_start.timestamp(), existing_end.timestamp()):
            return False, f"Conflict with existing reservation for {booking.mixer.name}"

    return True, "ok"


def create_booking_from_payload(payload):
    mixer = Mixer.query.filter_by(name=payload["mixer"]).first()
    if not mixer:
        return None, "Mixer not found"

    valid, message = validate_no_conflict(
        mixer.id,
        payload["booking_date"],
        payload["start_time"],
        payload["end_time"],
    )
    if not valid:
        return None, message

    booking = Reservation(
        client_name=payload["artist"].strip(),
        client_contact=payload["contact"].strip(),
        service=payload["service"].strip(),
        mixer_id=mixer.id,
        reservation_date=datetime.strptime(payload["booking_date"], "%Y-%m-%d").date(),
        start_time=payload["start_time"],
        end_time=payload["end_time"],
        status="confirmed",
        google_sync_status="pending",
    )
    db.session.add(booking)
    db.session.commit()
    return booking, None
