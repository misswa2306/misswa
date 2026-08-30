from datetime import datetime

from flask import Blueprint, current_app, jsonify, request
from flask_login import login_required

from app.extensions import db
from app.models import Reservation, Mixer, GoogleCalendarAccount, GoogleSyncLog
from app.services.booking_service import create_booking_from_payload, validate_no_conflict
from app.services.google_calendar_service import GoogleCalendarService
from app.utils.validators import validate_booking_payload

bookings_bp = Blueprint("bookings", __name__)


@bookings_bp.route("/api/bookings", methods=["POST"])
def create_booking():
    data = request.get_json(silent=True) or {}
    ok, message = validate_booking_payload(data)
    if not ok:
        return jsonify({"success": False, "message": message}), 400

    mixer = Mixer.query.filter_by(name=data["mixer"]).first()
    if not mixer:
        return jsonify({"success": False, "message": "Mixer not found"}), 400

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

    try:
        account = GoogleCalendarService.get_shared_account()
    except RuntimeError as exc:
        current_app.logger.error("Booking sync configuration error: error_type=%s message=%s", type(exc).__name__, str(exc))
        booking.status = "pending"
        booking.google_sync_status = "configuration_error"
        booking.last_google_error = str(exc)
        db.session.commit()
        return jsonify({
            "success": False,
            "message": "Reservation saved, but Google Calendar is not configured.",
            "booking_id": booking.id,
        }), 503
    if not account or not account.access_token:
        current_app.logger.warning(
            "Booking sync stopped: shared Google account unavailable email_configured=%s mixer_id=%s booking_id=%s",
            bool(current_app.config.get("GOOGLE_CALENDAR_ACCOUNT_EMAIL")),
            mixer.id,
            booking.id,
        )
        booking.status = "pending"
        booking.google_sync_status = "not_connected"
        db.session.commit()
        return jsonify({
            "success": False,
            "message": "Reservation saved, but the shared administrator Google Calendar is not connected.",
            "booking_id": booking.id,
        }), 503

    try:
        current_app.logger.info("Booking created; starting Google Calendar sync: booking_id=%s mixer=%s", booking.id, mixer.name)
        gcal = GoogleCalendarService(mixer, account)
        event_id = gcal.create_event(booking)
        booking.google_calendar_event_id = event_id
        booking.google_sync_status = "synced"
        booking.last_google_error = None
        db.session.commit()
    except Exception as exc:
        current_app.logger.exception("Google Calendar event creation failed for booking_id=%s", booking.id)
        booking.status = "pending"
        booking.google_sync_status = "error"
        booking.last_google_error = str(exc)
        db.session.add(GoogleSyncLog(
            reservation_id=booking.id,
            action="create",
            outcome="error",
            message=str(exc),
        ))
        db.session.commit()
        return jsonify({
            "success": False,
            "message": "Reservation saved, but Google Calendar synchronization failed.",
            "booking_id": booking.id,
        }), 502

    return jsonify({
        "success": True,
        "message": "Reservation created successfully.",
        "booking_id": booking.id,
    }), 201


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

    account = GoogleCalendarAccount.query.filter_by(
        account_email=current_app.config.get("GOOGLE_CALENDAR_ACCOUNT_EMAIL", "")
    ).first()
    if account and account.access_token and booking.google_calendar_event_id:
        try:
            GoogleCalendarService(booking.mixer, account).update_event(booking)
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

    account = GoogleCalendarAccount.query.filter_by(
        account_email=current_app.config.get("GOOGLE_CALENDAR_ACCOUNT_EMAIL", "")
    ).first()
    if account and account.access_token and booking.google_calendar_event_id:
        try:
            deleted = GoogleCalendarService(booking.mixer, account).delete_event(booking)
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
