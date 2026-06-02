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


def get_user_balance_with(user_id, other_user_id):
    from app.models import Expense, ExpenseShare
    a = db.session.query(func.coalesce(func.sum(ExpenseShare.share_amount), 0))\
        .join(Expense, Expense.id == ExpenseShare.expense_id)\
        .filter(Expense.payer_id == user_id,
                ExpenseShare.user_id == other_user_id).scalar() or 0
    b = db.session.query(func.coalesce(func.sum(ExpenseShare.share_amount), 0))\
        .join(Expense, Expense.id == ExpenseShare.expense_id)\
        .filter(Expense.payer_id == other_user_id,
                ExpenseShare.user_id == user_id).scalar() or 0
    return float(a) - float(b)


def get_user_monthly_summary(user_id, year, month):
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
    expenses = db.session.query(Expense, ExpenseShare)\
        .join(ExpenseShare, ExpenseShare.expense_id == Expense.id)\
        .filter(ExpenseShare.user_id == user_id).all()
    debt_total = 0.0
    for exp, share in expenses:
        if exp.is_active_on(year, month):
            debt_total += float(share.share_amount)
    return {
        "income": income_total,
        "expense": debt_total,
        "balance": income_total - debt_total,
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
        # Repasses: gastos pagos pelo usuário que serão devolvidos por outro
        for exp, share in repasses:
            if not exp.is_active_on(year, m):
                continue
            v = round(float(share.share_amount), 2)
            if v <= 0:
                continue
            income_eventual += v
            # Calcula parcela atual se recorrente com prazo definido
            parc_label = ""
            if exp.kind == "recorrente" and exp.recurrence_months:
                months_diff = (year - exp.spent_at.year) * 12 + (m - exp.spent_at.month) + 1
                parc_label = f" ({months_diff}/{exp.recurrence_months})"
            income_eventual_items.append({
                "desc": f"Repasse: {exp.description}{parc_label}",
                "amount": v
            })
        income_total = income_recurring + income_eventual
        fixed_total = 0.0
        eventual_total = 0.0
        eventual_items = []
        fixed_items = []

        # Parcelados do cartão → gasto eventual por mês
        for item in parcelados_por_mes.get((year, m), []):
            eventual_total += item["amount"]
            eventual_items.append(item)

        # Débitos do usuário (repassados por outra pessoa) → gasto eventual
        for exp, share in debitos:
            if not exp.is_active_on(year, m):
                continue
            v = round(float(share.share_amount), 2)
            if v <= 0:
                continue
            parc_label = ""
            if exp.kind == "recorrente" and exp.recurrence_months:
                months_diff = (year - exp.spent_at.year) * 12 + (m - exp.spent_at.month) + 1
                parc_label = f" ({months_diff}/{exp.recurrence_months})"
            eventual_total += v
            eventual_items.append({
                "desc": f"Débito: {exp.description}{parc_label}",
                "amount": v
            })
        for exp, valor in expenses:
            if not exp.is_active_on(year, m):
                continue
            # Exclui gastos eventuais (pontual) anteriores a junho/2026
            if exp.kind == "pontual" and (year < 2026 or (year == 2026 and m < 6)):
                continue
            v = float(valor)
            if exp.kind == "recorrente":
                fixed_total += v
                fixed_items.append({
                    "desc": exp.description,
                    "amount": round(float(v), 2),
                })
            else:
                eventual_total += v
                eventual_items.append({
                    "desc": exp.description,
                    "amount": round(float(v), 2),
                })
        net = income_total - fixed_total - eventual_total
        # Maio/2026: zera saldo e acumulado (mês de referência inicial)
        if year == 2026 and m == 5:
            net = 0.0
            cumulative = 0.0
        else:
            cumulative += net

        # Aplica override manual se existir
        override = overrides.get((year, m))
        net_final = float(override.net_override) if override and override.net_override is not None else net
        cumulative_final = float(override.cumulative_override) if override and override.cumulative_override is not None else cumulative
        if override and override.cumulative_override is not None:
            cumulative = cumulative_final  # propaga para próximo mês

        result.append({
            "month": m,
            "month_name": months_pt[m - 1],
            "income": income_total,
            "income_recurring": income_recurring,
            "income_eventual": income_eventual,
            "fixed_expense": fixed_total,
            "eventual_expense": eventual_total,
            "eventual_items": sorted(eventual_items, key=lambda x: x["amount"], reverse=True),
            "fixed_items": sorted(fixed_items, key=lambda x: x["amount"], reverse=True),
            "income_recurring_items": sorted(income_recurring_items, key=lambda x: x["amount"], reverse=True),
            "income_eventual_items": sorted(income_eventual_items, key=lambda x: x["amount"], reverse=True),
            "total_expense": fixed_total + eventual_total,
            "net": net_final,
            "cumulative": cumulative_final,
        })
    return result
