from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db


class Mixer(UserMixin, db.Model):
    __tablename__ = "mixers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default="mixer")
    is_active = db.Column(db.Boolean, default=True)

    google_calendar_connected = db.Column(db.Boolean, default=False)
    google_access_token = db.Column(db.Text, nullable=True)
    google_refresh_token = db.Column(db.Text, nullable=True)
    google_token_expiry = db.Column(db.DateTime, nullable=True)
    google_calendar_id = db.Column(db.String(255), nullable=True)
    connected_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    reservations = db.relationship("Reservation", back_populates="mixer", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class GoogleCalendarAccount(db.Model):
    __tablename__ = "google_calendar_accounts"

    id = db.Column(db.Integer, primary_key=True)
    account_email = db.Column(db.String(255), unique=True, nullable=False)
    access_token = db.Column(db.Text, nullable=True)
    refresh_token = db.Column(db.Text, nullable=True)
    token_expiry = db.Column(db.DateTime, nullable=True)
    calendar_id = db.Column(db.String(255), nullable=False, default="primary")
    connected_at = db.Column(db.DateTime, nullable=True)


class Reservation(db.Model):
    __tablename__ = "reservations"

    id = db.Column(db.Integer, primary_key=True)
    client_name = db.Column(db.String(150), nullable=False)
    client_contact = db.Column(db.String(200), nullable=False)
    service = db.Column(db.String(120), nullable=False)
    reservation_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.String(10), nullable=False)
    end_time = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(50), default="pending")
    google_calendar_event_id = db.Column(db.String(255), nullable=True)
    google_sync_status = db.Column(db.String(50), default="pending")
    last_google_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    cancelled_at = db.Column(db.DateTime, nullable=True)

    mixer_id = db.Column(db.Integer, db.ForeignKey("mixers.id"), nullable=False)
    mixer = db.relationship("Mixer", back_populates="reservations")


class OAuthState(db.Model):
    __tablename__ = "oauth_states"

    id = db.Column(db.Integer, primary_key=True)
    mixer_id = db.Column(db.Integer, db.ForeignKey("mixers.id"), nullable=False)
    state = db.Column(db.String(255), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)


class GoogleSyncLog(db.Model):
    __tablename__ = "google_sync_logs"

    id = db.Column(db.Integer, primary_key=True)
    reservation_id = db.Column(db.Integer, db.ForeignKey("reservations.id"), nullable=False)
    action = db.Column(db.String(50), nullable=False)
    outcome = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
