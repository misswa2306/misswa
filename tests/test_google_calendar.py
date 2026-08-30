import os
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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
from app.models import Mixer, OAuthState, Reservation
from app.routes import google as google_routes
from app.services.mixer_google_calendar_service import MixerGoogleCalendarService


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

    def __init__(self, mixer):
        self.mixer = mixer

    def is_available_for_window(self, reservation_date, start_time, end_time):
        return True

    def create_event(self, reservation):
        self.events.append((self.mixer.google_access_token, self.mixer.google_calendar_id, self.mixer.name))
        return f"event-{reservation.id}"

    def update_event(self, reservation):
        return {"id": reservation.google_calendar_event_id}

    def delete_event(self, reservation):
        return True


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
            for mixer in Mixer.query.all():
                mixer.google_access_token = f"{mixer.name.lower()}-access-token"
                mixer.google_refresh_token = f"{mixer.name.lower()}-refresh-token"
                mixer.google_token_expiry = datetime.utcnow() + timedelta(hours=1)
                mixer.google_calendar_id = "primary"
                mixer.google_calendar_connected = True
            db.session.commit()
        FakeCalendarService.events = []

    def test_oauth_connect_and_callback_share_redirect_uri(self):
        flow = FakeFlow()
        with patch.object(google_routes, "build_oauth_flow", return_value=flow), patch.object(
            google_routes.id_token, "verify_oauth2_token", return_value={"email": "adminrestless@gmail.com"}
        ):
            client = self.app.test_client()
            client.post("/mixer/login", data={"email": "sleeze@test.local", "password": "test-sleeze-password"})
            connect = client.get("/google/connect")
            with self.app.app_context():
                state = OAuthState.query.one().state
            callback = client.get(f"/google/callback?state={state}&code=oauth-code")
        self.assertEqual(connect.status_code, 302)
        self.assertIn("accounts.google.com", connect.headers["Location"])
        self.assertEqual(callback.status_code, 302)
        self.assertIn("/mixer/dashboard", callback.headers["Location"])
        self.assertEqual(flow.redirect_uri, "https://restless-24-7.onrender.com/google/callback")
        self.assertNotIn("redirect_uri", flow.fetch_token_kwargs)
        self.assertEqual(flow.authorization_kwargs["include_granted_scopes"], "false")
        self.assertEqual(flow.authorization_kwargs["prompt"], "consent")
        self.assertEqual(flow.authorization_kwargs["login_hint"], "sleeze@test.local")
        with self.app.app_context():
            mixer = Mixer.query.filter_by(name="Sleeze").one()
            self.assertTrue(mixer.google_access_token)
            self.assertTrue(mixer.google_refresh_token)
            self.assertIsNotNone(mixer.google_token_expiry)

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
        self.assertEqual({event[0] for event in FakeCalendarService.events}, {
            "sleeze-access-token", "kayc-access-token", "ppo-access-token", "boa-access-token"
        })
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

        with self.app.app_context(), patch("app.services.mixer_google_calendar_service.Credentials", return_value=RefreshableCredentials()), patch("app.services.mixer_google_calendar_service.Request"):
            mixer = Mixer.query.filter_by(name="Sleeze").one()
            service = MixerGoogleCalendarService(mixer)
            credentials = service.get_credentials()
            self.assertEqual(credentials.token, "new-access-token")
            self.assertEqual(mixer.google_refresh_token, "old-refresh-token")

    def test_refresh_token_recovers_missing_access_token(self):
        class RefreshableCredentials:
            expired = True
            refresh_token = "stored-refresh-token"
            token = None
            expiry = None

            def refresh(self, request):
                self.token = "recovered-access-token"
                self.expiry = datetime.utcnow() + timedelta(hours=1)

        with self.app.app_context(), patch(
            "app.services.mixer_google_calendar_service.Credentials",
            return_value=RefreshableCredentials(),
        ), patch("app.services.mixer_google_calendar_service.Request"):
            mixer = Mixer.query.filter_by(name="Kayc").one()
            mixer.google_access_token = None
            mixer.google_refresh_token = "stored-refresh-token"
            credentials = MixerGoogleCalendarService(mixer).get_credentials()
            self.assertEqual(credentials.token, "recovered-access-token")
            self.assertEqual(mixer.google_access_token, "recovered-access-token")

    def test_freebusy_uses_mixer_primary_calendar_and_montreal_timezone(self):
        class FakeFreebusyQuery:
            def __init__(self):
                self.body = None

            def query(self, body):
                self.body = body
                return self

            def execute(self):
                return {"calendars": {"primary": {"busy": []}}}

        class FakeGoogleService:
            def __init__(self):
                self.freebusy_query = FakeFreebusyQuery()

            def freebusy(self):
                return self.freebusy_query

        with self.app.app_context():
            mixer = Mixer.query.filter_by(name="PPO").one()
            service = MixerGoogleCalendarService(mixer)
            fake_google = FakeGoogleService()
            reservation = Reservation(
                reservation_date=datetime.strptime("2099-12-01", "%Y-%m-%d").date(),
                start_time="15:00", end_time="17:00",
            )
            with patch.object(service, "get_service", return_value=fake_google):
                self.assertTrue(service.is_available(reservation))
            body = fake_google.freebusy_query.body
        self.assertEqual(body["items"], [{"id": "primary"}])
        self.assertEqual(body["timeZone"], "America/Montreal")
        self.assertIn("-05:00", body["timeMin"])

    def test_invalid_oauth_code_returns_generic_error(self):
        flow = FakeFlow()
        with patch.object(google_routes, "build_oauth_flow", return_value=flow):
            client = self.app.test_client()
            with self.app.app_context():
                db.session.add(OAuthState(
                    mixer_id=Mixer.query.filter_by(name="Sleeze").one().id,
                    state="state",
                    expires_at=datetime.utcnow() + timedelta(minutes=10),
                ))
                db.session.commit()
            response = client.get("/google/callback?state=state&code=expired-code")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/mixer/dashboard", response.headers["Location"])

    def test_invalid_oauth_state_is_rejected(self):
        client = self.app.test_client()
        response = client.get("/google/callback?state=wrong-state&code=oauth-code")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/mixer/dashboard", response.headers["Location"])

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

    def test_logo_is_served_from_static_route(self):
        client = self.app.test_client()
        root = client.get("/")
        self.assertEqual(root.status_code, 200)
        self.assertIn("/static/logo.png", root.get_data(as_text=True))
        static = client.get("/static/logo.png")
        self.assertEqual(static.status_code, 200)
        self.assertEqual(static.mimetype, "image/png")
        for audio_path in [
            "/static/K1ME%20x%20WAZI%20x%20MINO-%20LOVAS.mp3",
            "/static/GAKO%20-%20CHEMIN.mp3",
            "/static/SORRY.mp3",
        ]:
            audio = client.get(audio_path)
            self.assertEqual(audio.status_code, 200, msg=audio_path)
            self.assertEqual(audio.mimetype, "audio/mpeg", msg=audio_path)

    def test_dynamic_booking_rules_against_montreal_timezone(self):
        with patch.object(
            __import__("app.routes.bookings", fromlist=["GoogleCalendarService"]),
            "GoogleCalendarService",
            FakeCalendarService,
        ):
            client = self.app.test_client()
            now_mt = datetime.now(ZoneInfo("America/Montreal"))

            def date_offset(days):
                return (now_mt + timedelta(days=days)).date().isoformat()

            def time_offset(minutes):
                return (now_mt + timedelta(minutes=minutes)).strftime("%H:%M")

            rejected_dates = [date_offset(-1), date_offset(-7), date_offset(-30)]
            for booking_date in rejected_dates:
                response = client.post("/api/bookings", json={
                    "artist": "Past Client",
                    "contact": "past@example.com",
                    "service": "Mix",
                    "mixer": "Sleeze",
                    "booking_date": booking_date,
                    "start_time": "18:00",
                    "end_time": "20:00",
                })
                self.assertEqual(response.status_code, 409, msg=f"Past date {booking_date} should be rejected")

            today = date_offset(0)
            past_time = time_offset(-90)
            response = client.post("/api/bookings", json={
                "artist": "Today Past Client",
                "contact": "past@example.com",
                "service": "Mix",
                "mixer": "Sleeze",
                "booking_date": today,
                "start_time": past_time,
                "end_time": time_offset(30),
            })
            self.assertEqual(response.status_code, 409, msg="Same-day past time should be rejected")

            tomorrow = date_offset(1)
            future_response = client.post("/api/bookings", json={
                "artist": "Future Client",
                "contact": "future@example.com",
                "service": "Mix",
                "mixer": "Sleeze",
                "booking_date": tomorrow,
                "start_time": "15:00",
                "end_time": "17:00",
            })
            self.assertEqual(future_response.status_code, 201, msg="Future booking should be accepted")

            first = client.post("/api/bookings", json={
                "artist": "Slot Client",
                "contact": "slot@example.com",
                "service": "Mix",
                "mixer": "Sleeze",
                "booking_date": date_offset(2),
                "start_time": "15:00",
                "end_time": "17:00",
            })
            self.assertEqual(first.status_code, 201)

            rejected_overlaps = [
                ("14:00", "16:00"),
                ("15:00", "16:00"),
                ("16:00", "17:00"),
                ("16:30", "18:00"),
                ("15:30", "18:00"),
                ("16:00", "16:30"),
            ]
            for start_time, end_time in rejected_overlaps:
                response = client.post("/api/bookings", json={
                    "artist": "Overlap Client",
                    "contact": "overlap@example.com",
                    "service": "Mix",
                    "mixer": "Sleeze",
                    "booking_date": date_offset(2),
                    "start_time": start_time,
                    "end_time": end_time,
                })
                self.assertEqual(response.status_code, 409, msg=f"Overlap {start_time}-{end_time} should be rejected")

            accepted_after = client.post("/api/bookings", json={
                "artist": "After Client",
                "contact": "after@example.com",
                "service": "Mix",
                "mixer": "Sleeze",
                "booking_date": date_offset(2),
                "start_time": "17:00",
                "end_time": "19:00",
            })
            self.assertEqual(accepted_after.status_code, 201, msg="Exact end-to-start slot should be accepted")

            other_mixer = client.post("/api/bookings", json={
                "artist": "Other Mixer Client",
                "contact": "other@example.com",
                "service": "Mix",
                "mixer": "Kayc",
                "booking_date": date_offset(2),
                "start_time": "15:00",
                "end_time": "17:00",
            })
            self.assertEqual(other_mixer.status_code, 201, msg="Same slot on a different mixer should be accepted")

            for mixer_name in ["Sleeze", "Kayc", "PPO", "Boa"]:
                response = client.post("/api/bookings", json={
                    "artist": f"Mixer {mixer_name}",
                    "contact": "mixer@example.com",
                    "service": "Mix",
                    "mixer": mixer_name,
                    "booking_date": date_offset(10 + ["Sleeze", "Kayc", "PPO", "Boa"].index(mixer_name)),
                    "start_time": "18:00",
                    "end_time": "20:00",
                })
                self.assertEqual(response.status_code, 201, msg=f"{mixer_name} should accept a future booking")

    def test_simultaneous_duplicate_booking_only_accepts_one(self):
        with patch.object(
            __import__("app.routes.bookings", fromlist=["GoogleCalendarService"]),
            "GoogleCalendarService",
            FakeCalendarService,
        ):
            client = self.app.test_client()
            booking_date = (datetime.now(ZoneInfo("America/Montreal")) + timedelta(days=3)).date().isoformat()
            payload = {
                "artist": "Concurrent Client",
                "contact": "concurrent@example.com",
                "service": "Mix",
                "mixer": "Sleeze",
                "booking_date": booking_date,
                "start_time": "16:00",
                "end_time": "18:00",
            }
            first = client.post("/api/bookings", json=payload)
            second = client.post("/api/bookings", json=payload)
            self.assertEqual(first.status_code, 201)
            self.assertEqual(second.status_code, 409)

    def test_rejected_bookings_do_not_create_google_events_and_accepted_ones_do(self):
        with patch.object(
            __import__("app.routes.bookings", fromlist=["GoogleCalendarService"]),
            "GoogleCalendarService",
            FakeCalendarService,
        ):
            client = self.app.test_client()
            future_date = (datetime.now(ZoneInfo("America/Montreal")) + timedelta(days=5)).date().isoformat()
            accepted = client.post("/api/bookings", json={
                "artist": "Accepted Sync Client",
                "contact": "sync@example.com",
                "service": "Mix",
                "mixer": "Sleeze",
                "booking_date": future_date,
                "start_time": "18:00",
                "end_time": "20:00",
            })
            self.assertEqual(accepted.status_code, 201)
            self.assertEqual(len(FakeCalendarService.events), 1)

            rejected = client.post("/api/bookings", json={
                "artist": "Rejected Sync Client",
                "contact": "reject@example.com",
                "service": "Mix",
                "mixer": "Sleeze",
                "booking_date": future_date,
                "start_time": "18:30",
                "end_time": "19:30",
            })
            self.assertEqual(rejected.status_code, 409)
            self.assertEqual(len(FakeCalendarService.events), 1)

    def test_public_site_and_booking_work_without_admin_session(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/").status_code, 200)
        with patch.object(
            __import__("app.routes.bookings", fromlist=["GoogleCalendarService"]),
            "GoogleCalendarService",
            FakeCalendarService,
        ):
            response = client.post("/api/bookings", json={
                "artist": "Public Client", "contact": "public@example.com", "service": "Mix",
                "mixer": "Kayc", "booking_date": "2099-08-30", "start_time": "15:00", "end_time": "17:00",
            })
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.get_json()["success"])

    def test_availability_is_scoped_to_mixer_and_date(self):
        with self.app.app_context():
            sleeze = Mixer.query.filter_by(name="Sleeze").one()
            kayc = Mixer.query.filter_by(name="Kayc").one()
            db.session.add(Reservation(
                client_name="Busy Client", client_contact="busy@example.com", service="Mix",
                mixer_id=sleeze.id, reservation_date=datetime.strptime("2099-08-30", "%Y-%m-%d").date(),
                start_time="15:00", end_time="17:00", status="confirmed",
            ))
            db.session.commit()
            sleeze_response = self.app.test_client().get(
                f"/api/mixers/{sleeze.id}/availability?date=2099-08-30"
            )
            kayc_response = self.app.test_client().get(
                f"/api/mixers/{kayc.id}/availability?date=2099-08-30"
            )
        self.assertEqual(sleeze_response.status_code, 200)
        self.assertEqual(sleeze_response.get_json()["occupied"], [{"start_time": "15:00", "end_time": "17:00"}])
        self.assertEqual(kayc_response.get_json()["occupied"], [])

    def test_all_four_mixers_accept_independent_bookings(self):
        with patch.object(
            __import__("app.routes.bookings", fromlist=["GoogleCalendarService"]),
            "GoogleCalendarService",
            FakeCalendarService,
        ):
            client = self.app.test_client()
            for index, mixer in enumerate(["Sleeze", "Kayc", "PPO", "Boa"], start=1):
                response = client.post("/api/bookings", json={
                    "artist": f"{mixer} Client", "contact": "mixer@example.com", "service": "Mix",
                    "mixer": mixer, "booking_date": f"2099-09-{index:02d}",
                    "start_time": "15:00", "end_time": "17:00",
                })
                self.assertEqual(response.status_code, 201, msg=mixer)
        self.assertEqual(len(FakeCalendarService.events), 4)

    def test_boundary_end_time_is_available_and_overlap_is_not(self):
        with self.app.app_context():
            sleeze = Mixer.query.filter_by(name="Sleeze").one()
            db.session.add(Reservation(
                client_name="Busy Client", client_contact="busy@example.com", service="Mix",
                mixer_id=sleeze.id, reservation_date=datetime.strptime("2099-08-30", "%Y-%m-%d").date(),
                start_time="15:00", end_time="17:00", status="confirmed",
            ))
            db.session.commit()
            client = self.app.test_client()
            at_end = client.get(
                f"/api/mixers/{sleeze.id}/availability?date=2099-08-30&start_time=17:00&end_time=19:00"
            )
            overlap = client.get(
                f"/api/mixers/{sleeze.id}/availability?date=2099-08-30&start_time=16:30&end_time=18:00"
            )
        self.assertTrue(at_end.get_json()["available"])
        self.assertFalse(overlap.get_json()["available"])

    def test_google_outage_keeps_booking_and_returns_success(self):
        booking_module = __import__("app.routes.bookings", fromlist=["GoogleCalendarService"])
        with patch.object(booking_module, "GoogleCalendarService") as service_class:
            service_class.return_value.create_event.side_effect = RuntimeError("calendar unavailable")
            response = self.app.test_client().post("/api/bookings", json={
                "artist": "Offline Calendar Client", "contact": "offline@example.com", "service": "Mix",
                "mixer": "PPO", "booking_date": "2099-10-01", "start_time": "15:00", "end_time": "17:00",
            })
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.get_json()["success"])
        with self.app.app_context():
            booking = Reservation.query.filter_by(client_name="Offline Calendar Client").one()
            self.assertEqual(booking.status, "confirmed")
            self.assertEqual(booking.google_sync_status, "error")

    def test_frontend_requests_mixer_date_availability_and_has_persistent_navbar(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "mix-site", "index.html"), encoding="utf-8") as site:
            html = site.read()
        self.assertIn("position: fixed", html)
        self.assertIn("top: 0", html)
        self.assertIn("z-index: 1000", html)
        self.assertIn("/api/mixers/${mixerId}/availability?date=", html)
        self.assertIn("mixerSelect.addEventListener('change', refreshAvailability)", html)
        self.assertIn("refreshAvailability();", html)
        self.assertIn("button.disabled = disabled", html)
        self.assertIn("src=\"/static/logo.png\"", html)
        self.assertIn("src=\"/static/K1ME%20x%20WAZI%20x%20MINO-%20LOVAS.mp3\"", html)
        self.assertIn("src=\"/static/GAKO%20-%20CHEMIN.mp3\"", html)
        self.assertIn("src=\"/static/SORRY.mp3\"", html)

    def test_missing_google_account_keeps_booking_saved(self):
        with self.app.app_context():
            mixer = Mixer.query.filter_by(name="Sleeze").one()
            mixer.google_access_token = None
            mixer.google_refresh_token = None
            db.session.commit()
        response = self.app.test_client().post("/api/bookings", json={
            "artist": "No Account Client", "contact": "no-account@example.com", "service": "Mix",
            "mixer": "Sleeze", "booking_date": "2099-11-01", "start_time": "15:00", "end_time": "17:00",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["google_sync_status"], "not_connected")

    def test_cancelled_booking_does_not_occupy_availability(self):
        with self.app.app_context():
            sleeze = Mixer.query.filter_by(name="Sleeze").one()
            db.session.add(Reservation(
                client_name="Cancelled Client", client_contact="cancelled@example.com", service="Mix",
                mixer_id=sleeze.id, reservation_date=datetime.strptime("2099-11-02", "%Y-%m-%d").date(),
                start_time="15:00", end_time="17:00", status="cancelled",
            ))
            db.session.commit()
            response = self.app.test_client().get(
                f"/api/mixers/{sleeze.id}/availability?date=2099-11-02"
            )
        self.assertEqual(response.get_json()["occupied"], [])

    def test_invalid_past_date_is_rejected_by_availability_check(self):
        past_date = (datetime.now(ZoneInfo("America/Montreal")) - timedelta(days=1)).date().isoformat()
        mixer_id = self.app.test_client().get("/api/mixers").get_json()[0]["id"]
        response = self.app.test_client().get(
            f"/api/mixers/{mixer_id}/availability?date={past_date}&start_time=15:00&end_time=17:00"
        )
        self.assertFalse(response.get_json()["available"])

    def test_navbar_has_no_scroll_visibility_controller(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "mix-site", "index.html"), encoding="utf-8") as site:
            html = site.read()
        self.assertNotIn("window.addEventListener('scroll'", html)
        self.assertNotIn("classList.toggle('scrolled'", html)


if __name__ == "__main__":
    unittest.main()