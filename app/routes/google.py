import os
import secrets
import re
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
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar.events",
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
def connect_google():
    flow = build_oauth_flow()
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="false",
        prompt="consent",
        login_hint=current_app.config.get("GOOGLE_CALENDAR_ACCOUNT_EMAIL", "").strip(),
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
    if not state or state != session.get("oauth_state"):
        return jsonify({"error": "Invalid OAuth state"}), 400
    session.pop("oauth_state", None)

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
    account.refresh_token = credentials.refresh_token or account.refresh_token
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
