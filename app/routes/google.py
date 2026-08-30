import os
import secrets
from datetime import datetime

from flask import Blueprint, current_app, redirect, request, session, url_for, jsonify
from flask_login import login_required
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from app.extensions import db
from app.models import Mixer, GoogleCalendarAccount, OAuthState


google_bp = Blueprint("google", __name__)
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "email",
]


def build_oauth_flow():
    redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:5000/google/callback")
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
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


@google_bp.route("/google/connect")
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
    current_app.logger.info(
        "Google OAuth started: redirect_configured=%s admin_account_configured=%s",
        bool(flow.redirect_uri),
        bool(current_app.config.get("GOOGLE_CALENDAR_ACCOUNT_EMAIL")),
    )
    return redirect(auth_url)


@google_bp.route("/google/callback")
def google_callback():
    state = request.args.get("state")
    if not state or state != session.get("oauth_state"):
        return jsonify({"error": "Invalid OAuth state"}), 400

    code = request.args.get("code")
    if not code:
        return jsonify({"error": "Missing OAuth code"}), 400

    flow = build_oauth_flow()
    try:
        flow.fetch_token(code=code)
    except Exception:
        current_app.logger.exception("Google OAuth token exchange failed")
        return jsonify({"error": "Google OAuth token exchange failed"}), 502

    credentials = flow.credentials
    account_email = current_app.config.get("GOOGLE_CALENDAR_ACCOUNT_EMAIL", "").strip()
    if not account_email:
        current_app.logger.error("Google OAuth callback rejected: admin account email is not configured")
        return jsonify({"error": "Google Calendar account email is not configured"}), 500

    try:
        claims = id_token.verify_oauth2_token(
            credentials.id_token,
            google_requests.Request(),
            os.environ.get("GOOGLE_CLIENT_ID"),
        )
    except Exception:
        current_app.logger.exception("Google OAuth identity verification failed")
        return jsonify({"error": "Google account identity could not be verified"}), 502

    authorized_email = claims.get("email", "").strip().lower()
    if authorized_email != account_email.lower():
        current_app.logger.error("Google OAuth account mismatch: configured account does not match authorized account")
        return jsonify({"error": "The authorized Google account is not the configured administrator account"}), 403

    current_app.logger.info(
        "Google OAuth callback verified: account_match=%s access_token_present=%s refresh_token_present=%s",
        True,
        bool(credentials.token),
        bool(credentials.refresh_token),
    )

    account = GoogleCalendarAccount.query.filter_by(account_email=account_email).first()
    if account is None:
        account = GoogleCalendarAccount(account_email=account_email)
        db.session.add(account)

    account.access_token = credentials.token
    account.refresh_token = credentials.refresh_token
    account.token_expiry = credentials.expiry
    account.connected_at = datetime.utcnow()
    account.calendar_id = "primary"
    db.session.commit()

    return redirect("/")


@google_bp.route("/google/disconnect", methods=["POST"])
@login_required
def disconnect_google():
    account_email = current_app.config.get("GOOGLE_CALENDAR_ACCOUNT_EMAIL", "").strip()
    account = GoogleCalendarAccount.query.filter_by(account_email=account_email).first()
    if account:
        account.access_token = None
        account.refresh_token = None
        account.token_expiry = None
        account.calendar_id = "primary"
    db.session.commit()
    return jsonify({"success": True, "message": "Google Calendar disconnected."})
