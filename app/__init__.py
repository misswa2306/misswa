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
        raise RuntimeError(
            "Missing required mixer bootstrap environment variables: "
            + ", ".join(missing)
        )

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

    existing_names = {mixer.name for mixer in Mixer.query.all()}
    missing_names = [name for name, _ in DEFAULT_MIXER_SEEDS if name not in existing_names]
    if not missing_names:
        return

    passwords = get_default_mixer_passwords(missing_names)

    for name, email in DEFAULT_MIXER_SEEDS:
        if name not in missing_names:
            continue
        existing = Mixer.query.filter_by(name=name).first()
        if existing is None:
            mixer = Mixer(name=name, email=email)
            mixer.set_password(passwords[name])
            db.session.add(mixer)

    db.session.commit()
