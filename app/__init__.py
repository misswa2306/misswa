import os
from flask import Flask, send_from_directory
from config import Config

from app.extensions import db, login_manager


DEFAULT_MIXER_SEEDS = [
    ("Sleeze", "sleeze@restless.local"),
    ("Kayc", "kayc@restless.local"),
    ("PPO", "ppo@restless.local"),
    ("Boa", "boa@restless.local"),
]


def get_default_mixer_passwords(required_names=None):
    required_names = set(required_names) if required_names is not None else {
        name for name, _ in DEFAULT_MIXER_SEEDS
    }
    passwords = {}
    missing = []

    for name, _ in DEFAULT_MIXER_SEEDS:
        if name not in required_names:
            continue
        env_name = f"MIXER_{name.upper().replace(' ', '_').replace('-', '_')}_PASSWORD"
        password = os.getenv(env_name, "").strip()
        if not password:
            missing.append(env_name)
        else:
            passwords[name] = password

    if missing:
        for env_name in missing:
            print(f"Warning: missing bootstrap env {env_name}; skipping mixer seeding for this environment.")

    return passwords


def create_app():
    app = Flask(__name__, static_folder="../mix-site", template_folder="../mix-site", static_url_path="/static")
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id):
        from app.models import Mixer
        return Mixer.query.get(int(user_id))

    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    from app.routes.auth import auth_bp
    from app.routes.bookings import bookings_bp
    from app.routes.google import google_bp
    from app.routes.mixers import mixers_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(bookings_bp)
    app.register_blueprint(google_bp)
    app.register_blueprint(mixers_bp)

    with app.app_context():
        try:
            db.create_all()
        except Exception as exc:
            app.logger.exception("Database initialization failed during app startup: %s", exc)

        try:
            seed_database(app)
        except Exception as exc:
            app.logger.exception("Default mixer seeding failed during app startup: %s", exc)

    return app


def seed_database(app):
    with app.app_context():
        seed_default_mixers()


def seed_default_mixers():
    from app.models import Mixer

    default_seed_data = [
        {"name": "Boa", "email": "boa@restless.com"},
        {"name": "Kayc", "email": "kayc@restless.com"},
        {"name": "Sleeze", "email": "sleeze@restless.com"},
        {"name": "PPO", "email": "ppo@restless.com"},
    ]

    for data in default_seed_data:
        existing = Mixer.query.filter_by(name=data["name"]).first()
        if existing is not None:
            continue

        mixer = Mixer(name=data["name"], email=data["email"])
        mixer.set_password("Restless2026!")
        db.session.add(mixer)

    db.session.commit()
