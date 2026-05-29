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
    entries = CardEntry.query.filter_by(card_id=card_id, status="ativo")\
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


# ── Lançamento em Lote ────────────────────────────────────────────────────────

@cards_bp.route("/<int:card_id>/lote", methods=["GET", "POST"])
@login_required
def batch_upload(card_id):
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    if request.method == "POST":
        return _process_batch(card)
    return render_template("cards/batch_upload.html", card=card)


def _process_batch(card):
    import uuid, base64, json, re
    files = request.files.getlist("files")
    if not files or not files[0].filename:
        flash("Selecione pelo menos um arquivo.", "danger")
        return render_template("cards/batch_upload.html", card=card)

    # Monta conteúdo para a API
    content = []
    for f in files:
        data = f.read()
        mime = f.content_type or "image/jpeg"
        if mime == "application/pdf":
            content.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf",
                           "data": base64.b64encode(data).decode()}
            })
        else:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime,
                           "data": base64.b64encode(data).decode()}
            })

    content.append({
        "type": "text",
        "text": (
            "Analise este extrato de cartão de crédito e extraia TODAS as transações/lançamentos. "
            "Retorne SOMENTE um JSON válido, sem texto adicional, sem markdown, sem explicações. "
            "Formato exato:\n"
            '[{"description": "nome do lançamento", "amount": 99.90, "date": "2024-01-15", '
            '"kind": "pontual"}]\n'
            'Regras: amount sempre número positivo em reais. '
            'date no formato YYYY-MM-DD, se não encontrar use a data de hoje. '
            'kind: "pontual" para compras normais, "recorrente" para assinaturas, '
            '"parcelado" para parcelados. '
            'Se parcelado, adicione "installment_no" e "installments" (ex: 2 e 6 para 2/6). '
            'Ignore taxas, juros, pagamentos e saldo. Extraia apenas compras/débitos.'
        )
    })

    import urllib.request
    import os

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        flash("GEMINI_API_KEY não configurada nas variáveis do Railway.", "danger")
        return render_template("cards/batch_upload.html", card=card)

    # Gemini usa parts com inline_data para imagens/PDFs
    prompt_text = (
        "Analise este extrato de cartão de crédito e extraia TODAS as transações. "
        "Retorne SOMENTE JSON válido, sem markdown, sem texto adicional. "
        'Formato: [{"description": "nome", "amount": 99.90, "date": "2024-01-15", "kind": "pontual"}]\n'
        "Regras: amount positivo em reais. date em YYYY-MM-DD. "
        'kind: "pontual" para compras, "recorrente" para assinaturas, "parcelado" para parcelados. '
        'Se parcelado, inclua "installment_no" e "installments". '
        "Ignore taxas, juros, pagamentos. Extraia apenas compras e débitos."
    )

    parts = [{"text": prompt_text}]
    for item in content:
        if item.get("type") == "image":
            parts.append({"inline_data": {
                "mime_type": item["source"]["media_type"],
                "data": item["source"]["data"]
            }})
        elif item.get("type") == "document":
            parts.append({"inline_data": {
                "mime_type": "application/pdf",
                "data": item["source"]["data"]
            }})

    payload = json.dumps({
        "contents": [{"parts": parts}]
    }).encode()

    url = (f"https://generativelanguage.googleapis.com/v1/"
           f"models/gemini-2.5-flash:generateContent?key={api_key}")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read())
        raw = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        transactions = json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        flash(f"Erro Gemini {e.code}: {body[:400]}", "danger")
        return render_template("cards/batch_upload.html", card=card)
    except Exception as e:
        flash(f"Erro ao processar arquivo: {e}", "danger")
        return render_template("cards/batch_upload.html", card=card)

    if not isinstance(transactions, list) or not transactions:
        flash("Nenhuma transação encontrada no arquivo.", "warning")
        return render_template("cards/batch_upload.html", card=card)

    batch_id = str(uuid.uuid4())[:8]
    count = 0
    for t in transactions:
        try:
            d_str = t.get("date", "")
            try:
                d = datetime.strptime(d_str, "%Y-%m-%d").date()
            except Exception:
                d = date.today()
            amount = Decimal(str(t.get("amount", 0)))
            if amount <= 0:
                continue
            kind = t.get("kind", "pontual")
            entry = CardEntry(
                card_id=card.id,
                user_id=current_user.id,
                description=str(t.get("description", "Sem descrição"))[:160],
                amount=amount,
                entry_date=d,
                kind=kind,
                installments=int(t.get("installments", 1)),
                installment_no=int(t.get("installment_no", 1)),
                category="A classificar",
                status="em_avaliacao",
                batch_id=batch_id,
            )
            db.session.add(entry)
            count += 1
        except Exception:
            continue

    db.session.commit()
    flash(f"{count} lançamento(s) importado(s) para avaliação.", "success")
    return redirect(url_for("cards.batch_review", card_id=card.id, batch_id=batch_id))


@cards_bp.route("/<int:card_id>/lote/<batch_id>/revisao")
@login_required
def batch_review(card_id, batch_id):
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    entries = CardEntry.query.filter_by(
        card_id=card_id, batch_id=batch_id, status="em_avaliacao"
    ).order_by(CardEntry.entry_date).all()
    fixed_expenses = _get_user_fixed_expenses()
    return render_template("cards/batch_review.html",
                           card=card, entries=entries,
                           batch_id=batch_id,
                           fixed_expenses=fixed_expenses)


@cards_bp.route("/<int:card_id>/lote/pendentes")
@login_required
def batch_pending(card_id):
    """Lista todos os lotes pendentes de avaliação."""
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    from sqlalchemy import distinct
    batches = db.session.query(
        CardEntry.batch_id,
        db.func.count(CardEntry.id).label("count"),
        db.func.sum(CardEntry.amount).label("total"),
        db.func.min(CardEntry.entry_date).label("min_date"),
    ).filter_by(card_id=card_id, status="em_avaliacao")\
     .group_by(CardEntry.batch_id).all()
    return render_template("cards/batch_pending.html",
                           card=card, batches=batches)


@cards_bp.route("/<int:card_id>/lote/<batch_id>/aprovar/<int:entry_id>", methods=["POST"])
@login_required
def batch_approve_entry(card_id, batch_id, entry_id):
    card = Card.query.get_or_404(card_id)
    entry = CardEntry.query.get_or_404(entry_id)
    if card.user_id != current_user.id:
        abort(403)
    expense_id_raw = request.form.get("expense_id", "").strip()
    expense_id = int(expense_id_raw) if expense_id_raw.isdigit() else None
    entry.expense_id = expense_id
    if expense_id:
        linked = Expense.query.get(expense_id)
        if linked:
            entry.category = linked.description[:60]
    else:
        entry.category = request.form.get("category", "Outros")
    entry.description = request.form.get("description", entry.description)
    entry.status = "ativo"
    db.session.commit()
    flash("Lançamento aprovado.", "success")
    return redirect(url_for("cards.batch_review",
                            card_id=card_id, batch_id=batch_id))


@cards_bp.route("/<int:card_id>/lote/<batch_id>/aprovar-todos", methods=["POST"])
@login_required
def batch_approve_all(card_id, batch_id):
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    entries = CardEntry.query.filter_by(
        card_id=card_id, batch_id=batch_id, status="em_avaliacao"
    ).all()
    for e in entries:
        e.status = "ativo"
    db.session.commit()
    flash(f"{len(entries)} lançamento(s) aprovado(s).", "success")
    return redirect(url_for("cards.detail_card", card_id=card_id))


@cards_bp.route("/<int:card_id>/lote/<batch_id>/excluir/<int:entry_id>", methods=["POST"])
@login_required
def batch_delete_entry(card_id, batch_id, entry_id):
    entry = CardEntry.query.get_or_404(entry_id)
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    db.session.delete(entry)
    db.session.commit()
    remaining = CardEntry.query.filter_by(
        card_id=card_id, batch_id=batch_id, status="em_avaliacao"
    ).count()
    if remaining == 0:
        flash("Lote concluído.", "info")
        return redirect(url_for("cards.detail_card", card_id=card_id))
    return redirect(url_for("cards.batch_review",
                            card_id=card_id, batch_id=batch_id))
