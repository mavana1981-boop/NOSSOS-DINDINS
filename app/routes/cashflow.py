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

    # Janeiro do ano seguinte — acumula sobre o saldo de dezembro
    next_jan_list = get_yearly_cashflow(current_user.id, year + 1)[:1]
    # Ajusta o acumulado para continuar de onde dezembro parou
    if next_jan_list and months:
        dec_cumulative = months[-1]["cumulative"]
        nj = dict(next_jan_list[0])
        nj["cumulative"] = dec_cumulative + nj["net"]
        nj["month_name"] = "Jan+" 
        next_jan = [nj]
    else:
        next_jan = []

    totals = {
        "income": sum(m["income"] for m in months),
        "income_recurring": sum(m["income_recurring"] for m in months),
        "income_eventual": sum(m["income_eventual"] for m in months),
        "fixed": sum(m["fixed_expense"] for m in months),
        "eventual": sum(m["eventual_expense"] for m in months),
        "net": sum(m["net"] for m in months),
    }
    totals["total_expense"] = totals["fixed"] + totals["eventual"]

    all_months = months + next_jan
    max_value = max(
        max((m["income"] for m in all_months), default=0),
        max((m["total_expense"] for m in all_months), default=0),
        1,
    )

    return render_template("cashflow.html",
                           year=year, months=months, next_jan=next_jan,
                           totals=totals, max_value=max_value,
                           current_year=date.today().year)
