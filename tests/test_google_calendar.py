import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "https://restless-24-7.onrender.com/google/callback")
os.environ.setdefault("GOOGLE_CALENDAR_ACCOUNT_EMAIL", "adminrestless@gmail.com")
os.environ.setdefault("MIXER_SLEEZE_PASSWORD", "test-sleeze-password")
os.environ.setdefault("MIXER_KAYC_PASSWORD", "test-kayc-password")
os.environ.setdefault("MIXER_PPO_PASSWORD", "test-ppo-password")
os.environ.setdefault("MIXER_BOA_PASSWORD", "test-boa-password")

from app import create_app
from app.extensions import db
from app.models import GoogleCalendarAccount, Mixer, Reservation
from app.routes import google as google_routes
from app.services.google_calendar_service import GoogleCalendarService


class FakeCredentials:
    def __init__(self, token="access-token", refresh_token="refresh-token", expiry=None, id_token="id-token"):
        self.token = token
        self.refresh_token = refresh_token
        self.expiry = expiry or datetime.utcnow() + timedelta(hours=1)
        self.id_token = id_token


class FakeFlow:
    redirect_uri = "https://restless-24-7.onrender.com/google/callback"
    credentials = FakeCredentials()
    exchanged_redirect_uri = None
    fetch_token_kwargs = None
    authorization_kwargs = None

    def authorization_url(self, **kwargs):
        self.state = kwargs["state"]
        self.authorization_kwargs = kwargs
        return "https://accounts.google.com/o/oauth2/auth?state=hidden", None

    def fetch_token(self, code, **kwargs):
        self.fetch_token_kwargs = kwargs
        if code == "expired-code":
            raise ValueError("invalid_grant: code expired")


class FakeCalendarService:
    events = []
    get_shared_account = staticmethod(GoogleCalendarService.get_shared_account)

    def __init__(self, mixer, account):
        self.mixer = mixer
        self.account = account

    def create_event(self, reservation):
        self.events.append((self.account.account_email, self.account.calendar_id, self.mixer.name))
        return f"event-{reservation.id}"


class GoogleCalendarFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_path = os.path.join(os.getcwd(), "google-calendar-tests.db")
        os.environ["DATABASE_URL"] = f"sqlite:///{cls.database_path.replace(chr(92), '/') }"
        os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
        os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret"
        os.environ["GOOGLE_REDIRECT_URI"] = "https://restless-24-7.onrender.com/google/callback"
        os.environ["GOOGLE_CALENDAR_ACCOUNT_EMAIL"] = "adminrestless@gmail.com"
        for name in ["SLEEZE", "KAYC", "PPO", "BOA"]:
            os.environ[f"MIXER_{name}_PASSWORD"] = f"test-{name.lower()}-password"
        cls.app = create_app()

    def setUp(self):
        with self.app.app_context():
            db.drop_all()
            db.create_all()
            for name, email in [("Sleeze", "sleeze@test.local"), ("Kayc", "kayc@test.local"), ("PPO", "ppo@test.local"), ("Boa", "boa@test.local")]:
                mixer = Mixer(name=name, email=email)
                mixer.set_password(f"test-{name.lower()}-password")
                db.session.add(mixer)
            db.session.add(GoogleCalendarAccount(
                account_email="adminrestless@gmail.com",
                access_token="stored-access-token",
                refresh_token="stored-refresh-token",
                token_expiry=datetime.utcnow() + timedelta(hours=1),
                calendar_id="primary",
            ))
            db.session.commit()
        FakeCalendarService.events = []

    def test_oauth_connect_and_callback_share_redirect_uri(self):
        flow = FakeFlow()
        with patch.object(google_routes, "build_oauth_flow", return_value=flow), patch.object(
            google_routes.id_token, "verify_oauth2_token", return_value={"email": "adminrestless@gmail.com"}
        ):
            client = self.app.test_client()
            connect = client.get("/google/connect")
            with client.session_transaction() as session:
                state = session["oauth_state"]
            callback = client.get(f"/google/callback?state={state}&code=oauth-code")
        self.assertEqual(connect.status_code, 302)
        self.assertIn("accounts.google.com", connect.headers["Location"])
        self.assertEqual(callback.status_code, 302)
        self.assertEqual(flow.redirect_uri, "https://restless-24-7.onrender.com/google/callback")
        self.assertNotIn("redirect_uri", flow.fetch_token_kwargs)
        self.assertEqual(flow.authorization_kwargs["include_granted_scopes"], "false")
        self.assertEqual(flow.authorization_kwargs["prompt"], "consent")
        self.assertEqual(flow.authorization_kwargs["login_hint"], "adminrestless@gmail.com")
        with self.app.app_context():
            account = GoogleCalendarAccount.query.one()
            self.assertTrue(account.access_token)
            self.assertTrue(account.refresh_token)

    def test_four_mixers_use_one_primary_calendar(self):
        with patch.object(booking_module := __import__("app.routes.bookings", fromlist=["GoogleCalendarService"]), "GoogleCalendarService", FakeCalendarService):
            client = self.app.test_client()
            for index, name in enumerate(["Sleeze", "Kayc", "PPO", "Boa"], start=1):
                response = client.post("/api/bookings", json={
                    "artist": f"Client {name}", "contact": "client@example.com", "service": "Mix",
                    "mixer": name, "booking_date": f"2099-06-{index:02d}", "start_time": "18:00", "end_time": "20:00",
                })
                self.assertEqual(response.status_code, 201)
        self.assertEqual(len(FakeCalendarService.events), 4)
        self.assertEqual({event[0] for event in FakeCalendarService.events}, {"adminrestless@gmail.com"})
        self.assertEqual({event[1] for event in FakeCalendarService.events}, {"primary"})
        self.assertEqual({event[2] for event in FakeCalendarService.events}, {"Sleeze", "Kayc", "PPO", "Boa"})

    def test_expired_token_refreshes_and_preserves_refresh_token(self):
        class RefreshableCredentials:
            expired = True
            refresh_token = "old-refresh-token"
            token = "old-access-token"
            expiry = datetime.utcnow()

            def refresh(self, request):
                self.token = "new-access-token"
                self.expiry = datetime.utcnow() + timedelta(hours=1)

        with self.app.app_context(), patch("app.services.google_calendar_service.Credentials", return_value=RefreshableCredentials()), patch("app.services.google_calendar_service.Request"):
            account = GoogleCalendarAccount.query.one()
            service = GoogleCalendarService(Mixer.query.first(), account)
            credentials = service.get_credentials()
            self.assertEqual(credentials.token, "new-access-token")
            self.assertEqual(account.refresh_token, "old-refresh-token")

    def test_invalid_oauth_code_returns_generic_error(self):
        flow = FakeFlow()
        with patch.object(google_routes, "build_oauth_flow", return_value=flow):
            client = self.app.test_client()
            with client.session_transaction() as session:
                session["oauth_state"] = "state"
            response = client.get("/google/callback?state=state&code=expired-code")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json(), {"error": "Google OAuth token exchange failed"})

    def test_invalid_oauth_state_is_rejected(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["oauth_state"] = "expected-state"
        response = client.get("/google/callback?state=wrong-state&code=oauth-code")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "Invalid OAuth state"})

    def test_duplicate_booking_is_rejected_as_conflict(self):
        with patch.object(
            __import__("app.routes.bookings", fromlist=["GoogleCalendarService"]),
            "GoogleCalendarService",
            FakeCalendarService,
        ):
            client = self.app.test_client()
            payload = {
                "artist": "Conflict Client", "contact": "client@example.com", "service": "Mix",
                "mixer": "Sleeze", "booking_date": "2099-07-01", "start_time": "18:00", "end_time": "20:00",
            }
            first = client.post("/api/bookings", json=payload)
            second = client.post("/api/bookings", json=payload)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)


if __name__ == "__main__":
    unittest.main()