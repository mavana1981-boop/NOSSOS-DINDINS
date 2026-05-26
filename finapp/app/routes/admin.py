from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash
from app import db
from app.models import User
from app.utils import save_profile_photo

admin_bp = Blueprint("admin", __name__)


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


@admin_bp.route("/usuarios")
@login_required
@admin_required
def list_users():
    users = User.query.order_by(User.full_name).all()
    return render_template("admin/users.html", users=users)


@admin_bp.route("/usuarios/novo", methods=["GET", "POST"])
@login_required
@admin_required
def new_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        is_admin = bool(request.form.get("is_admin"))

        if not all([username, full_name, email, password]):
            flash("Preencha todos os campos obrigatórios.", "danger")
            return render_template("admin/user_form.html", user=None)

        if User.query.filter_by(username=username).first():
            flash("Já existe usuário com este login.", "danger")
            return render_template("admin/user_form.html", user=None)
        if User.query.filter_by(email=email).first():
            flash("Já existe usuário com este email.", "danger")
            return render_template("admin/user_form.html", user=None)

        photo_name = None
        if "photo" in request.files:
            photo_name = save_profile_photo(request.files["photo"])

        u = User(
            username=username,
            full_name=full_name,
            email=email,
            password_hash=generate_password_hash(password),
            is_admin=is_admin,
            photo=photo_name,
        )
        db.session.add(u)
        db.session.commit()
        flash("Usuário cadastrado com sucesso.", "success")
        return redirect(url_for("admin.list_users"))

    return render_template("admin/user_form.html", user=None)


@admin_bp.route("/usuarios/<int:user_id>/editar", methods=["GET", "POST"])
@login_required
@admin_required
def edit_user(user_id):
    u = User.query.get_or_404(user_id)
    if request.method == "POST":
        u.full_name = request.form.get("full_name", u.full_name).strip()
        new_email = request.form.get("email", u.email).strip().lower()
        if new_email != u.email and User.query.filter_by(email=new_email).first():
            flash("Email já está em uso.", "danger")
            return render_template("admin/user_form.html", user=u)
        u.email = new_email
        u.is_admin = bool(request.form.get("is_admin"))

        new_password = request.form.get("password")
        if new_password:
            u.password_hash = generate_password_hash(new_password)

        if "photo" in request.files and request.files["photo"].filename:
            photo_name = save_profile_photo(request.files["photo"])
            if photo_name:
                u.photo = photo_name

        db.session.commit()
        flash("Usuário atualizado.", "success")
        return redirect(url_for("admin.list_users"))

    return render_template("admin/user_form.html", user=u)


@admin_bp.route("/usuarios/<int:user_id>/excluir", methods=["POST"])
@login_required
@admin_required
def delete_user(user_id):
    u = User.query.get_or_404(user_id)
    if u.id == current_user.id:
        flash("Você não pode excluir a si mesmo.", "danger")
        return redirect(url_for("admin.list_users"))
    db.session.delete(u)
    db.session.commit()
    flash("Usuário excluído.", "info")
    return redirect(url_for("admin.list_users"))
