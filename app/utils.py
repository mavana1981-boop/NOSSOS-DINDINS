import os
import uuid
import base64
from io import BytesIO
from decimal import Decimal
from datetime import datetime
from PIL import Image
from flask import current_app
from flask_login import current_user
from sqlalchemy import func
from app import db

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def save_profile_photo(file_storage):
    """Converte foto para base64 e retorna data URI para salvar no banco.
    A foto fica persistida no PostgreSQL — nunca se perde no deploy."""
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_image(file_storage.filename):
        return None
    try:
        img = Image.open(file_storage)
        img = img.convert("RGB")
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img = img.resize((256, 256), Image.LANCZOS)
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=80, optimize=True)
        b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        print(f"Erro processando imagem: {e}")
        return None


def format_brl(value):
    if value is None:
        return "R$ 0,00"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "R$ 0,00"
    s = f"{v:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def format_date_br(d):
    if not d:
        return ""
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%d/%m/%Y")


def register_filters(app):
    app.jinja_env.filters["brl"] = format_brl
    app.jinja_env.filters["data_br"] = format_date_br


def register_context(app):
    @app.context_processor
    def inject_globals():
        return {
            "current_year": datetime.now().year,
            "app_name": "Nosso Dindin",
        }


def get_user_balance_with(user_id, other_user_id, year=None, month=None):
    """Saldo com gastos vigentes no mês indicado (padrão: mês atual)."""
    from app.models import Expense, ExpenseShare
    from datetime import date as _d
    if year is None or month is None:
        today = _d.today()
        year, month = today.year, today.month

    def _sum_active(payer_id, share_uid):
        exps = db.session.query(Expense, ExpenseShare)\
            .join(ExpenseShare, ExpenseShare.expense_id == Expense.id)\
            .filter(Expense.payer_id == payer_id,
                    ExpenseShare.user_id == share_uid).all()
        return sum(float(share.share_amount) for exp, share in exps
                   if exp.is_active_on(year, month))

    a = _sum_active(user_id, other_user_id)
    b = _sum_active(other_user_id, user_id)
    return a - b


def get_user_monthly_summary(user_id, year, month):
    """Saldo = Renda - gastos próprios + cotas recebidas."""
    from app.models import Income, Expense, ExpenseShare
    from datetime import date as _date
    incomes = Income.query.filter_by(user_id=user_id).all()
    income_total = 0.0
    last_day = _date(year, month, 28)
    for i in incomes:
        if i.received_at.year == year and i.received_at.month == month:
            income_total += float(i.amount)
        elif i.is_recurring and i.received_at <= last_day:
            if (year, month) >= (i.received_at.year, i.received_at.month):
                income_total += float(i.amount)

    proprios = 0.0  # o que o usuário paga de verdade
    cotas = 0.0     # o que outros lhe devem

    # Gastos onde o usuário é pagador
    for exp in Expense.query.filter_by(payer_id=user_id).all():
        if not exp.is_active_on(year, month):
            continue
        for s in exp.shares:
            if s.user_id == user_id:
                proprios += float(s.share_amount)
            else:
                cotas += float(s.share_amount)

    # Gastos onde o usuário é devedor (outro pagou)
    debitos = db.session.query(Expense, ExpenseShare)\
        .join(ExpenseShare, ExpenseShare.expense_id == Expense.id)\
        .filter(ExpenseShare.user_id == user_id, Expense.payer_id != user_id).all()
    for exp, share in debitos:
        if exp.is_active_on(year, month):
            proprios += float(share.share_amount)

    return {
        "income": income_total,
        "expense": proprios,
        "cotas": cotas,
        "balance": income_total - proprios + cotas,
    }


def get_credits_debits(user_id):
    from app.models import User
    others = User.query.filter(User.id != user_id).all()
    result = []
    for o in others:
        bal = get_user_balance_with(user_id, o.id)
        if abs(bal) > 0.005:
            result.append({"user": o, "balance": bal})
    return result


def get_consolidated_cards(user_id):
    """Retorna o consolidado de cartões do mês atual (entries sem billing_month)."""
    from app.models import Card, CardEntry
    from collections import defaultdict
    cards = Card.query.filter_by(user_id=user_id, is_active=True).all()
    card_ids = [c.id for c in cards]
    # Mês atual: entries com entry_date no mês corrente
    from datetime import date as _d2
    from sqlalchemy import extract as _ext2
    _today = _d2.today()
    all_entries = CardEntry.query.filter(
        CardEntry.card_id.in_(card_ids),
        (CardEntry.status == "ativo") | (CardEntry.status == None),
        _ext2("year",  CardEntry.entry_date) == _today.year,
        _ext2("month", CardEntry.entry_date) == _today.month,
    ).all() if card_ids else []

    consolidated = defaultdict(lambda: {"total": 0.0, "planned": 0.0})
    for entry in all_entries:
        if entry.expense_id and entry.expense:
            key = entry.expense.description
            consolidated[key]["planned"] = float(entry.expense.amount)
        else:
            key = entry.description
        consolidated[key]["total"] += float(entry.amount)

    return consolidated


def get_yearly_cashflow(user_id, year):
    from app import db as _db
    from app.models import Income, Expense, ExpenseShare, CashflowOverride
    # Garante sessão limpa — fecha transação antiga se houver
    try:
        _db.session.commit()
    except Exception:
        _db.session.rollback()
    overrides = {(o.year, o.month): o for o in
                 CashflowOverride.query.filter_by(user_id=user_id).all()}
    from datetime import date as _date
    months_pt = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                 "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
    from decimal import Decimal as _Dec

    # Busca EXATAMENTE igual ao menu Gastos: payer_id == user
    # O valor usado é sempre o amount do Expense (o que o user pagou)
    # Carrega consolidado de cartões UMA VEZ — mesmos valores da tela inicial
    consolidated_cards = get_consolidated_cards(user_id)

    all_expenses = Expense.query.filter(
        Expense.payer_id == user_id
    ).all()

    # Converte para lista (exp, valor) — valor é o que impacta o fluxo do user
    expenses = []
    for exp in all_expenses:
        # Para split: o custo do user é o share_amount dele
        if exp.share_mode in ("split", "integral"):
            share = ExpenseShare.query.filter_by(
                expense_id=exp.id, user_id=user_id
            ).first()
            valor = float(share.share_amount) if share else float(exp.amount)
        else:
            valor = float(exp.amount)
        expenses.append((exp, valor))

    # Lançamentos parcelados no cartão do usuário → gasto eventual por mês
    from app.models import CardEntry
    import calendar as _cal

    def _add_months(dt, n):
        month = dt.month - 1 + n
        year2 = dt.year + month // 12
        month = month % 12 + 1
        day = min(dt.day, _cal.monthrange(year2, month)[1])
        return _date(year2, month, day)

    parcelados = CardEntry.query.filter_by(
        user_id=user_id,
        kind="parcelado",
        status="ativo"
    ).all()

    # Agrupa valor por (ano, mês) para cada parcela futura
    parcelados_por_mes = {}
    for entry in parcelados:
        if not entry.installments or entry.installment_no is None:
            continue
        first_date = _add_months(entry.entry_date, 1 - entry.installment_no)
        for i in range(entry.installment_no, entry.installments + 1):
            d = _add_months(first_date, i - 1)
            key = (d.year, d.month)
            if key not in parcelados_por_mes:
                parcelados_por_mes[key] = []
            parc_label = f" ({i}/{entry.installments})"
            parcelados_por_mes[key].append({
                "desc": f"{entry.description}{parc_label}",
                "amount": round(float(entry.amount), 2)
            })

    # Gastos onde o usuário é o payer E tem repasse (integral/split) de outro usuário
    repasses = db.session.query(Expense, ExpenseShare)        .join(ExpenseShare, ExpenseShare.expense_id == Expense.id)        .filter(
            Expense.payer_id == user_id,
            Expense.share_mode.in_(["integral", "split"]),
            ExpenseShare.user_id != user_id
        ).all()

    # Gastos repassados AO usuário por outra pessoa (o user é devedor)
    # Aparecem como gasto eventual no fluxo do usuário
    debitos = db.session.query(Expense, ExpenseShare)        .join(ExpenseShare, ExpenseShare.expense_id == Expense.id)        .filter(
            ExpenseShare.user_id == user_id,
            Expense.payer_id != user_id,
            Expense.share_mode.in_(["integral", "split"])
        ).all()


    incomes = Income.query.filter_by(user_id=user_id).all()
    result = []
    cumulative = 0.0
    for m in range(1, 13):
        last_day = _date(year, m, 28)
        income_recurring = 0.0
        income_eventual = 0.0
        income_recurring_items = []
        income_eventual_items = []
        for i in incomes:
            if i.is_recurring and i.received_at <= last_day:
                if (year, m) >= (i.received_at.year, i.received_at.month):
                    income_recurring += float(i.amount)
                    income_recurring_items.append({"desc": i.description, "amount": round(float(i.amount), 2)})
            elif i.received_at.year == year and i.received_at.month == m:
                income_eventual += float(i.amount)
                income_eventual_items.append({"desc": i.description, "amount": round(float(i.amount), 2)})
        # Repasses: não somam na renda eventual
        income_total = income_recurring + income_eventual
        fixed_total = 0.0
        eventual_total = 0.0
        eventual_items = []
        fixed_items = []

        # Débitos removidos dos eventuais (repassados não impactam eventual)
        for exp, valor in expenses:
            if not exp.is_active_on(year, m):
                continue
            # Exclui registros de excedente automático (calculados dinamicamente via cartão)
            if "- excedente" in (exp.description or "").lower() and exp.kind == "pontual":
                continue
            # Exclui gastos repassados dos eventuais
            if exp.share_mode in ("integral", "split") and exp.payer_id != user_id:
                continue
            # Exclui gastos eventuais (pontual) anteriores a junho/2026
            if exp.kind == "pontual" and (year < 2026 or (year == 2026 and m < 6)):
                continue
            v = float(valor)
            if exp.kind == "recorrente":
                # Desconta do fixo o valor repassado a outro usuário
                if exp.share_mode in ("integral", "split"):
                    from app.models import ExpenseShare as _ES2
                    repasse_share = _ES2.query.filter(
                        _ES2.expense_id == exp.id,
                        _ES2.user_id != user_id
                    ).all()
                    repasse_v = sum(float(s.share_amount) for s in repasse_share)
                    v = max(0.0, round(v - repasse_v, 2))
                fixed_total += v
                parc_fix = ""
                if exp.recurrence_months:
                    md = (year - exp.spent_at.year) * 12 + (m - exp.spent_at.month) + 1
                    parc_fix = f" ({md}/{exp.recurrence_months})"
                fixed_items.append({
                    "desc": exp.description + parc_fix,
                    "amount": round(float(v), 2),
                })
                # Excedente calculado de forma consolidada após o loop
            else:
                eventual_total += v
                eventual_items.append({
                    "desc": exp.description,
                    "amount": round(float(v), 2),
                })

        # Gastos repassados ao usuário → gastos fixos no fluxo dele
        for exp, share in debitos:
            if not exp.is_active_on(year, m):
                continue
            v2 = round(float(share.share_amount), 2)
            if v2 <= 0:
                continue
            parc_fix2 = ""
            if exp.kind == "recorrente" and exp.recurrence_months:
                md2 = (year - exp.spent_at.year) * 12 + (m - exp.spent_at.month) + 1
                parc_fix2 = f" ({md2}/{exp.recurrence_months})"
            fixed_total += v2
            fixed_items.append({
                "desc": f"{exp.description}{parc_fix2}",
                "amount": v2,
            })
        net = income_total - fixed_total - eventual_total

        # Excedente: lógica diferenciada por mês
        from app.models import CardEntry as _CE, Card as _Card2
        _today = _date.today()

        if year == _today.year and m == _today.month:
            # Mês atual: usa EXATAMENTE os valores da tela inicial de cartões
            for key, grp in consolidated_cards.items():
                if grp["planned"] > 0 and grp["total"] > grp["planned"]:
                    excedente = round(grp["total"] - grp["planned"], 2)
                    eventual_total += excedente
                    eventual_items.append({
                        "desc": f"Excedente: {key}",
                        "amount": excedente,
                    })
        else:
            # Outros meses: só parcelados projetados vs planejado
            _card_ids = [c2.id for c2 in _Card2.query.filter_by(user_id=user_id).all()]
            _parc_entries = _CE.query.filter(
                _CE.card_id.in_(_card_ids),
                _CE.installments > 1,
                (_CE.status == "ativo") | (_CE.status == None)
            ).all() if _card_ids else []

            _groups_parc = {}
            for ce in _parc_entries:
                if not ce.installments or not ce.installment_no:
                    continue
                first = _add_months(ce.entry_date, 1 - ce.installment_no)
                for i in range(ce.installment_no, ce.installments + 1):
                    d = _add_months(first, i - 1)
                    if d.year == year and d.month == m:
                        if ce.expense_id and ce.expense:
                            key = ce.expense.description
                            planned = float(ce.expense.amount)
                        else:
                            key = ce.description
                            planned = 0.0
                        if key not in _groups_parc:
                            _groups_parc[key] = {"total": 0.0, "planned": planned}
                        _groups_parc[key]["total"] += float(ce.amount)
                        break

            for key, grp in _groups_parc.items():
                if grp["planned"] > 0 and grp["total"] > grp["planned"]:
                    excedente = round(grp["total"] - grp["planned"], 2)
                    eventual_total += excedente
                    eventual_items.append({
                        "desc": f"Excedente: {key}",
                        "amount": excedente,
                    })

        # Aplica overrides manuais
        override = overrides.get((year, m))
        def _ov(attr, default):
            v = getattr(override, attr, None) if override else None
            return float(v) if v is not None else default

        income_recurring_f = _ov("income_recurring_override", income_recurring)
        income_eventual_f  = _ov("income_eventual_override",  income_eventual)
        fixed_total_f      = _ov("fixed_override",            fixed_total)
        eventual_total_f   = _ov("eventual_override",         eventual_total)
        net_calc = (income_recurring_f + income_eventual_f) - (fixed_total_f + eventual_total_f)

        # Maio/2026: ponto de referência — zera saldo, acumulado e renda eventual
        if year == 2026 and m == 5:
            income_eventual_f  = _ov("income_eventual_override", 0.0)
            net_final        = _ov("net_override", 0.0)
            cumulative_final = _ov("cumulative_override", 0.0)
            cumulative = cumulative_final
        else:
            net_final        = _ov("net_override", net_calc)
            cumulative_final = _ov("cumulative_override", cumulative + net_final)
            if override and override.cumulative_override is not None:
                cumulative = cumulative_final
            else:
                cumulative += net_final

        result.append({
            "month": m,
            "month_name": months_pt[m - 1],
            "income_recurring": income_recurring_f,
            "income_eventual": income_eventual_f,
            "income": income_recurring_f + income_eventual_f,
            "fixed_expense": fixed_total_f,
            "eventual_expense": eventual_total_f,
            "total_expense": fixed_total_f + eventual_total_f,
            "net": net_final,
            "cumulative": cumulative_final,
            "eventual_items": sorted(eventual_items, key=lambda x: x["amount"], reverse=True),
            "fixed_items": sorted(fixed_items, key=lambda x: x["amount"], reverse=True),
            "income_recurring_items": sorted(income_recurring_items, key=lambda x: x["amount"], reverse=True),
            "income_eventual_items": sorted(income_eventual_items, key=lambda x: x["amount"], reverse=True),
        })
    return result
