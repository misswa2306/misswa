from datetime import datetime

from flask import Blueprint, current_app, jsonify, request
from flask_login import login_required

from app.extensions import db
from app.models import Reservation, Mixer, GoogleSyncLog
from app.services.booking_service import create_booking_from_payload, validate_no_conflict
from app.services.mixer_google_calendar_service import MixerGoogleCalendarService as GoogleCalendarService
from app.utils.validators import validate_booking_payload

bookings_bp = Blueprint("bookings", __name__)


@bookings_bp.route("/api/bookings", methods=["POST"])
def create_booking():
    try:
        data = request.get_json(silent=True) or {}
        ok, message = validate_booking_payload(data)
        if not ok:
            return jsonify({"success": False, "message": message}), 400

        mixer_name = (data.get("mixer") or "").strip()
        mixer = Mixer.query.filter(Mixer.name.ilike(mixer_name)).first()
        if not mixer:
            return jsonify({"success": False, "message": "Mixer non trouvé"}), 404

        available, _ = validate_no_conflict(
            mixer.id,
            data["booking_date"],
            data["start_time"],
            data["end_time"],
        )
        if not available:
            return jsonify({"success": False, "message": "This time slot is not available."}), 409

        booking, error = create_booking_from_payload(data)
        if error:
            return jsonify({"success": False, "message": error}), 409

        calendar_service = GoogleCalendarService(mixer)
        try:
            if not calendar_service.is_available_for_window(
                data["booking_date"], data["start_time"], data["end_time"]
            ):
                return jsonify({"success": False, "message": "This time slot is not available on the studio Google Calendar."}), 409
        except Exception as exc:
            current_app.logger.warning(
                "Google freebusy check unavailable; continuing with local booking mixer_id=%s error_type=%s",
                mixer.id,
                type(exc).__name__,
            )

        try:
            current_app.logger.info("Booking created; starting Google Calendar sync: booking_id=%s mixer=%s", booking.id, mixer.name)
            try:
                event_id = calendar_service.create_event(booking, mixer=mixer)
            except TypeError as exc:
                if "unexpected keyword argument 'mixer'" not in str(exc):
                    raise
                event_id = calendar_service.create_event(booking)
            booking.google_calendar_event_id = event_id
            booking.google_sync_status = "synced"
            booking.last_google_error = None
            db.session.commit()
        except Exception as exc:
            current_app.logger.exception("Google Calendar event creation failed for booking_id=%s", booking.id)
            booking.google_sync_status = "not_connected" if "not connected" in str(exc).lower() else "error"
            booking.last_google_error = str(exc)
            db.session.add(GoogleSyncLog(
                reservation_id=booking.id,
                action="create",
                outcome=booking.google_sync_status,
                message=str(exc),
            ))
            db.session.commit()
            return jsonify({
                "success": True,
                "message": "Reservation saved, but Google Calendar synchronization failed.",
                "booking_id": booking.id,
                "google_sync_status": booking.google_sync_status,
            }), 201

        return jsonify({
            "success": True,
            "message": "Reservation created successfully.",
            "booking_id": booking.id,
        }), 201
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Booking creation failed")
        return jsonify({"success": False, "error": str(exc)}), 500


@bookings_bp.route("/api/bookings/", methods=["GET"])
def list_bookings():
    bookings = Reservation.query.order_by(Reservation.reservation_date, Reservation.start_time).all()
    return jsonify({
        "success": True,
        "bookings": [
            {
                "id": booking.id,
                "client_name": booking.client_name,
                "client_contact": booking.client_contact,
                "service": booking.service,
                "mixer_id": booking.mixer_id,
                "mixer": booking.mixer.name,
                "reservation_date": booking.reservation_date.isoformat(),
                "start_time": booking.start_time,
                "end_time": booking.end_time,
                "status": booking.status,
                "google_calendar_event_id": booking.google_calendar_event_id,
                "google_sync_status": booking.google_sync_status,
            }
            for booking in bookings
        ],
    })


@bookings_bp.route("/api/bookings/<int:booking_id>", methods=["GET"])
def get_booking(booking_id):
    booking = Reservation.query.get_or_404(booking_id)
    return jsonify({
        "success": True,
        "booking": {
            "id": booking.id,
            "client_name": booking.client_name,
            "client_contact": booking.client_contact,
            "service": booking.service,
            "mixer_id": booking.mixer_id,
            "mixer": booking.mixer.name,
            "reservation_date": booking.reservation_date.isoformat(),
            "start_time": booking.start_time,
            "end_time": booking.end_time,
            "status": booking.status,
            "google_calendar_event_id": booking.google_calendar_event_id,
            "google_sync_status": booking.google_sync_status,
        },
    })


@bookings_bp.route("/api/bookings/<int:booking_id>", methods=["PATCH"])
@login_required
def update_booking(booking_id):
    booking = Reservation.query.get_or_404(booking_id)
    data = request.get_json(silent=True) or {}

    if "start_time" in data:
        booking.start_time = data["start_time"]
    if "end_time" in data:
        booking.end_time = data["end_time"]
    if "service" in data:
        booking.service = data["service"]
    if "date" in data:
        booking.reservation_date = datetime.strptime(data["date"], "%Y-%m-%d").date()

    db.session.commit()

    if (booking.mixer.google_access_token or booking.mixer.google_refresh_token) and booking.google_calendar_event_id:
        try:
            GoogleCalendarService(booking.mixer).update_event(booking)
            booking.google_sync_status = "synced"
            booking.last_google_error = None
            db.session.commit()
        except Exception as exc:
            booking.google_sync_status = "pending"
            booking.last_google_error = str(exc)
            db.session.add(GoogleSyncLog(
                reservation_id=booking.id,
                action="update",
                outcome="error",
                message=str(exc),
            ))
            db.session.commit()

    return jsonify({"success": True, "message": "Booking updated."})


@bookings_bp.route("/api/bookings/<int:booking_id>", methods=["DELETE"])
@login_required
def cancel_booking(booking_id):
    booking = Reservation.query.get_or_404(booking_id)

    if (booking.mixer.google_access_token or booking.mixer.google_refresh_token) and booking.google_calendar_event_id:
        try:
            deleted = GoogleCalendarService(booking.mixer).delete_event(booking)
            if deleted:
                booking.google_calendar_event_id = None
        except Exception as exc:
            booking.google_sync_status = "pending"
            booking.last_google_error = str(exc)
            db.session.add(GoogleSyncLog(
                reservation_id=booking.id,
                action="cancel",
                outcome="error",
                message=str(exc),
            ))

    booking.status = "cancelled"
    booking.cancelled_at = datetime.utcnow()
    booking.google_sync_status = "cancelled"
    db.session.commit()

    return jsonify({"success": True, "message": "Booking cancelled."})
