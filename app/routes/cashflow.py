from datetime import date
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from app.utils import get_yearly_cashflow
from app import db

cashflow_bp = Blueprint("cashflow", __name__)


def _limpar_excedentes_invalidos():
    from app.models import Expense, ExpenseShare
    try:
        todos = Expense.query.filter(
            Expense.description.like("% - excedente %"),
            Expense.kind == "pontual"
        ).order_by(Expense.id).all()
        seen = {}
        for exp in todos:
            key = (exp.payer_id, exp.description, exp.spent_at.year, exp.spent_at.month)
            if key in seen:
                ExpenseShare.query.filter_by(expense_id=exp.id).delete()
                db.session.delete(exp)
            else:
                seen[key] = exp.id
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
    db.session.remove()
    db.session.expire_all() if hasattr(db.session, "expire_all") else None
    year = request.args.get("year", type=int) or date.today().year
    _limpar_excedentes_invalidos()
    months = get_yearly_cashflow(current_user.id, year)

    jan_next = get_yearly_cashflow(current_user.id, year + 1)
    if jan_next:
        jan = dict(jan_next[0])
        jan["is_next_year"] = True
        if "eventual_items" not in jan:
            jan["eventual_items"] = []
        dec_cumulative = months[-1]["cumulative"] if months else 0.0
        jan["cumulative"] = dec_cumulative + jan["net"]
        months = months + [jan]

    months12 = months[:12]
    totals = {
        "income":           sum(m["income"] for m in months12),
        "income_recurring": sum(m["income_recurring"] for m in months12),
        "income_eventual":  sum(m["income_eventual"] for m in months12),
        "fixed":            sum(m["fixed_expense"] for m in months12),
        "eventual":         sum(m["eventual_expense"] for m in months12),
        "net":              sum(m["net"] for m in months12),
    }
    totals["total_expense"] = totals["fixed"] + totals["eventual"]
    max_value = max(
        max((m["income"] for m in months), default=0),
        max((m["total_expense"] for m in months), default=0),
        1,
    )
    # Dados do mês atual para os cards
    today = date.today()
    current_month = next(
        (m for m in months if m["month"] == today.month and not m.get("is_next_year")),
        months[0] if months else {}
    )
    # Saldo do ano = acumulado de dezembro
    dec = next((m for m in months12 if m["month"] == 12), months12[-1] if months12 else {})

    return render_template("cashflow.html",
                           year=year, months=months, totals=totals,
                           max_value=max_value,
                           current_year=today.year,
                           current_month=current_month,
                           saldo_ano=dec.get("cumulative", 0))


@cashflow_bp.route("/ajustar", methods=["POST"])
def ajustar():
    """Salva ajuste manual de qualquer coluna do fluxo."""
    from flask import request as req, jsonify
    from app.models import CashflowOverride
    from decimal import Decimal, InvalidOperation
    if not current_user.is_authenticated:
        return jsonify({"ok": False, "error": "sessao_expirada"}), 401
    year  = req.form.get("year", type=int)
    month = req.form.get("month", type=int)
    field = req.form.get("field")
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
        "net":              "net_override",
        "cumulative":       "cumulative_override",
        "income_recurring": "income_recurring_override",
        "income_eventual":  "income_eventual_override",
        "fixed":            "fixed_override",
        "eventual":         "eventual_override",
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


@cashflow_bp.route("/debug-entries")
@login_required
def debug_entries():
    from flask import jsonify
    from app.models import CardEntry, Card
    # Mostra todos os cartões e entries do sistema
    all_cards = Card.query.all()
    my_cards = Card.query.filter_by(user_id=current_user.id).all()
    entries = CardEntry.query.limit(10).all()
    return jsonify({
        "current_user_id": current_user.id,
        "total_cards": len(all_cards),
        "my_cards": [{"id": c.id, "name": c.name, "user_id": c.user_id} for c in my_cards],
        "sample_entries": [{
            "id": e.id,
            "desc": e.description[:40],
            "kind": e.kind,
            "installments": e.installments,
            "installment_no": e.installment_no,
            "status": e.status,
            "user_id": e.user_id,
            "card_id": e.card_id,
        } for e in entries]
    })

@cashflow_bp.route("/debug-billing")
@login_required
def debug_billing():
    from flask import jsonify
    from app.models import CardEntry, Card
    from sqlalchemy import text
    # Verifica coluna diretamente no banco
    with db.engine.connect() as conn:
        result = conn.execute(text("""
            SELECT 
                COUNT(*) as total,
                COUNT(billing_month) as com_billing,
                SUM(CASE WHEN billing_month IS NULL THEN 1 ELSE 0 END) as sem_billing,
                array_agg(DISTINCT billing_month) as valores
            FROM card_entries
            WHERE card_id IN (
                SELECT id FROM cards WHERE user_id = :uid AND is_active = true
            )
        """), {"uid": current_user.id})
        row = result.fetchone()
    return jsonify({
        "total": row[0],
        "com_billing_month": row[1],
        "sem_billing_month_null": row[2],
        "valores_billing_month": str(row[3]),
    })

@cashflow_bp.route("/items-json")
@login_required
def items_json():
    from flask import jsonify, request as req
    year = req.args.get("year", type=int) or date.today().year
    col  = req.args.get("col", "eventual")
    months = get_yearly_cashflow(current_user.id, year)
    key_map = {
        "eventual":         "eventual_items",
        "fixed":            "fixed_items",
        "income_recurring": "income_recurring_items",
        "income_eventual":  "income_eventual_items",
    }
    key  = key_map.get(col, "eventual_items")
    data = [m.get(key, []) for m in months]
    return jsonify(data)
