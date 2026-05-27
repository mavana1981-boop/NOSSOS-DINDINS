import os
import uuid
from decimal import Decimal
from datetime import datetime
from werkzeug.utils import secure_filename
from PIL import Image
from flask import current_app
from flask_login import current_user
from sqlalchemy import or_, and_, func
from app import db

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def save_profile_photo(file_storage):
    """Salva foto de perfil redimensionada (256x256, quadrada)."""
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_image(file_storage.filename):
        return None

    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    folder = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, filename)

    file_storage.save(path)

    # Redimensionar/recortar quadrado
    try:
        img = Image.open(path)
        img = img.convert("RGB")
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img = img.resize((256, 256), Image.LANCZOS)
        img.save(path, quality=85, optimize=True)
    except Exception as e:
        print(f"Erro processando imagem: {e}")

    return filename


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


# ----- cálculos financeiros -----

def get_user_balance_with(user_id, other_user_id):
    """
    Retorna saldo entre dois usuários (positivo = other_user deve para user_id).
    Olha todas as expense_shares onde um pagou e o outro participa.
    """
    from app.models import Expense, ExpenseShare

    # other deve para user: user pagou, other tem share
    a = db.session.query(func.coalesce(func.sum(ExpenseShare.share_amount), 0))\
        .join(Expense, Expense.id == ExpenseShare.expense_id)\
        .filter(Expense.payer_id == user_id,
                ExpenseShare.user_id == other_user_id).scalar() or 0
    # user deve para other: other pagou, user tem share
    b = db.session.query(func.coalesce(func.sum(ExpenseShare.share_amount), 0))\
        .join(Expense, Expense.id == ExpenseShare.expense_id)\
        .filter(Expense.payer_id == other_user_id,
                ExpenseShare.user_id == user_id).scalar() or 0
    return float(a) - float(b)


def get_user_monthly_summary(user_id, year, month):
    """Resumo mensal do usuário: rendas + gastos (próprios + devidos a outros)."""
    from app.models import Income, Expense, ExpenseShare

    income_total = db.session.query(func.coalesce(func.sum(Income.amount), 0))\
        .filter(Income.user_id == user_id,
                func.extract("year", Income.received_at) == year,
                func.extract("month", Income.received_at) == month).scalar() or 0

    # Gastos que recaem sobre o usuário (share dele em qualquer expense)
    debt_total = db.session.query(func.coalesce(func.sum(ExpenseShare.share_amount), 0))\
        .join(Expense, Expense.id == ExpenseShare.expense_id)\
        .filter(ExpenseShare.user_id == user_id,
                func.extract("year", Expense.spent_at) == year,
                func.extract("month", Expense.spent_at) == month).scalar() or 0

    return {
        "income": float(income_total),
        "expense": float(debt_total),
        "balance": float(income_total) - float(debt_total),
    }


def get_credits_debits(user_id):
    """
    Para cada outro usuário, calcula crédito/débito acumulado.
    Retorna lista [{user, balance}] onde balance>0 significa "tem a receber".
    """
    from app.models import User

    others = User.query.filter(User.id != user_id).all()
    result = []
    for o in others:
        bal = get_user_balance_with(user_id, o.id)
        if abs(bal) > 0.005:
            result.append({"user": o, "balance": bal})
    return result
