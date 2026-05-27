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
    from app.models import Income, Expense, ExpenseShare
    from datetime import date as _date
    months_pt = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                 "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
    expenses = db.session.query(Expense, ExpenseShare)\
        .join(ExpenseShare, ExpenseShare.expense_id == Expense.id)\
        .filter(ExpenseShare.user_id == user_id).all()
    incomes = Income.query.filter_by(user_id=user_id).all()
    result = []
    cumulative = 0.0
    for m in range(1, 13):
        last_day = _date(year, m, 28)
        income_recurring = 0.0
        income_eventual = 0.0
        for i in incomes:
            if i.is_recurring and i.received_at <= last_day:
                if (year, m) >= (i.received_at.year, i.received_at.month):
                    income_recurring += float(i.amount)
            elif i.received_at.year == year and i.received_at.month == m:
                income_eventual += float(i.amount)
        income_total = income_recurring + income_eventual
        fixed_total = 0.0
        eventual_total = 0.0
        for exp, share in expenses:
            if not exp.is_active_on(year, m):
                continue
            v = float(share.share_amount)
            if exp.kind == "recorrente":
                fixed_total += v
            else:
                eventual_total += v
        net = income_total - fixed_total - eventual_total
        cumulative += net
        result.append({
            "month": m,
            "month_name": months_pt[m - 1],
            "income": income_total,
            "income_recurring": income_recurring,
            "income_eventual": income_eventual,
            "fixed_expense": fixed_total,
            "eventual_expense": eventual_total,
            "total_expense": fixed_total + eventual_total,
            "net": net,
            "cumulative": cumulative,
        })
    return result
