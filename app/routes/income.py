from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from app import db
from app.models import Income

income_bp = Blueprint("income", __name__)


def _parse_decimal(s):
    if not s:
        return None
    try:
        return Decimal(str(s).replace(".", "").replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None


@income_bp.route("/")
@login_required
def list_incomes():
    incomes = Income.query.filter_by(user_id=current_user.id)\
        .order_by(Income.received_at.desc()).all()
    total = sum(float(i.amount) for i in incomes)
    return render_template("income/list.html", incomes=incomes, total=total)


@income_bp.route("/<int:income_id>/toggle-recorrente", methods=["POST"])
@login_required
def toggle_recorrente(income_id):
    from app.models import Income
    inc = Income.query.get_or_404(income_id)
    if inc.user_id != current_user.id:
        abort(403)
    inc.is_recurring = not inc.is_recurring
    db.session.commit()
    from flask import jsonify
    return jsonify({"ok": True, "is_recurring": inc.is_recurring})


@income_bp.route("/novo", methods=["GET", "POST"])
@login_required
def new_income():
    if request.method == "POST":
        desc = request.form.get("description", "").strip()
        amount = _parse_decimal(request.form.get("amount"))
        cat = request.form.get("category", "Salário").strip() or "Salário"
        rec = request.form.get("is_recurring", "0") == "1"
        notes = request.form.get("notes", "").strip()
        d_str = request.form.get("received_at")

        if not desc or not amount or amount <= 0:
            flash("Descrição e valor são obrigatórios.", "danger")
            return render_template("income/form.html", income=None)

        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date() if d_str else date.today()
        except ValueError:
            d = date.today()

        i = Income(user_id=current_user.id, description=desc, amount=amount,
                   received_at=d, category=cat, is_recurring=rec, notes=notes)
        db.session.add(i)
        db.session.commit()
        flash("Renda registrada.", "success")
        return redirect(url_for("income.list_incomes"))

    return render_template("income/form.html", income=None)


@income_bp.route("/<int:income_id>/editar", methods=["GET", "POST"])
@login_required
def edit_income(income_id):
    i = Income.query.get_or_404(income_id)
    if i.user_id != current_user.id:
        abort(403)
    if request.method == "POST":
        i.description = request.form.get("description", i.description).strip()
        amount = _parse_decimal(request.form.get("amount"))
        if amount and amount > 0:
            i.amount = amount
        i.category = request.form.get("category", i.category).strip() or "Salário"
        i.is_recurring = request.form.get("is_recurring", "0") == "1"
        i.notes = request.form.get("notes", "").strip()
        d_str = request.form.get("received_at")
        if d_str:
            try:
                i.received_at = datetime.strptime(d_str, "%Y-%m-%d").date()
            except ValueError:
                pass
        db.session.commit()
        flash("Renda atualizada.", "success")
        return redirect(url_for("income.list_incomes"))
    return render_template("income/form.html", income=i)


@income_bp.route("/<int:income_id>/excluir", methods=["POST"])
@login_required
def delete_income(income_id):
    i = Income.query.get_or_404(income_id)
    if i.user_id != current_user.id:
        abort(403)
    db.session.delete(i)
    db.session.commit()
    flash("Renda removida.", "info")
    return redirect(url_for("income.list_incomes"))
