from datetime import datetime

from flask import Blueprint, current_app, jsonify, render_template_string, request
from flask_login import login_required, current_user

from app.models import Mixer, Reservation
from app.services.booking_service import validate_no_conflict

mixers_bp = Blueprint("mixers", __name__)


@mixers_bp.route("/mixer/dashboard")
@login_required
def dashboard():
    mixer = Mixer.query.get(current_user.id)
    return render_template_string('''
        <h2>Dashboard mixeur</h2>
        <p>Mixeur : {{ mixer.name }}</p>
        {% if mixer.google_access_token %}
            <p>Google Calendar connecté ✓</p>
            <form method="post" action="/google/disconnect">
                <button type="submit">Déconnecter Google Calendar</button>
            </form>
        {% else %}
            <a href="/google/connect">Connecter Google Calendar</a>
        {% endif %}
    ''', mixer=mixer)


@mixers_bp.route("/api/mixers")
def list_mixers():
    mix = Mixer.query.all()
    return jsonify([
        {
            "id": m.id,
            "name": m.name,
            "google_calendar_connected": m.google_calendar_connected,
        }
        for m in mix
    ])


@mixers_bp.route("/api/mixers/<int:mixer_id>/availability")
def mixer_availability(mixer_id):
    mixer = Mixer.query.get_or_404(mixer_id)
    booking_date = request.args.get("date", "")
    start_time = request.args.get("start_time", "")
    end_time = request.args.get("end_time", "")

    if not booking_date:
        return jsonify({
            "success": False,
            "message": "date is required",
        }), 400

    if not start_time and not end_time:
        try:
            selected_date = datetime.strptime(booking_date, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"success": False, "message": "Invalid date format"}), 400

        bookings = Reservation.query.filter(
            Reservation.mixer_id == mixer.id,
            Reservation.reservation_date == selected_date,
            Reservation.status != "cancelled",
        ).order_by(Reservation.start_time).all()
        return jsonify({
            "success": True,
            "mixer_id": mixer.id,
            "mixer": mixer.name,
            "date": booking_date,
            "occupied": [
                {"start_time": booking.start_time, "end_time": booking.end_time}
                for booking in bookings
            ],
        })

    if not all((start_time, end_time)):
        return jsonify({
            "success": False,
            "message": "start_time and end_time are required together",
        }), 400

    try:
        available, message = validate_no_conflict(
            mixer.id,
            booking_date,
            start_time,
            end_time,
        )
    except ValueError:
        return jsonify({
            "success": False,
            "message": "Invalid date or time format",
        }), 400

    return jsonify({
        "success": True,
        "mixer_id": mixer.id,
        "mixer": mixer.name,
        "date": booking_date,
        "start_time": start_time,
        "end_time": end_time,
        "available": available,
        "message": message,
    })
