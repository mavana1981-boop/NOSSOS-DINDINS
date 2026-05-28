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

    # Detalhes de gastos entre membros
    from app.models import Expense, ExpenseShare
    credits_debits_detail = []
    for cd in credits_debits:
        other = cd["user"]
        # Gastos onde eu paguei e o outro tem share
        eu_paguei = db.session.query(Expense, ExpenseShare)            .join(ExpenseShare, ExpenseShare.expense_id == Expense.id)            .filter(Expense.payer_id == current_user.id,
                    ExpenseShare.user_id == other.id).all()
        # Gastos onde o outro pagou e eu tenho share
        outro_pagou = db.session.query(Expense, ExpenseShare)            .join(ExpenseShare, ExpenseShare.expense_id == Expense.id)            .filter(Expense.payer_id == other.id,
                    ExpenseShare.user_id == current_user.id).all()
        entries = []
        for exp, share in eu_paguei:
            entries.append({
                "description": exp.description,
                "date": exp.spent_at,
                "amount": float(share.share_amount),
                "direction": "receber",  # outro me deve
                "category": exp.category,
            })
        for exp, share in outro_pagou:
            entries.append({
                "description": exp.description,
                "date": exp.spent_at,
                "amount": float(share.share_amount),
                "direction": "pagar",  # eu devo ao outro
                "category": exp.category,
            })
        entries.sort(key=lambda x: x["date"], reverse=True)
        credits_debits_detail.append({
            "user": other,
            "balance": cd["balance"],
            "entries": entries,
        })

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

        # Calcula quanto já foi lançado no cartão vinculado para este gasto no mês atual
        from app.models import CardEntry
        spent_this_month = 0.0
        if exp.card_id:
            entries = CardEntry.query.filter_by(
                expense_id=exp.id
            ).all()
            spent_this_month = sum(float(e.amount) for e in entries)
        else:
            # Sem cartão vinculado: considera o valor planejado como gasto
            spent_this_month = float(exp.amount)

        planned = float(exp.amount)
        pct = min(round(spent_this_month / planned * 100, 1) if planned > 0 else 0, 100)

        household_expenses.append({
            "expense": exp,
            "household": hh,
            "spent": spent_this_month,
            "pct": pct,
        })
        household_total_planned += planned
        household_total_spent += spent_this_month

    household_pct = min(
        round(household_total_spent / household_total_planned * 100, 1)
        if household_total_planned > 0 else 0, 100
    )

    return render_template(
        "dashboard.html",
        summary=summary,
        credits_debits=credits_debits_detail,
        projects=projects[:4],
        all_projects_count=len(projects),
        recent_expenses=recent_expenses,
        recent_incomes=recent_incomes,
        household_expenses=household_expenses,
        household_total_planned=household_total_planned,
        household_total_spent=household_total_spent,
        household_pct=household_pct,
    )
