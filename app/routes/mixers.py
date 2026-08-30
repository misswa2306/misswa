from flask import Blueprint, jsonify, render_template_string
from flask_login import login_required, current_user

from app.models import Mixer

mixers_bp = Blueprint("mixers", __name__)


@mixers_bp.route("/mixer/dashboard")
@login_required
def dashboard():
    mixer = Mixer.query.get(current_user.id)
    return render_template_string('''
        <h2>Dashboard mixeur</h2>
        <p>Mixeur : {{ mixer.name }}</p>
        {% if mixer.google_calendar_connected %}
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
