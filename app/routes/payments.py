from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app import db
from decimal import Decimal, InvalidOperation

payments_bp = Blueprint("payments", __name__)


def _parse(s):
    if not s:
        return Decimal("0")
    try:
        return Decimal(str(s).replace(".", "").replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return Decimal("0")


@payments_bp.route("/", methods=["GET"])
@login_required
def index():
    from app.models import Card, CardEntry, Expense, ExpenseShare, PaymentPlan, PaymentItem

    # Plano atual do usuário (ou cria vazio)
    plan = PaymentPlan.query.filter_by(user_id=current_user.id).first()
    if not plan:
        plan = PaymentPlan(user_id=current_user.id, saldo_inicial=0)
        db.session.add(plan)
        db.session.commit()

    # Cartões ativos com total da fatura atual
    cards = Card.query.filter_by(user_id=current_user.id, is_active=True).order_by(Card.name).all()
    card_ids = [c.id for c in cards]

    card_totals = {}
    for card in cards:
        entries = CardEntry.query.filter(
            CardEntry.card_id == card.id,
            CardEntry.status == "ativo",
        ).all()
        card_totals[card.id] = round(sum(float(e.amount) for e in entries), 2)

    # Gastos recorrentes do usuário
    fixed_expenses = Expense.query.filter(
        Expense.payer_id == current_user.id,
        Expense.kind == "recorrente",
    ).order_by(Expense.description).all()

    # Itens do plano atual
    plan_items = PaymentItem.query.filter_by(plan_id=plan.id).order_by(PaymentItem.id).all()

    # Calcular saldo atualizado
    total_debitos = sum(float(item.amount) for item in plan_items)
    total_cartoes = sum(
        card_totals[c.id] for c in cards if card_totals.get(c.id, 0) > 0
    )
    saldo_atualizado = float(plan.saldo_inicial) - total_debitos - total_cartoes

    return render_template("payments/index.html",
                           plan=plan,
                           cards=cards,
                           card_totals=card_totals,
                           fixed_expenses=fixed_expenses,
                           plan_items=plan_items,
                           total_debitos=total_debitos,
                           total_cartoes=total_cartoes,
                           saldo_atualizado=saldo_atualizado)


@payments_bp.route("/saldo", methods=["POST"])
@login_required
def update_saldo():
    from app.models import PaymentPlan
    plan = PaymentPlan.query.filter_by(user_id=current_user.id).first()
    if not plan:
        plan = PaymentPlan(user_id=current_user.id)
        db.session.add(plan)
    plan.saldo_inicial = _parse(request.form.get("saldo_inicial", "0"))
    db.session.commit()
    return redirect(url_for("payments.index"))


@payments_bp.route("/item/add", methods=["POST"])
@login_required
def add_item():
    from app.models import PaymentPlan, PaymentItem
    plan = PaymentPlan.query.filter_by(user_id=current_user.id).first()
    if not plan:
        return redirect(url_for("payments.index"))

    desc = request.form.get("description", "").strip()
    amount = _parse(request.form.get("amount", "0"))
    expense_id_raw = request.form.get("expense_id", "").strip()
    expense_id = int(expense_id_raw) if expense_id_raw.isdigit() else None

    if not desc or amount <= 0:
        flash("Descrição e valor são obrigatórios.", "danger")
        return redirect(url_for("payments.index"))

    item = PaymentItem(
        plan_id=plan.id,
        description=desc,
        amount=amount,
        expense_id=expense_id,
    )
    db.session.add(item)
    db.session.commit()
    return redirect(url_for("payments.index"))


@payments_bp.route("/item/<int:item_id>/remove", methods=["POST"])
@login_required
def remove_item(item_id):
    from app.models import PaymentItem, PaymentPlan
    item = PaymentItem.query.get_or_404(item_id)
    plan = PaymentPlan.query.get(item.plan_id)
    if plan.user_id != current_user.id:
        return jsonify({"error": "forbidden"}), 403
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("payments.index"))


@payments_bp.route("/reset", methods=["POST"])
@login_required
def reset_plan():
    from app.models import PaymentPlan, PaymentItem
    plan = PaymentPlan.query.filter_by(user_id=current_user.id).first()
    if plan:
        PaymentItem.query.filter_by(plan_id=plan.id).delete()
        plan.saldo_inicial = 0
        db.session.commit()
    flash("Plano de pagamento reiniciado.", "info")
    return redirect(url_for("payments.index"))
