from datetime import date
from flask import Blueprint, render_template, request, flash, redirect, url_for
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


@cashflow_bp.route("/debug-tudo")
@login_required
def debug_tudo():
    from flask import jsonify
    from app.models import Income, Expense, CardEntry, Card
    from sqlalchemy import text
    with db.engine.connect() as conn:
        users   = conn.execute(text("SELECT id, username FROM users")).fetchall()
        incomes = conn.execute(text("SELECT COUNT(*) FROM incomes")).fetchone()[0]
        expenses= conn.execute(text("SELECT COUNT(*) FROM expenses")).fetchone()[0]
        cards   = conn.execute(text("SELECT COUNT(*) FROM cards")).fetchone()[0]
        entries = conn.execute(text("SELECT COUNT(*) FROM card_entries")).fetchone()[0]
    my_incomes  = Income.query.filter_by(user_id=current_user.id).count()
    my_expenses = Expense.query.filter_by(payer_id=current_user.id).count()
    return jsonify({
        "current_user": {"id": current_user.id, "username": current_user.username},
        "todos_users": [{"id": u[0], "username": u[1]} for u in users],
        "banco_total": {"incomes": incomes, "expenses": expenses, "cards": cards, "entries": entries},
        "meu_user":    {"incomes": my_incomes, "expenses": my_expenses},
    })

@cashflow_bp.route("/debug-entries")
@login_required
def debug_entries():
    from flask import jsonify
    from app.models import Card, CardEntry
    cards = Card.query.filter_by(user_id=current_user.id, is_active=True).all()
    card_ids = [c.id for c in cards]
    entries = CardEntry.query.filter(
        CardEntry.card_id.in_(card_ids),
        (CardEntry.status == "ativo") | (CardEntry.status == None)
    ).order_by(CardEntry.entry_date.desc()).all()
    result = [{"id": e.id, "desc": e.description[:30], "amount": float(e.amount),
               "entry_date": str(e.entry_date), "card_id": e.card_id} for e in entries]
    by_month = {}
    for r in result:
        key = r["entry_date"][:7]
        by_month[key] = by_month.get(key, 0) + 1
    return jsonify({"total": len(result), "por_mes": by_month, "primeiros_10": result[:10]})

@cashflow_bp.route("/limpar-excedentes", methods=["POST"])
@login_required
def limpar_excedentes():
    """Remove excedentes antigos e define billing_month nos entries sem ele."""
    from app.models import Expense, ExpenseShare, Card, CardEntry
    from app.utils import get_billing_month

    # 1. Apaga excedentes existentes do usuário
    todos = Expense.query.filter(
        Expense.payer_id == current_user.id,
        Expense.description.like("% - excedente %"),
        Expense.kind == "pontual"
    ).all()
    count_exc = len(todos)
    for exp in todos:
        ExpenseShare.query.filter_by(expense_id=exp.id).delete()
        db.session.delete(exp)

    # 2. Preencher billing_month nos entries que não têm
    cards = Card.query.filter_by(user_id=current_user.id, is_active=True).all()
    card_closing = {c.id: c.closing_day for c in cards}
    card_ids = [c.id for c in cards]
    entries_sem_bm = CardEntry.query.filter(
        CardEntry.card_id.in_(card_ids),
        CardEntry.status == "ativo",
        CardEntry.billing_month == None,
    ).all() if card_ids else []
    count_bm = 0
    for e in entries_sem_bm:
        closing = card_closing.get(e.card_id)
        yr, mo = get_billing_month(e.entry_date, closing)
        e.billing_month = f"{yr}-{mo:02d}"
        count_bm += 1

    db.session.commit()
    flash(f"✅ {count_exc} excedente(s) removidos. {count_bm} lançamento(s) com fatura corrigida.", "success")
    return redirect(url_for("cashflow.index"))

@cashflow_bp.route("/debug-parcelados")
@login_required
def debug_parcelados():
    from flask import jsonify
    from app.models import CardEntry, Card
    from app.utils import get_billing_month
    import calendar as _cal
    from datetime import date as _d

    cards = Card.query.filter_by(user_id=current_user.id, is_active=True).all()
    card_closing = {c.id: c.closing_day for c in cards}
    card_names = {c.id: c.name for c in cards}
    card_ids = [c.id for c in cards]

    parcelados = CardEntry.query.filter(
        CardEntry.user_id == current_user.id,
        CardEntry.status == "ativo",
        CardEntry.installments > 1,
    ).all()

    def add_m(dt, n):
        month = dt.month - 1 + n
        yr = dt.year + month // 12
        month = month % 12 + 1
        return _d(yr, month, min(dt.day, _cal.monthrange(yr, month)[1]))

    proj = {}
    for e in parcelados:
        first = add_m(e.entry_date, 1 - (e.installment_no or 1))
        for i in range(e.installment_no or 1, e.installments + 1):
            d = add_m(first, i - 1)
            if i == (e.installment_no or 1) and e.billing_month:
                try:
                    bm_yr = int(e.billing_month[:4])
                    bm_mo = int(e.billing_month[5:7])
                    extra = i - (e.installment_no or 1)
                    mo_t = bm_mo - 1 + extra
                    key = f"{bm_yr + mo_t // 12}-{mo_t % 12 + 1:02d}"
                except:
                    key = f"{d.year}-{d.month:02d}"
            else:
                closing = card_closing.get(e.card_id)
                byr, bmo = get_billing_month(d, closing)
                key = f"{byr}-{bmo:02d}"
            if key not in proj:
                proj[key] = 0.0
            proj[key] += float(e.amount)

    return jsonify({
        "total_parcelados_encontrados": len(parcelados),
        "cartoes": [{"id": c.id, "nome": c.name, "closing": c.closing_day} for c in cards],
        "projecao_por_mes": dict(sorted(proj.items())),
        "amostra_entries": [
            {"desc": e.description, "amount": float(e.amount),
             "installment_no": e.installment_no, "installments": e.installments,
             "kind": e.kind, "billing_month": e.billing_month,
             "card": card_names.get(e.card_id)}
            for e in parcelados[:5]
        ]
    })

@cashflow_bp.route("/debug-parc-junho")
@login_required
def debug_parc_junho():
    from app.models import Card, CardEntry
    from app.utils import get_billing_month
    cards = Card.query.filter_by(user_id=current_user.id, is_active=True).all()
    card_ids = [c.id for c in cards]
    card_map = {c.id: c.name for c in cards}
    card_closing = {c.id: c.closing_day for c in cards}

    parcelados = CardEntry.query.filter(
        CardEntry.card_id.in_(card_ids),
        CardEntry.status == "ativo",
        CardEntry.installments > 1,
    ).all() if card_ids else []

    import calendar as _cal
    from datetime import date as _d

    def add_m(dt, n):
        mo = dt.month - 1 + n
        yr = dt.year + mo // 12
        mo = mo % 12 + 1
        return _d(yr, mo, min(dt.day, _cal.monthrange(yr, mo)[1]))

    junho = []
    total = 0.0
    for e in parcelados:
        if e.billing_month:
            try: bm_yr, bm_mo = int(e.billing_month[:4]), int(e.billing_month[5:7])
            except: bm_yr, bm_mo = e.entry_date.year, e.entry_date.month
        else:
            bm_yr, bm_mo = get_billing_month(e.entry_date, card_closing.get(e.card_id))

        for i in range(e.installment_no, e.installments + 1):
            extra = i - e.installment_no
            mo_t = bm_mo - 1 + extra
            key = (bm_yr + mo_t // 12, mo_t % 12 + 1)
            if key == (2026, 6):
                total += float(e.amount)
                junho.append({
                    "id": e.id,
                    "desc": e.description,
                    "amount": float(e.amount),
                    "parcela": f"{i}/{e.installments}",
                    "billing_month": e.billing_month,
                    "entry_date": str(e.entry_date),
                    "cartao": card_map.get(e.card_id, "?"),
                })

    junho.sort(key=lambda x: x["amount"], reverse=True)

    rows = "".join(
        f"<tr><td>{r['id']}</td><td>{r['desc'][:40]}</td><td>{r['cartao']}</td>"
        f"<td>{r['parcela']}</td><td>R$ {r['amount']:.2f}</td>"
        f"<td>{r['billing_month']}</td><td>{r['entry_date']}</td></tr>"
        for r in junho
    )
    return f"""<html><body style='font-family:monospace;padding:20px'>
    <h2>Parcelados junho/2026 — Total: R$ {total:.2f}</h2>
    <p>{len(junho)} parcelas</p>
    <table border=1 cellpadding=4>
    <tr><th>ID</th><th>Descrição</th><th>Cartão</th><th>Parcela</th>
        <th>Valor</th><th>billing_month</th><th>entry_date</th></tr>
    {rows}
    </table></body></html>"""

@cashflow_bp.route("/debug-env")
@login_required
def debug_env():
    from flask import jsonify
    import os
    keys = {k: (v[:6]+"***" if len(v)>6 else "***") for k,v in os.environ.items()
            if any(x in k.upper() for x in ["GEMINI","GROQ","CLOUD","API","KEY","TOKEN"])}
    return jsonify(keys)

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


@cashflow_bp.route("/parcelados")
@login_required
def planejados():
    from app.models import PlannedInstallment
    # Mostrar TODOS os meses com parcelas planejadas (sem limite de ano)
    items = PlannedInstallment.query.filter_by(user_id=current_user.id)        .order_by(PlannedInstallment.billing_month, PlannedInstallment.description)        .all()
    from collections import defaultdict
    por_mes = defaultdict(list)
    for p in items:
        por_mes[p.billing_month].append(p)
    return render_template("cashflow/planejados.html",
                           por_mes=dict(sorted(por_mes.items())))


@cashflow_bp.route("/planejados/<int:planned_id>/delete", methods=["POST"])
@login_required
def delete_planejado(planned_id):
    from app.models import PlannedInstallment
    from app import db
    p = PlannedInstallment.query.get_or_404(planned_id)
    if p.user_id != current_user.id:
        abort(403)
    year = p.billing_month[:4]
    db.session.delete(p)
    db.session.commit()
    flash("Lançamento removido da projeção.", "success")
    return redirect(url_for("cashflow.planejados"))


@cashflow_bp.route("/planejados/delete-bulk", methods=["POST"])
@login_required
def delete_planejados_bulk():
    from app.models import PlannedInstallment
    from app import db
    ids = request.form.getlist("ids")
    year = request.form.get("year", str(date.today().year))
    if not ids:
        flash("Nenhum item selecionado.", "warning")
        return redirect(url_for("cashflow.planejados", year=year))
    count = 0
    for id_str in ids:
        try:
            p = PlannedInstallment.query.get(int(id_str))
            if p and p.user_id == current_user.id:
                db.session.delete(p)
                count += 1
        except Exception:
            continue
    db.session.commit()
    flash(f"{count} item(s) removido(s) da projeção.", "success")
    return redirect(url_for("cashflow.planejados"))


@cashflow_bp.route("/parcelados/comparativo")
@login_required
def comparativo_parcelados():
    from app.models import PlannedInstallment
    m7 = PlannedInstallment.query.filter_by(user_id=current_user.id, billing_month="2026-07")        .order_by(PlannedInstallment.description).all()
    m8 = PlannedInstallment.query.filter_by(user_id=current_user.id, billing_month="2026-08")        .order_by(PlannedInstallment.description).all()

    # Series em julho
    set7 = {(p.description, p.installment_no, p.installments): p for p in m7}
    set8 = {(p.description, p.installment_no, p.installments): p for p in m8}

    # Ultimas em julho que deveriam sumir em agosto
    ultimas_jul = [(k, v) for k, v in set7.items() if k[1] == k[2]]
    # Dessas, quais ainda aparecem em agosto (com installment_no+1)?
    erros = []
    for (desc, ino, itot), p in ultimas_jul:
        # Se é a última (ino==itot), agosto NÃO deveria ter essa série
        # Verifica se há qualquer item em agosto com mesma desc
        agos_items = [v for (d,i,t), v in set8.items() if d == desc]
        if agos_items:
            erros.append({
                "desc": desc,
                "jul": f"{ino}/{itot} (ÚLTIMA)",
                "ago": f"{agos_items[0].installment_no}/{agos_items[0].installments}",
                "card": agos_items[0].card.name if agos_items[0].card else "—",
                "valor": float(agos_items[0].amount),
            })

    return render_template("cashflow/comparativo.html",
                           m7=m7, m8=m8, erros=erros,
                           total7=sum(float(p.amount) for p in m7),
                           total8=sum(float(p.amount) for p in m8))
