from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from app import db
from app.models import Investment

investments_bp = Blueprint("investments", __name__)

CATEGORIES = ["Renda Fixa", "Renda Variável", "FII", "Ações", "Cripto",
              "Previdência", "Tesouro Direto", "CDB", "LCI/LCA", "Fundos", "Outros"]

OBJECTIVES = ["Reserva de Emergência", "Aposentadoria", "Viagem",
              "Educação", "Imóvel", "Carro", "Liberdade Financeira", "Outros"]


def _parse(s):
    if not s:
        return None
    try:
        return Decimal(str(s).replace(".", "").replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None


@investments_bp.route("/")
@login_required
def list_investments():
    obj_filter = request.args.get("objective", "")
    cat_filter = request.args.get("category", "")

    q = Investment.query.filter_by(user_id=current_user.id)
    if obj_filter:
        q = q.filter_by(objective=obj_filter)
    if cat_filter:
        q = q.filter_by(category=cat_filter)
    investments = q.order_by(Investment.invested_at.desc()).all()

    # Agrupado por objetivo
    by_objective = {}
    for inv in investments:
        obj = inv.objective or "Outros"
        if obj not in by_objective:
            by_objective[obj] = {"inv_list": [], "total_invested": 0, "total_current": 0}
        by_objective[obj]["inv_list"].append(inv)
        by_objective[obj]["total_invested"] += float(inv.amount)
        by_objective[obj]["total_current"] += float(inv.current_value or inv.amount)

    total_invested = sum(float(i.amount) for i in investments)
    total_current = sum(float(i.current_value or i.amount) for i in investments)

    objectives = Investment.query.filter_by(user_id=current_user.id)\
        .with_entities(Investment.objective).distinct().all()
    objectives = [o[0] for o in objectives]

    return render_template("investments/list.html",
                           investments=investments,
                           by_objective=by_objective,
                           total_invested=total_invested,
                           total_current=total_current,
                           total_gain=total_current - total_invested,
                           objectives=objectives,
                           all_objectives=OBJECTIVES,
                           all_categories=CATEGORIES,
                           obj_filter=obj_filter,
                           cat_filter=cat_filter)


@investments_bp.route("/novo", methods=["GET", "POST"])
@login_required
def new_investment():
    if request.method == "POST":
        return _save(None)
    return render_template("investments/form.html", inv=None,
                           categories=CATEGORIES, objectives=OBJECTIVES)


@investments_bp.route("/<int:inv_id>/editar", methods=["GET", "POST"])
@login_required
def edit_investment(inv_id):
    inv = Investment.query.get_or_404(inv_id)
    if inv.user_id != current_user.id:
        abort(403)
    if request.method == "POST":
        return _save(inv)
    return render_template("investments/form.html", inv=inv,
                           categories=CATEGORIES, objectives=OBJECTIVES)


def _save(inv):
    desc = request.form.get("description", "").strip()
    amount = _parse(request.form.get("amount"))
    current_value = _parse(request.form.get("current_value"))
    cat = request.form.get("category", "Renda Fixa")
    obj = request.form.get("objective", "").strip()
    institution = request.form.get("institution", "").strip()
    notes = request.form.get("notes", "").strip()
    d_str = request.form.get("invested_at")
    is_active = bool(request.form.get("is_active", True))

    if not desc or not amount or amount <= 0 or not obj:
        flash("Descrição, valor e objetivo são obrigatórios.", "danger")
        return render_template("investments/form.html", inv=inv,
                               categories=CATEGORIES, objectives=OBJECTIVES)
    try:
        d = datetime.strptime(d_str, "%Y-%m-%d").date() if d_str else date.today()
    except ValueError:
        d = date.today()

    if inv is None:
        inv = Investment(user_id=current_user.id)
        db.session.add(inv)

    inv.description = desc
    inv.amount = amount
    inv.current_value = current_value
    inv.category = cat
    inv.objective = obj
    inv.institution = institution
    inv.notes = notes
    inv.invested_at = d
    inv.is_active = is_active

    db.session.commit()
    flash("Investimento salvo.", "success")
    return redirect(url_for("investments.list_investments"))


@investments_bp.route("/<int:inv_id>/excluir", methods=["POST"])
@login_required
def delete_investment(inv_id):
    inv = Investment.query.get_or_404(inv_id)
    if inv.user_id != current_user.id:
        abort(403)
    db.session.delete(inv)
    db.session.commit()
    flash("Investimento removido.", "info")
    return redirect(url_for("investments.list_investments"))
