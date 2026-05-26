from datetime import date
from flask import Blueprint, render_template
from flask_login import login_required, current_user
from sqlalchemy import func, extract
from app import db
from app.models import Income, Expense, ExpenseShare, Project, ProjectMember, User
from app.utils import get_user_monthly_summary, get_credits_debits

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
@login_required
def index():
    today = date.today()
    summary = get_user_monthly_summary(current_user.id, today.year, today.month)
    credits_debits = get_credits_debits(current_user.id)

    # Projetos do usuário (próprios ou compartilhados)
    member_project_ids = [m.project_id for m in
                          ProjectMember.query.filter_by(user_id=current_user.id).all()]
    projects = Project.query.filter(
        (Project.owner_id == current_user.id) | (Project.id.in_(member_project_ids))
    ).order_by(Project.is_completed, Project.created_at.desc()).all()

    # Últimos gastos onde o usuário aparece
    recent_expenses = Expense.query.join(ExpenseShare).filter(
        (Expense.payer_id == current_user.id) | (ExpenseShare.user_id == current_user.id)
    ).distinct().order_by(Expense.spent_at.desc()).limit(6).all()

    # Últimas rendas
    recent_incomes = Income.query.filter_by(user_id=current_user.id)\
        .order_by(Income.received_at.desc()).limit(5).all()

    return render_template(
        "dashboard.html",
        summary=summary,
        credits_debits=credits_debits,
        projects=projects[:4],
        all_projects_count=len(projects),
        recent_expenses=recent_expenses,
        recent_incomes=recent_incomes,
    )
