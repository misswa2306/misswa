import os
import secrets
import re
from datetime import datetime, timedelta

from flask import Blueprint, current_app, flash, redirect, request, jsonify, session, url_for
from flask_login import current_user, login_required
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from app.extensions import db
from app.models import Mixer, OAuthState, GoogleCalendarAccount


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
    try:
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
            "Google OAuth started for studio account: admin_mixer_id=%s redirect_configured=%s",
            current_user.id,
            bool(flow.redirect_uri),
        )
        return redirect(auth_url)
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("OAuth error: %s", exc, exc_info=True)
        flash("La connexion Google Calendar a échoué. Veuillez réessayer.", "error")
        return redirect(url_for("mixers.dashboard"))


@google_bp.route("/google/login")
def standalone_google_login():
    try:
        flow = build_oauth_flow()
        state = secrets.token_urlsafe(32)
        session["google_master_oauth_state"] = state
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="false",
            prompt="consent",
            state=state,
        )
        return redirect(auth_url)
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("OAuth error: %s", exc, exc_info=True)
        return "Google Calendar Master connection failed.", 500


@google_bp.route("/google/callback")
def google_callback():
    try:
        if request.args.get("error"):
            raise RuntimeError("Google authorization was not completed")

        code = request.args.get("code")
        if not code:
            raise RuntimeError("Missing OAuth code")

        state = request.args.get("state")
        expected_state = session.pop("google_master_oauth_state", None)
        if expected_state and state != expected_state:
            raise RuntimeError("Invalid OAuth state")

        flow = build_oauth_flow()
        current_app.logger.info(
            "Google OAuth token exchange started: redirect_uri_configured=%s code_present=%s",
            flow.redirect_uri == current_app.config.get("GOOGLE_REDIRECT_URI"),
            bool(code),
        )
        flow.fetch_token(code=code)

        credentials = flow.credentials
        current_app.logger.info(
            "Google OAuth callback verified: access_token_present=%s refresh_token_present=%s",
            bool(credentials.token),
            bool(credentials.refresh_token),
        )

        account_email = (current_app.config.get("GOOGLE_CALENDAR_ACCOUNT_EMAIL") or "studio-admin@localhost").strip()
        account = GoogleCalendarAccount.query.filter_by(account_email=account_email).first()
        if account is None:
            account = GoogleCalendarAccount(account_email=account_email, calendar_id="primary")
            db.session.add(account)

        account.access_token = credentials.token
        account.refresh_token = credentials.refresh_token or account.refresh_token
        account.token_expiry = credentials.expiry
        account.calendar_id = "primary"
        account.connected_at = datetime.utcnow()

        db.session.commit()
        return "Google Calendar Master connecté avec succès ! Vous pouvez fermer cette page."
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("OAuth error: %s", exc, exc_info=True)
        flash("La connexion Google Calendar a échoué. Veuillez réessayer.", "error")
        return redirect(url_for("mixers.dashboard"))


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
