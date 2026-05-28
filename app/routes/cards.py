from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from app import db
from app.models import Card, CardEntry, Expense, ExpenseShare

cards_bp = Blueprint("cards", __name__)

COLORS = ["#6b8db5", "#7ea66b", "#c4654a", "#c9a868", "#9b7bb5",
          "#5bb5a8", "#b5756b", "#6b9eb5", "#a8b56b", "#b56b9b"]


def _parse(s):
    if not s:
        return None
    try:
        return Decimal(str(s).replace(".", "").replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None


def _get_user_fixed_expenses():
    return Expense.query.filter(
        Expense.payer_id == current_user.id,
        Expense.kind == "recorrente"
    ).order_by(Expense.description).all()


# ── Cartões ───────────────────────────────────────────────────────────────────

@cards_bp.route("/")
@login_required
def list_cards():
    cards = Card.query.filter_by(user_id=current_user.id, is_active=True)\
        .order_by(Card.name).all()

    # Consolidado: soma lançamentos por nome do gasto, agrupando entre todos os cartões
    from collections import defaultdict
    consolidated = defaultdict(lambda: {"total": 0.0, "cards": {}})
    for card in cards:
        entries = CardEntry.query.filter_by(card_id=card.id).all()
        for entry in entries:
            key = entry.expense.description if (entry.expense_id and entry.expense) else entry.description
            consolidated[key]["total"] += float(entry.amount)
            consolidated[key]["cards"][card.name] = \
                consolidated[key]["cards"].get(card.name, 0.0) + float(entry.amount)

    consolidated_sorted = sorted(
        [{"name": k, "total": v["total"], "cards": v["cards"]}
         for k, v in consolidated.items()],
        key=lambda x: x["total"], reverse=True
    )
    total_geral = sum(x["total"] for x in consolidated_sorted)

    return render_template("cards/list.html", cards=cards,
                           consolidated=consolidated_sorted,
                           total_geral=total_geral)


@cards_bp.route("/novo", methods=["GET", "POST"])
@login_required
def new_card():
    if request.method == "POST":
        return _save_card(None)
    return render_template("cards/form.html", card=None, colors=COLORS)


@cards_bp.route("/<int:card_id>/editar", methods=["GET", "POST"])
@login_required
def edit_card(card_id):
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    if request.method == "POST":
        return _save_card(card)
    return render_template("cards/form.html", card=card, colors=COLORS)


def _save_card(card):
    name = request.form.get("name", "").strip()
    last_digits = request.form.get("last_digits", "").strip()
    limit_amount = _parse(request.form.get("limit_amount"))
    closing_day = request.form.get("closing_day", "").strip()
    due_day = request.form.get("due_day", "").strip()
    color = request.form.get("color", "#6b8db5")

    if not name:
        flash("Nome é obrigatório.", "danger")
        return render_template("cards/form.html", card=card, colors=COLORS)

    is_new = card is None
    if is_new:
        card = Card(user_id=current_user.id)
        db.session.add(card)

    card.name = name
    card.last_digits = last_digits[:4] if last_digits else None
    card.limit_amount = limit_amount or 0
    card.closing_day = int(closing_day) if closing_day.isdigit() else None
    card.due_day = int(due_day) if due_day.isdigit() else None
    card.color = color

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao salvar cartão: {e}", "danger")
        return render_template("cards/form.html", card=card, colors=COLORS)

    flash("Cartão salvo com sucesso.", "success")
    return redirect(url_for("cards.list_cards"))


@cards_bp.route("/<int:card_id>/excluir", methods=["POST"])
@login_required
def delete_card(card_id):
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    card.is_active = False
    db.session.commit()
    flash("Cartão removido.", "info")
    return redirect(url_for("cards.list_cards"))


@cards_bp.route("/<int:card_id>")
@login_required
def detail_card(card_id):
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    entries = CardEntry.query.filter_by(card_id=card_id)\
        .order_by(CardEntry.entry_date.desc()).all()
    fixed_expenses = _get_user_fixed_expenses()

    by_expense = {}
    unlinked = []
    for e in entries:
        if e.expense_id:
            eid = e.expense_id
            if eid not in by_expense:
                by_expense[eid] = {
                    "expense": e.expense,
                    "entries": [],
                    "total": 0
                }
            by_expense[eid]["entries"].append(e)
            by_expense[eid]["total"] += float(e.amount)
        else:
            unlinked.append(e)

    return render_template("cards/detail.html",
                           card=card,
                           entries=entries,
                           by_expense=by_expense,
                           unlinked=unlinked,
                           fixed_expenses=fixed_expenses)


# ── Lançamentos ───────────────────────────────────────────────────────────────

@cards_bp.route("/<int:card_id>/lancamento/novo", methods=["GET", "POST"])
@login_required
def new_entry(card_id):
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    fixed_expenses = _get_user_fixed_expenses()
    if request.method == "POST":
        return _save_entry(None, card)
    return render_template("cards/entry_form.html",
                           card=card, entry=None,
                           fixed_expenses=fixed_expenses)


@cards_bp.route("/<int:card_id>/lancamento/<int:entry_id>/editar", methods=["GET", "POST"])
@login_required
def edit_entry(card_id, entry_id):
    card = Card.query.get_or_404(card_id)
    entry = CardEntry.query.get_or_404(entry_id)
    if card.user_id != current_user.id or entry.card_id != card_id:
        abort(403)
    fixed_expenses = _get_user_fixed_expenses()
    if request.method == "POST":
        return _save_entry(entry, card)
    return render_template("cards/entry_form.html",
                           card=card, entry=entry,
                           fixed_expenses=fixed_expenses)


def _save_entry(entry, card):
    fixed_expenses = _get_user_fixed_expenses()
    desc = request.form.get("description", "").strip()
    amount = _parse(request.form.get("amount"))
    expense_id_raw = request.form.get("expense_id", "").strip()
    expense_id = expense_id_raw if expense_id_raw and expense_id_raw.isdigit() else None
    category = request.form.get("category", "Outros")
    kind = request.form.get("kind", "pontual")
    installments = request.form.get("installments", "1")
    installment_no = request.form.get("installment_no", "1")
    notes = request.form.get("notes", "").strip()
    d_str = request.form.get("entry_date")

    if not desc or not amount or amount <= 0:
        flash("Descrição e valor são obrigatórios.", "danger")
        return render_template("cards/entry_form.html",
                               card=card, entry=entry,
                               fixed_expenses=fixed_expenses)
    try:
        d = datetime.strptime(d_str, "%Y-%m-%d").date() if d_str else date.today()
    except ValueError:
        d = date.today()

    if entry is None:
        entry = CardEntry(card_id=card.id, user_id=current_user.id)
        db.session.add(entry)

    entry.description = desc
    entry.amount = amount
    entry.expense_id = int(expense_id) if expense_id else None
    if entry.expense_id:
        linked_exp = Expense.query.get(entry.expense_id)
        if linked_exp:
            entry.category = linked_exp.description[:60]
    else:
        entry.category = category
    entry.kind = kind if kind in ("pontual", "recorrente", "parcelado") else "pontual"
    if kind == "parcelado":
        entry.installments = max(1, int(installments) if str(installments).isdigit() else 1)
        entry.installment_no = max(1, int(installment_no) if str(installment_no).isdigit() else 1)
    else:
        entry.installments = 1
        entry.installment_no = 1
    entry.notes = notes
    entry.entry_date = d

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao salvar lançamento: {e}", "danger")
        return render_template("cards/entry_form.html",
                               card=card, entry=entry,
                               fixed_expenses=fixed_expenses)

    flash("Lançamento salvo.", "success")
    return redirect(url_for("cards.detail_card", card_id=card.id))


@cards_bp.route("/<int:card_id>/lancamento/<int:entry_id>/excluir", methods=["POST"])
@login_required
def delete_entry(card_id, entry_id):
    entry = CardEntry.query.get_or_404(entry_id)
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    db.session.delete(entry)
    db.session.commit()
    flash("Lançamento removido.", "info")
    return redirect(url_for("cards.detail_card", card_id=card_id))
