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

    # Adiciona Janeiro do ano seguinte com acumulado continuado
    jan_next = get_yearly_cashflow(current_user.id, year + 1)
    if jan_next:
        jan = dict(jan_next[0])
        jan["is_next_year"] = True
        if "eventual_items" not in jan:
            jan["eventual_items"] = []
        # Acumulado continua a partir do acumulado de dezembro
        dec_cumulative = months[-1]["cumulative"] if months else 0.0
        jan["cumulative"] = dec_cumulative + jan["net"]
        months = months + [jan]

    # Totais apenas dos 12 meses do ano atual
    months12 = months[:12]
    totals = {
        "income": sum(m["income"] for m in months12),
        "income_recurring": sum(m["income_recurring"] for m in months12),
        "income_eventual": sum(m["income_eventual"] for m in months12),
        "fixed": sum(m["fixed_expense"] for m in months12),
        "eventual": sum(m["eventual_expense"] for m in months12),
        "net": sum(m["net"] for m in months12),
    }
    totals["total_expense"] = totals["fixed"] + totals["eventual"]

    max_value = max(
        max((m["income"] for m in months), default=0),
        max((m["total_expense"] for m in months), default=0),
        1,
    )

    from markupsafe import Markup
    import json
    eventual_data = Markup(json.dumps([m.get("eventual_items", []) for m in months]))
    return render_template("cashflow.html",
                           year=year, months=months, totals=totals,
                           max_value=max_value,
                           eventual_data=eventual_data,
                           current_year=date.today().year)
