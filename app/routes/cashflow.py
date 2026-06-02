from datetime import date
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from app.utils import get_yearly_cashflow

cashflow_bp = Blueprint("cashflow", __name__)


def _limpar_excedentes_invalidos():
    """Remove excedentes duplicados e órfãos do banco."""
    from app import db
    from app.models import Expense, ExpenseShare
    try:
        todos = Expense.query.filter(
            Expense.description.like("% - excedente %"),
            Expense.kind == "pontual"
        ).order_by(Expense.id).all()

        # Duplicatas
        seen = {}
        for exp in todos:
            key = (exp.payer_id, exp.description, exp.spent_at.year, exp.spent_at.month)
            if key in seen:
                ExpenseShare.query.filter_by(expense_id=exp.id).delete()
                db.session.delete(exp)
            else:
                seen[key] = exp.id

        # Órfãos
        todos2 = Expense.query.filter(
            Expense.description.like("% - excedente %"),
            Expense.kind == "pontual"
        ).all()
        for exp in todos2:
            parts = exp.description.split(" - excedente ")
            if len(parts) < 2:
                continue
            nome_base = parts[0].strip()
            original = Expense.query.filter(
                Expense.payer_id == exp.payer_id,
                Expense.description == nome_base,
                Expense.id != exp.id
            ).first()
            if not original:
                ExpenseShare.query.filter_by(expense_id=exp.id).delete()
                db.session.delete(exp)

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[cashflow] erro limpeza excedentes: {e}")


@cashflow_bp.route("/")
@login_required
def index():
    from app import db
    db.session.remove()
    year = request.args.get("year", type=int) or date.today().year
    _limpar_excedentes_invalidos()
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
def ajustar():
    """Salva ajuste manual de qualquer coluna do fluxo."""
    from flask_login import current_user
    from flask import request as req, jsonify
    from app.models import CashflowOverride
    from app import db
    from decimal import Decimal, InvalidOperation
    if not current_user.is_authenticated:
        return jsonify({"ok": False, "error": "sessao_expirada"}), 401
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

    field_map = {
        "net": "net_override",
        "cumulative": "cumulative_override",
        "income_recurring": "income_recurring_override",
        "income_eventual": "income_eventual_override",
        "fixed": "fixed_override",
        "eventual": "eventual_override",
    }
    col = field_map.get(field)
    if col:
        setattr(override, col, v)

    try:
        db.session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.session.rollback()
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


@cashflow_bp.route("/debug-junho")
@login_required
def debug_junho():
    from flask import jsonify
    from app.models import Expense, ExpenseShare
    from sqlalchemy import extract
    shares = ExpenseShare.query.filter_by(user_id=current_user.id).all()
    result = []
    for s in shares:
        exp = Expense.query.get(s.expense_id)
        if not exp:
            continue
        if not (exp.spent_at.year == 2026 and exp.spent_at.month == 6):
            if exp.kind != "pontual":
                continue
            if not exp.is_active_on(2026, 6):
                continue
        if exp.kind == "pontual" and not (exp.spent_at.year == 2026 and exp.spent_at.month == 6):
            continue
        result.append({
            "id": exp.id,
            "desc": exp.description,
            "kind": exp.kind,
            "amount": float(exp.amount),
            "share_amount": float(s.share_amount),
            "spent_at": str(exp.spent_at),
            "share_mode": exp.share_mode,
        })
    return jsonify(sorted(result, key=lambda x: x["desc"]))


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


@cashflow_bp.route("/items-json")
@login_required
def items_json():
    """Retorna todos os itens detalhados por mês e tipo."""
    from flask import jsonify, request as req
    year = req.args.get("year", type=int) or date.today().year
    col = req.args.get("col", "eventual")
    months = get_yearly_cashflow(current_user.id, year)
    key_map = {
        "eventual": "eventual_items",
        "fixed": "fixed_items",
        "income_recurring": "income_recurring_items",
        "income_eventual": "income_eventual_items",
    }
    key = key_map.get(col, "eventual_items")
    data = [m.get(key, []) for m in months]
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
