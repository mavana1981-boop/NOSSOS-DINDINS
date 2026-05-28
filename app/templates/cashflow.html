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

    totals = {
        "income": sum(m["income"] for m in months),
        "income_recurring": sum(m["income_recurring"] for m in months),
        "income_eventual": sum(m["income_eventual"] for m in months),
        "fixed": sum(m["fixed_expense"] for m in months),
        "eventual": sum(m["eventual_expense"] for m in months),
        "net": sum(m["net"] for m in months),
    }
    totals["total_expense"] = totals["fixed"] + totals["eventual"]

    max_value = max(
        max((m["income"] for m in months), default=0),
        max((m["total_expense"] for m in months), default=0),
        1,
    )

    return render_template("cashflow.html",
                           year=year, months=months, totals=totals,
                           max_value=max_value,
                           current_year=date.today().year)
