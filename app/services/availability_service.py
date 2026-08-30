from datetime import datetime

from app.models import Reservation


def has_booking_conflict(mixer_id, reservation_date, start_time, end_time, exclude_id=None):
    start_dt = datetime.strptime(f"{reservation_date} {start_time}", "%Y-%m-%d %H:%M")
    end_dt = datetime.strptime(f"{reservation_date} {end_time}", "%Y-%m-%d %H:%M")

    query = Reservation.query.filter_by(mixer_id=mixer_id, status="confirmed")
    if exclude_id is not None:
        query = query.filter(Reservation.id != exclude_id)

    for booking in query.all():
        booking_start = datetime.strptime(f"{booking.reservation_date} {booking.start_time}", "%Y-%m-%d %H:%M")
        booking_end = datetime.strptime(f"{booking.reservation_date} {booking.end_time}", "%Y-%m-%d %H:%M")
        if start_dt < booking_end and booking_start < end_dt:
            return True, booking
    return False, None
