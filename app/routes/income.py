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
    """
    Rendas do mês escolhido (mesma regra do Fluxo de Caixa):
      - fixas (recorrentes) que começaram até o mês;
      - eventuais só do próprio mês.
    """
    from app.utils import get_open_billing_month
    mes = request.args.get("mes") or get_open_billing_month(
        current_user.id, date.today().strftime("%Y-%m"))
    try:
        ano, mm = int(mes[:4]), int(mes[5:7])
    except (ValueError, IndexError):
        ano, mm = date.today().year, date.today().month
    mes = f"{ano}-{mm:02d}"

    todas = Income.query.filter_by(user_id=current_user.id)\
        .order_by(Income.received_at.desc()).all()
    fixas = [i for i in todas if i.is_recurring
             and (i.received_at.year, i.received_at.month) <= (ano, mm)]
    eventuais = [i for i in todas if not i.is_recurring
                 and i.received_at.year == ano and i.received_at.month == mm]
    total_fixa = sum(float(i.amount) for i in fixas)
    total_eventual = sum(float(i.amount) for i in eventuais)

    MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
             "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
    prev_mes = f"{ano-1}-12" if mm == 1 else f"{ano}-{mm-1:02d}"
    next_mes = f"{ano+1}-01" if mm == 12 else f"{ano}-{mm+1:02d}"
    return render_template("income/list.html",
                           incomes=fixas + eventuais,
                           fixas=fixas, eventuais=eventuais,
                           total_fixa=total_fixa, total_eventual=total_eventual,
                           total=total_fixa + total_eventual,
                           mes=mes, mes_label=f"{MESES[mm-1]}/{ano}",
                           prev_mes=prev_mes, next_mes=next_mes)


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
 