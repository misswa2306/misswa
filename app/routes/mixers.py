from flask import Blueprint, current_app, jsonify, render_template_string, request
from flask_login import login_required, current_user

from app.models import GoogleCalendarAccount, Mixer
from app.services.booking_service import validate_no_conflict

mixers_bp = Blueprint("mixers", __name__)


@mixers_bp.route("/mixer/dashboard")
@login_required
def dashboard():
    mixer = Mixer.query.get(current_user.id)
    account = GoogleCalendarAccount.query.filter_by(
        account_email=current_app.config.get("GOOGLE_CALENDAR_ACCOUNT_EMAIL", "")
    ).first()
    return render_template_string('''
        <h2>Dashboard mixeur</h2>
        <p>Mixeur : {{ mixer.name }}</p>
        {% if account and account.access_token %}
            <p>Google Calendar connecté ✓</p>
            <form method="post" action="/google/disconnect">
                <button type="submit">Déconnecter Google Calendar</button>
            </form>
        {% else %}
            <a href="/google/connect">Connecter Google Calendar</a>
        {% endif %}
    ''', mixer=mixer, account=account)


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

    if not all((booking_date, start_time, end_time)):
        return jsonify({
            "success": False,
            "message": "date, start_time and end_time are required",
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
