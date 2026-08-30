import os
import secrets
import re
from datetime import datetime, timedelta

from flask import Blueprint, current_app, redirect, request, jsonify
from flask_login import current_user, login_required
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from app.extensions import db
from app.models import Mixer, OAuthState


google_bp = Blueprint("google", __name__)
GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
]


def build_oauth_flow():
    redirect_uri = current_app.config.get("GOOGLE_REDIRECT_URI", "").strip()
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not redirect_uri or not client_id or not client_secret:
        raise RuntimeError("Google OAuth credentials are not configured")

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=GOOGLE_SCOPES)
    flow.redirect_uri = redirect_uri
    return flow


def safe_oauth_error(exc):
    message = str(exc)
    message = re.sub(r"(code|client_secret|access_token|refresh_token)=[^&\s]+", r"\1=[redacted]", message, flags=re.IGNORECASE)
    return message[:300]


@google_bp.route("/google/connect")
@login_required
def connect_google():
    flow = build_oauth_flow()
    state = secrets.token_urlsafe(32)
    OAuthState.query.filter_by(mixer_id=current_user.id).delete()
    db.session.add(OAuthState(
        mixer_id=current_user.id,
        state=state,
        expires_at=datetime.utcnow() + timedelta(minutes=10),
    ))
    db.session.commit()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="false",
        prompt="consent",
        login_hint=current_user.email,
        state=state,
    )
    current_app.logger.info(
        "Google OAuth started: redirect_configured=%s admin_account_configured=%s",
        bool(flow.redirect_uri),
        bool(current_app.config.get("GOOGLE_CALENDAR_ACCOUNT_EMAIL")),
    )
    return redirect(auth_url)


@google_bp.route("/google/callback")
def google_callback():
    if request.args.get("error"):
        current_app.logger.warning(
            "Google OAuth returned an error: error_type=%s error_description_present=%s",
            request.args.get("error"),
            bool(request.args.get("error_description")),
        )
        return jsonify({"error": "Google OAuth authorization was not completed"}), 400

    state = request.args.get("state")
    oauth_state = OAuthState.query.filter_by(state=state).first() if state else None
    if not oauth_state or oauth_state.expires_at < datetime.utcnow():
        return jsonify({"error": "Invalid OAuth state"}), 400
    mixer = Mixer.query.get(oauth_state.mixer_id)
    db.session.delete(oauth_state)
    db.session.commit()
    if not mixer:
        return jsonify({"error": "Mixer not found"}), 404

    code = request.args.get("code")
    if not code:
        return jsonify({"error": "Missing OAuth code"}), 400

    flow = build_oauth_flow()
    try:
        current_app.logger.info(
            "Google OAuth token exchange started: redirect_uri_configured=%s code_present=%s",
            flow.redirect_uri == current_app.config.get("GOOGLE_REDIRECT_URI"),
            bool(code),
        )
        flow.fetch_token(code=code)
    except Exception as exc:
        current_app.logger.error(
            "Google OAuth token exchange failed: error_type=%s safe_message=%s",
            type(exc).__name__,
            safe_oauth_error(exc),
        )
        return jsonify({"error": "Google OAuth token exchange failed"}), 502

    credentials = flow.credentials
    try:
        claims = id_token.verify_oauth2_token(
            credentials.id_token,
            google_requests.Request(),
            os.environ.get("GOOGLE_CLIENT_ID"),
        )
    except Exception:
        current_app.logger.exception("Google OAuth identity verification failed")
        return jsonify({"error": "Google account identity could not be verified"}), 502

    current_app.logger.info(
        "Google OAuth callback verified: mixer_id=%s google_email_present=%s access_token_present=%s refresh_token_present=%s",
        mixer.id,
        bool(claims.get("email")),
        bool(credentials.token),
        bool(credentials.refresh_token),
    )

    mixer.google_access_token = credentials.token
    mixer.google_refresh_token = credentials.refresh_token or mixer.google_refresh_token
    mixer.google_token_expiry = credentials.expiry
    mixer.google_calendar_id = "primary"
    mixer.google_calendar_connected = True
    mixer.connected_at = datetime.utcnow()
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
    mixer.google_calendar_id = "primary"
    db.session.commit()
    return jsonify({"success": True, "message": "Google Calendar disconnected."})
