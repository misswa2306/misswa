import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-session-secret-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or "sqlite:///{0}".format(BASE_DIR / "restless.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:5000/google/callback")
    GOOGLE_SCOPE = "https://www.googleapis.com/auth/calendar.events"
    APP_URL = os.environ.get("APP_URL", "http://localhost:5000")
    FLASK_ENV = os.environ.get("FLASK_ENV", "development")
    WTF_CSRF_ENABLED = False
