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


def get_default_mixer_passwords():
    passwords = {}

    for name, _ in DEFAULT_MIXER_SEEDS:
        env_name = f"MIXER_{name.upper().replace(' ', '_').replace('-', '_')}_PASSWORD"
        password = os.getenv(env_name, "").strip()
        if password:
            passwords[name] = password

    return passwords


def create_app():
    app = Flask(__name__, static_folder="../mix-site", template_folder="../mix-site")
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
        db.create_all()
        seed_default_mixers()

    return app


def seed_default_mixers():
    from app.models import Mixer

    passwords = get_default_mixer_passwords()
    if not passwords:
        return

    for name, email in DEFAULT_MIXER_SEEDS:
        password = passwords.get(name)
        if password is None:
            continue

        existing = Mixer.query.filter_by(name=name).first()
        if existing is None:
            mixer = Mixer(name=name, email=email)
            mixer.set_password(password)
            db.session.add(mixer)

    db.session.commit()
