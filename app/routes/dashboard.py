from datetime import date
from flask import Blueprint, render_template
from flask_login import login_required, current_user
from sqlalchemy import or_
from app import db
from app.models import Income, Expense, ExpenseShare, Project, ProjectMember, User, HouseholdExpense
from app.utils import get_user_monthly_summary, get_credits_debits

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
@login_required
def index():
    today = date.today()
    summary = get_user_monthly_summary(current_user.id, today.year, today.month)
    credits_debits = get_credits_debits(current_user.id)

    # Projetos do usuário
    member_project_ids = [m.project_id for m in
                          ProjectMember.query.filter_by(user_id=current_user.id).all()]
    projects = Project.query.filter(
        (Project.owner_id == current_user.id) | (Project.id.in_(member_project_ids))
    ).order_by(Project.is_completed, Project.created_at.desc()).all()

    # Últimos gastos
    recent_expenses = Expense.query.join(ExpenseShare).filter(
        (Expense.payer_id == current_user.id) | (ExpenseShare.user_id == current_user.id)
    ).distinct().order_by(Expense.spent_at.desc()).limit(6).all()

    # Últimas rendas
    recent_incomes = Income.query.filter_by(user_id=current_user.id)\
        .order_by(Income.received_at.desc()).limit(5).all()

    # Gastos da Casa — mostra todos os configurados (fixos aparecem todo mês, pontuais sempre visíveis)
    household_links = HouseholdExpense.query.filter(
        or_(
            HouseholdExpense.owner_id == current_user.id,
            HouseholdExpense.shared_with_id == current_user.id
        )
    ).all()

    household_expenses = []
    household_total_planned = 0.0
    household_total_spent = 0.0

    for hh in household_links:
        exp = hh.expense
        if not exp:
            continue
        # Para recorrentes: sempre aparece. Para pontuais: aparece no mês correto
        if exp.kind == 'recorrente':
            visible = exp.is_active_on(today.year, today.month)
        else:
            visible = True  # pontuais sempre visíveis no quadro

        if not visible:
            continue

        household_expenses.append({
            "expense": exp,
            "household": hh,
        })
        household_total_planned += float(exp.amount)
        household_total_spent += float(exp.amount)

    household_pct = min(
        round(household_total_spent / household_total_planned * 100, 1)
        if household_total_planned > 0 else 0, 100
    )

    return render_template(
        "dashboard.html",
        summary=summary,
        credits_debits=credits_debits,
        projects=projects[:4],
        all_projects_count=len(projects),
        recent_expenses=recent_expenses,
        recent_incomes=recent_incomes,
        household_expenses=household_expenses,
        household_total_planned=household_total_planned,
        household_total_spent=household_total_spent,
        household_pct=household_pct,
    )
