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
    from app.models import Card, CardEntry, Expense, PaymentPlan, PaymentItem, PaymentCardStatus
    from app.utils import get_billing_month
    from datetime import date as _dt

    # Filtro de mês
    today = _dt.today()
    mes_filter = request.args.get("mes", today.strftime("%Y-%m"))
    try:
        filter_year  = int(mes_filter[:4])
        filter_month = int(mes_filter[5:7])
    except Exception:
        filter_year, filter_month = today.year, today.month
        mes_filter = today.strftime("%Y-%m")

    if filter_month == 1:
        prev_mes = f"{filter_year-1}-12"
    else:
        prev_mes = f"{filter_year}-{filter_month-1:02d}"
    if filter_month == 12:
        next_mes = f"{filter_year+1}-01"
    else:
        next_mes = f"{filter_year}-{filter_month+1:02d}"

    MESES_PT = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho",
                "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
    mes_label = f"{MESES_PT[filter_month-1]}/{filter_year}"

    # Plano do mês — usa mes_filter como chave
    plan = PaymentPlan.query.filter_by(
        user_id=current_user.id, mes_ref=mes_filter
    ).first()
    if not plan:
        try:
            plan = PaymentPlan(user_id=current_user.id, saldo_inicial=0, mes_ref=mes_filter)
            db.session.add(plan)
            db.session.commit()
        except Exception:
            db.session.rollback()
            plan = PaymentPlan.query.filter_by(
                user_id=current_user.id, mes_ref=mes_filter
            ).first()
            if not plan:
                plan = PaymentPlan(user_id=current_user.id, saldo_inicial=0, mes_ref=mes_filter)
                db.session.add(plan)
                db.session.commit()

    # Cartões ativos — total filtrado pelo mês da fatura (billing_month)
    cards = Card.query.filter_by(user_id=current_user.id, is_active=True).order_by(Card.name).all()
    card_closing = {c.id: c.closing_day for c in cards}

    all_entries = CardEntry.query.filter(
        CardEntry.card_id.in_([c.id for c in cards]),
        CardEntry.status == "ativo",
    ).all()

    card_totals = {c.id: 0.0 for c in cards}
    for entry in all_entries:
        if entry.billing_month:
            bm = entry.billing_month
            matches = (bm == mes_filter)
        else:
            byr, bmo = get_billing_month(entry.entry_date, card_closing.get(entry.card_id))
            matches = (byr == filter_year and bmo == filter_month)
        if matches:
            card_totals[entry.card_id] = card_totals.get(entry.card_id, 0.0) + float(entry.amount)

    for k in card_totals:
        card_totals[k] = round(card_totals[k], 2)

    # Status de pagamento de cada cartão no mês
    card_status = {}
    for card in cards:
        cs = PaymentCardStatus.query.filter_by(plan_id=plan.id, card_id=card.id).first()
        if not cs:
            from datetime import date as _d2
            due = None
            if card.due_day:
                try:
                    due = _d2(filter_year, filter_month, card.due_day)
                except Exception:
                    due = None
            cs = PaymentCardStatus(plan_id=plan.id, card_id=card.id, is_paid=False, due_date=due)
            db.session.add(cs)
        card_status[card.id] = cs
    db.session.commit()

    # Gastos recorrentes do usuário (para seleção)
    fixed_expenses = Expense.query.filter(
        Expense.payer_id == current_user.id,
        Expense.kind == "recorrente",
    ).order_by(Expense.description).all()

    # Itens do plano do mês
    plan_items = PaymentItem.query.filter_by(plan_id=plan.id).order_by(PaymentItem.id).all()

    total_debitos = round(sum(float(item.amount) for item in plan_items), 2)
    total_cartoes = round(sum(card_totals.values()), 2)
    saldo_atualizado = round(float(plan.saldo_inicial) - total_debitos - total_cartoes, 2)

    return render_template("payments/index.html",
                           plan=plan,
                           cards=cards,
                           card_totals=card_totals,
                           fixed_expenses=fixed_expenses,
                           plan_items=plan_items,
                           total_debitos=total_debitos,
                           total_cartoes=total_cartoes,
                           saldo_atualizado=saldo_atualizado,
                           card_status=card_status,
                           mes_filter=mes_filter,
                           mes_label=mes_label,
                           prev_mes=prev_mes,
                           next_mes=next_mes)


@payments_bp.route("/saldo", methods=["POST"])
@login_required
def update_saldo():
    from app.models import PaymentPlan
    mes = request.args.get("mes", "")
    plan = PaymentPlan.query.filter_by(user_id=current_user.id, mes_ref=mes).first()
    if not plan:
        plan = PaymentPlan(user_id=current_user.id, mes_ref=mes)
        db.session.add(plan)
    plan.saldo_inicial = _parse(request.form.get("saldo_inicial", "0"))
    db.session.commit()
    return redirect(url_for("payments.index", mes=mes))


@payments_bp.route("/item/add", methods=["POST"])
@login_required
def add_item():
    from app.models import PaymentPlan, PaymentItem
    mes = request.args.get("mes", "")
    plan = PaymentPlan.query.filter_by(user_id=current_user.id, mes_ref=mes).first()
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
    return redirect(url_for("payments.index", mes=mes))


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
    mes = request.args.get("mes", "")
    plan = PaymentPlan.query.filter_by(user_id=current_user.id, mes_ref=mes).first()
    if plan:
        PaymentItem.query.filter_by(plan_id=plan.id).delete()
        plan.saldo_inicial = 0
        db.session.commit()
    flash("Plano de pagamento reiniciado.", "info")
    return redirect(url_for("payments.index", mes=mes))


@payments_bp.route("/item/<int:item_id>/toggle", methods=["POST"])
@login_required
def toggle_item(item_id):
    from app.models import PaymentItem, PaymentPlan
    item = PaymentItem.query.get_or_404(item_id)
    plan = PaymentPlan.query.get(item.plan_id)
    if plan.user_id != current_user.id:
        from flask import abort
        abort(403)
    item.is_paid = not item.is_paid
    db.session.commit()
    mes = plan.mes_ref
    return redirect(url_for("payments.index", mes=mes))


@payments_bp.route("/item/<int:item_id>/due", methods=["POST"])
@login_required
def set_item_due(item_id):
    from app.models import PaymentItem, PaymentPlan
    from datetime import datetime as _dt2
    item = PaymentItem.query.get_or_404(item_id)
    plan = PaymentPlan.query.get(item.plan_id)
    if plan.user_id != current_user.id:
        from flask import abort
        abort(403)
    d_str = request.form.get("due_date", "")
    try:
        item.due_date = _dt2.strptime(d_str, "%Y-%m-%d").date()
    except Exception:
        item.due_date = None
    db.session.commit()
    return redirect(url_for("payments.index", mes=plan.mes_ref))


@payments_bp.route("/card/<int:card_id>/toggle", methods=["POST"])
@login_required
def toggle_card(card_id):
    from app.models import PaymentPlan, PaymentCardStatus
    mes = request.args.get("mes", "")
    plan = PaymentPlan.query.filter_by(user_id=current_user.id, mes_ref=mes).first()
    if not plan:
        return redirect(url_for("payments.index", mes=mes))
    cs = PaymentCardStatus.query.filter_by(plan_id=plan.id, card_id=card_id).first()
    if cs:
        cs.is_paid = not cs.is_paid
        db.session.commit()
    return redirect(url_for("payments.index", mes=mes))


@payments_bp.route("/card/<int:card_id>/due", methods=["POST"])
@login_required
def set_card_due(card_id):
    from app.models import PaymentPlan, PaymentCardStatus
    from datetime import datetime as _dt3
    mes = request.args.get("mes", "")
    plan = PaymentPlan.query.filter_by(user_id=current_user.id, mes_ref=mes).first()
    if not plan:
        return redirect(url_for("payments.index", mes=mes))
    cs = PaymentCardStatus.query.filter_by(plan_id=plan.id, card_id=card_id).first()
    if cs:
        d_str = request.form.get("due_date", "")
        try:
            cs.due_date = _dt3.strptime(d_str, "%Y-%m-%d").date()
        except Exception:
            cs.due_date = None
        db.session.commit()
    return redirect(url_for("payments.index", mes=mes))
