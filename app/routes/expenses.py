from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from sqlalchemy import or_
from app import db
from app.models import Expense, ExpenseShare, User, Card, CardEntry, HouseholdExpense

expenses_bp = Blueprint("expenses", __name__)

CATEGORIES = ['Alimentação', 'Transporte', 'Saúde', 'Educação', 'Moradia',
              'Lazer', 'Vestuário', 'Beleza', 'Serviços', 'Contas', 'Outros']


def _parse_decimal(s):
    if not s:
        return None
    try:
        return Decimal(str(s).replace(".", "").replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None


def _sync_card_entry(expense, old_card_id=None):
    """
    Sincroniza o lançamento no cartão conforme o vínculo do gasto.
    - Se tinha cartão antigo diferente do novo: remove entrada antiga
    - Se tem cartão novo: cria ou atualiza entrada
    - Se removeu cartão: remove entrada existente
    """
    # Remove entrada do cartão antigo se mudou de cartão
    if old_card_id and old_card_id != expense.card_id:
        old_entry = CardEntry.query.filter_by(
            expense_id=expense.id, card_id=old_card_id
        ).first()
        if old_entry:
            db.session.delete(old_entry)

    # Remove entrada se cartão foi desvinculado
    if not expense.card_id:
        entries = CardEntry.query.filter_by(expense_id=expense.id).all()
        for e in entries:
            db.session.delete(e)
        return

    # Cria ou atualiza entrada no cartão atual
    entry = CardEntry.query.filter_by(
        expense_id=expense.id, card_id=expense.card_id
    ).first()

    if entry is None:
        entry = CardEntry(
            card_id=expense.card_id,
            user_id=expense.payer_id,
            expense_id=expense.id,
        )
        db.session.add(entry)

    entry.description = expense.description
    entry.amount = expense.amount
    entry.entry_date = expense.spent_at
    entry.category = expense.category
    entry.installments = expense.recurrence_months or 1
    entry.installment_no = 1
    entry.notes = f"Lançamento automático — {expense.kind}"


@expenses_bp.route("/")
@login_required
def list_expenses():
    q_text      = request.args.get("q", "").strip()
    cat_filter  = request.args.get("category", "")
    share_filter = request.args.get("share_mode", "")
    date_from   = request.args.get("date_from", "")
    date_to     = request.args.get("date_to", "")
    val_min     = request.args.get("val_min", "")
    val_max     = request.args.get("val_max", "")

    query = Expense.query.outerjoin(ExpenseShare).filter(
        or_(Expense.payer_id == current_user.id,
            ExpenseShare.user_id == current_user.id)
    ).distinct()

    if q_text:
        query = query.filter(Expense.description.ilike(f"%{q_text}%"))
    if cat_filter:
        query = query.filter(Expense.category == cat_filter)
    if share_filter:
        query = query.filter(Expense.share_mode == share_filter)
    if date_from:
        try:
            query = query.filter(Expense.spent_at >= datetime.strptime(date_from, "%Y-%m-%d").date())
        except ValueError:
            pass
    if date_to:
        try:
            query = query.filter(Expense.spent_at <= datetime.strptime(date_to, "%Y-%m-%d").date())
        except ValueError:
            pass
    if val_min:
        v = _parse_decimal(val_min)
        if v:
            query = query.filter(Expense.amount >= v)
    if val_max:
        v = _parse_decimal(val_max)
        if v:
            query = query.filter(Expense.amount <= v)

    expenses = query.order_by(Expense.spent_at.desc()).all()

    total_paid = sum(float(e.amount) for e in expenses if e.payer_id == current_user.id)
    total_my_share = 0
    for e in expenses:
        for s in e.shares:
            if s.user_id == current_user.id:
                total_my_share += float(s.share_amount)

    # Cartões do usuário para exibir nome na lista
    user_cards = {c.id: c for c in Card.query.filter_by(
        user_id=current_user.id, is_active=True).all()}

    return render_template("expenses/list.html",
                           expenses=expenses,
                           total_paid=total_paid,
                           total_my_share=total_my_share,
                           categories=CATEGORIES,
                           user_cards=user_cards,
                           q=q_text, cat_filter=cat_filter,
                           share_filter=share_filter,
                           date_from=date_from, date_to=date_to,
                           val_min=val_min, val_max=val_max)


@expenses_bp.route("/novo", methods=["GET", "POST"])
@login_required
def new_expense():
    users = User.query.order_by(User.full_name).all()
    user_cards = Card.query.filter_by(user_id=current_user.id, is_active=True).all()
    other_users = User.query.filter(User.id != current_user.id).order_by(User.full_name).all()
    if request.method == "POST":
        return _save_expense(None, users, user_cards)
    return render_template("expenses/form.html", expense=None,
                           users=users, user_cards=user_cards, other_users=other_users)


@expenses_bp.route("/<int:expense_id>/editar", methods=["GET", "POST"])
@login_required
def edit_expense(expense_id):
    e = Expense.query.get_or_404(expense_id)
    if e.payer_id != current_user.id and not current_user.is_admin:
        abort(403)
    users = User.query.order_by(User.full_name).all()
    user_cards = Card.query.filter_by(user_id=current_user.id, is_active=True).all()
    other_users = User.query.filter(User.id != current_user.id).order_by(User.full_name).all()
    if request.method == "POST":
        return _save_expense(e, users, user_cards)
    return render_template("expenses/form.html", expense=e,
                           users=users, user_cards=user_cards, other_users=other_users)


def _save_expense(expense, users, user_cards):
    desc = request.form.get("description", "").strip()
    amount = _parse_decimal(request.form.get("amount"))
    cat = request.form.get("category", "Outros").strip() or "Outros"
    notes = request.form.get("notes", "").strip()
    d_str = request.form.get("spent_at")
    share_mode = request.form.get("share_mode", "solo")
    payer_id = int(request.form.get("payer_id", current_user.id))
    kind = request.form.get("kind", "pontual")
    rec_months_raw = request.form.get("recurrence_months", "").strip()
    card_id_raw = request.form.get("card_id", "").strip()

    if not desc or not amount or amount <= 0:
        flash("Descrição e valor são obrigatórios.", "danger")
        return render_template("expenses/form.html", expense=expense,
                               users=users, user_cards=user_cards)

    try:
        d = datetime.strptime(d_str, "%Y-%m-%d").date() if d_str else date.today()
    except ValueError:
        d = date.today()

    if kind not in ("pontual", "recorrente"):
        kind = "pontual"
    recurrence_months = None
    if kind == "recorrente" and rec_months_raw:
        try:
            recurrence_months = max(1, min(360, int(rec_months_raw)))
        except ValueError:
            pass

    if payer_id != current_user.id and not current_user.is_admin:
        payer_id = current_user.id

    # Cartão vinculado
    new_card_id = int(card_id_raw) if card_id_raw.isdigit() else None
    old_card_id = expense.card_id if expense else None

    if expense is None:
        expense = Expense(payer_id=payer_id)
        db.session.add(expense)
    else:
        expense.payer_id = payer_id
        ExpenseShare.query.filter_by(expense_id=expense.id).delete()

    expense.description = desc
    expense.amount = amount
    expense.category = cat
    expense.notes = notes
    expense.spent_at = d
    expense.share_mode = share_mode
    expense.kind = kind
    expense.recurrence_months = recurrence_months
    expense.card_id = new_card_id

    db.session.flush()

    # Shares
    if share_mode == "solo":
        db.session.add(ExpenseShare(expense_id=expense.id, user_id=payer_id,
                                    share_amount=amount, share_percent=Decimal("100")))
    elif share_mode == "integral":
        debtor_id = request.form.get("debtor_id")
        if not debtor_id:
            flash("Selecione o usuário devedor (modo integral).", "danger")
            db.session.rollback()
            return render_template("expenses/form.html", expense=expense,
                                   users=users, user_cards=user_cards)
        db.session.add(ExpenseShare(expense_id=expense.id, user_id=int(debtor_id),
                                    share_amount=amount, share_percent=Decimal("100")))
    elif share_mode == "split":
        total_share = Decimal("0")
        any_share = False
        for u in users:
            v = _parse_decimal(request.form.get(f"share_user_{u.id}"))
            if v and v > 0:
                db.session.add(ExpenseShare(
                    expense_id=expense.id, user_id=u.id, share_amount=v,
                    share_percent=(v / amount * 100).quantize(Decimal("0.01"))))
                total_share += v
                any_share = True
        if not any_share:
            flash("Defina pelo menos um valor de divisão.", "danger")
            db.session.rollback()
            return render_template("expenses/form.html", expense=expense,
                                   users=users, user_cards=user_cards)

    # Sincroniza gastos da casa
    shared_with_id_raw = request.form.get("household_shared_with", "").strip()
    is_household = bool(request.form.get("is_household"))

    existing_hh = HouseholdExpense.query.filter_by(expense_id=expense.id).first()
    if is_household:
        shared_with_id = int(shared_with_id_raw) if shared_with_id_raw.isdigit() else None
        if existing_hh:
            existing_hh.shared_with_id = shared_with_id
        else:
            db.session.add(HouseholdExpense(
                expense_id=expense.id,
                owner_id=current_user.id,
                shared_with_id=shared_with_id
            ))
    else:
        if existing_hh:
            db.session.delete(existing_hh)

    # Sincroniza cartão
    _sync_card_entry(expense, old_card_id)

    db.session.commit()
    flash("Gasto registrado.", "success")
    return redirect(url_for("expenses.list_expenses"))


@expenses_bp.route("/<int:expense_id>/excluir", methods=["POST"])
@login_required
def delete_expense(expense_id):
    e = Expense.query.get_or_404(expense_id)
    if e.payer_id != current_user.id and not current_user.is_admin:
        abort(403)
    # Remove vínculos antes de excluir
    HouseholdExpense.query.filter_by(expense_id=e.id).delete()
    CardEntry.query.filter_by(expense_id=e.id).delete()
    db.session.delete(e)
    db.session.commit()
    flash("Gasto removido.", "info")
    return redirect(url_for("expenses.list_expenses"))
