from datetime import date
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from app.utils import get_yearly_cashflow

cashflow_bp = Blueprint("cashflow", __name__)


@cashflow_bp.route("/")
@login_required
def index():
    year = request.args.get("year", type=int) or date.today().year
    months = get_yearly_cashflow(current_user.id, year)

    # Adiciona Janeiro do ano seguinte com acumulado continuado
    jan_next = get_yearly_cashflow(current_user.id, year + 1)
    if jan_next:
        jan = dict(jan_next[0])
        jan["is_next_year"] = True
        if "eventual_items" not in jan:
            jan["eventual_items"] = []
        # Acumulado continua a partir do acumulado de dezembro
        dec_cumulative = months[-1]["cumulative"] if months else 0.0
        jan["cumulative"] = dec_cumulative + jan["net"]
        months = months + [jan]

    # Totais apenas dos 12 meses do ano atual
    months12 = months[:12]
    totals = {
        "income": sum(m["income"] for m in months12),
        "income_recurring": sum(m["income_recurring"] for m in months12),
        "income_eventual": sum(m["income_eventual"] for m in months12),
        "fixed": sum(m["fixed_expense"] for m in months12),
        "eventual": sum(m["eventual_expense"] for m in months12),
        "net": sum(m["net"] for m in months12),
    }
    totals["total_expense"] = totals["fixed"] + totals["eventual"]

    max_value = max(
        max((m["income"] for m in months), default=0),
        max((m["total_expense"] for m in months), default=0),
        1,
    )

    return render_template("cashflow.html",
                           year=year, months=months, totals=totals,
                           max_value=max_value,
                           current_year=date.today().year)


@cashflow_bp.route("/ajustar", methods=["POST"])
@login_required
def ajustar():
    """Salva ajuste manual de saldo/acumulado."""
    from flask import request as req, jsonify
    from app.models import CashflowOverride
    from decimal import Decimal, InvalidOperation
    year = req.form.get("year", type=int)
    month = req.form.get("month", type=int)
    field = req.form.get("field")  # "net" ou "cumulative"
    value = req.form.get("value", "").strip()

    def parse(s):
        try:
            return Decimal(str(s).replace(".", "").replace(",", "."))
        except (InvalidOperation, ValueError):
            return None

    v = parse(value)
    override = CashflowOverride.query.filter_by(
        user_id=current_user.id, year=year, month=month
    ).first()
    if override is None:
        override = CashflowOverride(user_id=current_user.id, year=year, month=month)
        db.session.add(override)

    if field == "net":
        override.net_override = v
    elif field == "cumulative":
        override.cumulative_override = v

    db.session.commit()
    return jsonify({"ok": True})


@cashflow_bp.route("/eventual-json")
@login_required
def eventual_json():
    from flask import jsonify, request as req
    year = req.args.get("year", type=int) or date.today().year
    months = get_yearly_cashflow(current_user.id, year)
    data = [m.get("eventual_items", []) for m in months]
    return jsonify(data)


@cashflow_bp.route("/fixed-json")
@login_required
def fixed_json():
    from flask import jsonify, request as req
    year = req.args.get("year", type=int) or date.today().year
    months = get_yearly_cashflow(current_user.id, year)
    data = [m.get("fixed_items", []) for m in months]
    return jsonify(data)


@cashflow_bp.route("/debug-fixos")
@login_required
def debug_fixos():
    """Mostra todos os gastos fixos e seus dados para debug."""
    from flask import jsonify
    from app.models import Expense, ExpenseShare
    expenses = db.session.query(Expense, ExpenseShare)        .join(ExpenseShare, ExpenseShare.expense_id == Expense.id)        .filter(ExpenseShare.user_id == current_user.id).all()
    result = []
    for exp, share in expenses:
        result.append({
            "id": exp.id,
            "desc": exp.description,
            "kind": exp.kind,
            "spent_at": str(exp.spent_at),
            "amount": float(exp.amount),
            "recurrence_months": exp.recurrence_months,
            "active_jun": exp.is_active_on(2026, 6),
            "active_jul": exp.is_active_on(2026, 7),
        })
    return jsonify(sorted(result, key=lambda x: x["desc"]))
