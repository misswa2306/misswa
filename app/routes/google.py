import os
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, current_app, redirect, request, session, url_for, jsonify
from flask_login import login_required, current_user
from google_auth_oauthlib.flow import Flow

from app.extensions import db
from app.models import Mixer, OAuthState


google_bp = Blueprint("google", __name__)


def build_oauth_flow():
    redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:5000/google/callback")
    client_config = {
        "web": {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=["https://www.googleapis.com/auth/calendar.events"])
    flow.redirect_uri = redirect_uri
    return flow


@google_bp.route("/google/connect")
@login_required
def connect_google():
    flow = build_oauth_flow()
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return redirect(auth_url)


@google_bp.route("/google/callback")
def google_callback():
    state = request.args.get("state")
    if not state or state != session.get("oauth_state"):
        return jsonify({"error": "Invalid OAuth state"}), 400

    if not current_user.is_authenticated:
        return jsonify({"error": "Authentication required"}), 401

    flow = build_oauth_flow()
    flow.fetch_token(code=request.args.get("code"))

    credentials = flow.credentials
    mixer = Mixer.query.get(current_user.id)
    mixer.google_calendar_connected = True
    mixer.google_access_token = credentials.token
    mixer.google_refresh_token = credentials.refresh_token
    mixer.google_token_expiry = datetime.utcnow() + timedelta(seconds=credentials.expiry.timestamp() if credentials.expiry else 0)
    mixer.connected_at = datetime.utcnow()
    mixer.google_calendar_id = "primary"
    db.session.commit()

    return redirect("/")


@google_bp.route("/google/disconnect", methods=["POST"])
@login_required
def disconnect_google():
    mixer = Mixer.query.get(current_user.id)
    mixer.google_calendar_connected = False
    mixer.google_access_token = None
    mixer.google_refresh_token = None
    mixer.google_token_expiry = None
    mixer.google_calendar_id = None
    db.session.commit()
    return jsonify({"success": True, "message": "Google Calendar disconnected."})
