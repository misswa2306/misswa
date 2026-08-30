import secrets
from datetime import datetime, timedelta

from flask import Blueprint, redirect, render_template_string, request, session, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user

from app.extensions import db
from app.models import Mixer, OAuthState

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/mixer/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        mixer = Mixer.query.filter_by(email=email).first()
        if mixer and mixer.check_password(password):
            login_user(mixer)
            return redirect(url_for("mixers.dashboard"))
        flash("Identifiants invalides")
    return render_template_string('''
    <h2>Connexion mixeur</h2>
    <form method="post">
      <input name="email" placeholder="Email" required><br><br>
      <input name="password" type="password" placeholder="Mot de passe" required><br><br>
      <button type="submit">Connexion</button>
    </form>
        <p><a href="/google/connect">Connecter Google Calendar</a></p>
    ''')


@auth_bp.route("/mixer/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
