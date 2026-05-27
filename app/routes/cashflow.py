from datetime import date
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from app.utils import get_yearly_cashflow

cashflow_bp = Blueprint("cashflow", __name__)


@cashflow_bp.route("/")
@login_required
def index():
    year = request.args.get("year", type=int) or date.today().year
    months = get_yearly_cashflow(current_user.id, year)

    # Totais do ano
    totals = {
        "income": sum(m["income"] for m in months),
        "fixed": sum(m["fixed_expense"] for m in months),
        "eventual": sum(m["eventual_expense"] for m in months),
        "net": sum(m["net"] for m in months),
    }
    totals["total_expense"] = totals["fixed"] + totals["eventual"]

    # Picos para escala visual
    max_value = max(
        max((m["income"] for m in months), default=0),
        max((m["total_expense"] for m in months), default=0),
        1,
    )

    current_year = date.today().year
    return render_template(
        "cashflow.html",
        year=year,
        months=months,
        totals=totals,
        max_value=max_value,
        current_year=current_year,
    )
