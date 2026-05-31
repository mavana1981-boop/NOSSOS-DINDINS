from datetime import date
from flask import Blueprint, render_template
from flask_login import login_required, current_user
from sqlalchemy import or_
from app import db
from app.models import (Income, Expense, ExpenseShare, Project,
                        ProjectMember, User, HouseholdExpense, CardEntry)
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
    credits_debits_detail = []
    for cd in credits_debits:
        other = cd["user"]
        eu_paguei = db.session.query(Expense, ExpenseShare)\
            .join(ExpenseShare, ExpenseShare.expense_id == Expense.id)\
            .filter(Expense.payer_id == current_user.id,
                    ExpenseShare.user_id == other.id).all()
        outro_pagou = db.session.query(Expense, ExpenseShare)\
            .join(ExpenseShare, ExpenseShare.expense_id == Expense.id)\
            .filter(Expense.payer_id == other.id,
                    ExpenseShare.user_id == current_user.id).all()
        entries = []
        for exp, share in eu_paguei:
            entries.append({
                "description": exp.description,
                "date": exp.spent_at,
                "amount": float(share.share_amount),
                "direction": "receber",
                "category": exp.category,
                "kind": exp.kind,
                "recurrence_months": exp.recurrence_months,
            })
        for exp, share in outro_pagou:
            entries.append({
                "description": exp.description,
                "date": exp.spent_at,
                "amount": float(share.share_amount),
                "direction": "pagar",
                "category": exp.category,
                "kind": exp.kind,
                "recurrence_months": exp.recurrence_months,
            })
        entries.sort(key=lambda x: x["date"], reverse=True)
        credits_debits_detail.append({
            "user": other,
            "balance": cd["balance"],
            "entries": entries,
        })

    # Gastos da Casa
    household_links = HouseholdExpense.query.filter(
        or_(
            HouseholdExpense.owner_id == current_user.id,
            HouseholdExpense.shared_with_id == current_user.id
        )
    ).all()
    # Filtra: se shared_with_id for None, só o owner vê; se preenchido, ambos veem
    household_links = [
        hh for hh in household_links
        if hh.owner_id == current_user.id or (hh.shared_with_id == current_user.id)
    ]

    household_expenses = []
    household_total_planned = 0.0
    household_total_spent = 0.0

    for hh in household_links:
        exp = hh.expense
        if not exp:
            continue
        if exp.kind == 'recorrente':
            visible = exp.is_active_on(today.year, today.month)
        else:
            visible = True
        if not visible:
            continue

        # Soma lançamentos reais no cartão — se não houver cartão vinculado, spent = 0
        entries_card = CardEntry.query.filter_by(expense_id=exp.id).all()
        spent_this_month = sum(float(e.amount) for e in entries_card)

        planned = float(exp.amount)
        pct = min(round(spent_this_month / planned * 100, 1) if planned > 0 else 0, 100)

        # Excedente atual: gasto efetuado vs esperado até agora (planejado * desired_pct)
        esperado_ate_hoje = round(planned * desired_pct / 100, 2)
        excedente_atual = round(spent_this_month - esperado_ate_hoje, 2)

        # Gasto por dia disponível: (planejado - gasto) / dias até dia 16
        dias_ate_fechamento = (cycle_end - today).days
        saldo_disponivel = round(planned - spent_this_month, 2)
        gasto_dia_disponivel = round(saldo_disponivel / dias_ate_fechamento, 2) if dias_ate_fechamento > 0 else 0.0

        household_expenses.append({
            "expense": exp,
            "household": hh,
            "spent": spent_this_month,
            "pct": pct,
            "excedente_atual": excedente_atual,
            "esperado_ate_hoje": esperado_ate_hoje,
            "gasto_dia_disponivel": gasto_dia_disponivel,
            "dias_ate_fechamento": dias_ate_fechamento,
        })
        household_total_planned += planned
        household_total_spent += spent_this_month

    household_pct = min(
        round(household_total_spent / household_total_planned * 100, 1)
        if household_total_planned > 0 else 0, 100
    )

    # Percentual desejável — ciclo do dia 16 ao próximo dia 16
    from datetime import timedelta
    if today.day >= 16:
        cycle_start = today.replace(day=16)
        if today.month == 12:
            cycle_end = today.replace(year=today.year+1, month=1, day=16)
        else:
            cycle_end = today.replace(month=today.month+1, day=16)
    else:
        if today.month == 1:
            cycle_start = today.replace(year=today.year-1, month=12, day=16)
        else:
            cycle_start = today.replace(month=today.month-1, day=16)
        cycle_end = today.replace(day=16)
    total_days = (cycle_end - cycle_start).days
    elapsed_days = (today - cycle_start).days
    desired_pct = min(round(elapsed_days / total_days * 100, 1) if total_days > 0 else 0, 100)

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
        desired_pct=desired_pct,
        today=today,
    )
