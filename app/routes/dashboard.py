from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import or_
from app import db
from app.models import (Income, Expense, ExpenseShare, Project,
                        ProjectMember, User, HouseholdExpense, CardEntry)
from app.utils import get_user_monthly_summary, get_credits_debits, get_yearly_cashflow, get_user_balance_with

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
@login_required
def index():
    today = date.today()
    # Filtro de mês: se ?mes= fornecido (navegação manual), respeitar sempre.
    # Sem parâmetro: usar primeiro mês aberto.
    from app.utils import get_open_billing_month as _gobm_dash
    _mes_param = request.args.get("mes")
    if _mes_param:
        mes_filter = _mes_param   # usuário escolheu — respeitar mesmo fechado
    else:
        mes_filter = _gobm_dash(current_user.id, today.strftime("%Y-%m"))
    try:
        filter_year  = int(mes_filter[:4])
        filter_month = int(mes_filter[5:7])
    except Exception:
        filter_year  = today.year
        filter_month = today.month
    mes_filter = f"{filter_year}-{filter_month:02d}"
    # Prev/next para navegação
    _prev_mo = filter_month - 1 if filter_month > 1 else 12
    _prev_yr = filter_year if filter_month > 1 else filter_year - 1
    _next_mo = filter_month + 1 if filter_month < 12 else 1
    _next_yr = filter_year if filter_month < 12 else filter_year + 1
    prev_mes = f"{_prev_yr}-{_prev_mo:02d}"
    next_mes = f"{_next_yr}-{_next_mo:02d}"
    import calendar as _cal
    mes_nome = _cal.month_name[filter_month]

    summary = get_user_monthly_summary(current_user.id, filter_year, filter_month)

    # Dados do fluxo de caixa para os cards do dashboard
    cf_months = get_yearly_cashflow(current_user.id, filter_year)
    cf_current = next(
        (m for m in cf_months if m["month"] == filter_month),
        cf_months[0] if cf_months else {}
    )
    cf_dec = next((m for m in cf_months if m["month"] == 12), cf_months[-1] if cf_months else {})
    # Usar filter_year/filter_month para calcular saldos entre membros
    from app.utils import get_user_balance_with as _gubw
    from app.models import User as _User
    _others = _User.query.filter(_User.id != current_user.id).all()
    credits_debits = []
    for _o in _others:
        _bal = _gubw(current_user.id, _o.id, filter_year, filter_month)
        if abs(_bal) > 0.005:
            credits_debits.append({"user": _o, "balance": _bal})

    # Projetos do usuário
    member_project_ids = [m.project_id for m in
                          ProjectMember.query.filter_by(user_id=current_user.id).all()]
    projects = Project.query.filter(
        (Project.owner_id == current_user.id) | (Project.id.in_(member_project_ids))
    ).order_by(Project.is_completed, Project.created_at.desc()).all()

    # Últimos gastos
    recent_expenses = []

    # Últimas rendas
    recent_incomes = []

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
        from datetime import date as _d
        def _parc(exp):
            if exp.kind != "recorrente" or not exp.recurrence_months:
                return ""
            md = (filter_year - exp.spent_at.year) * 12 + (filter_month - exp.spent_at.month) + 1
            md = max(1, min(md, exp.recurrence_months))
            return f"{md}/{exp.recurrence_months}"

        for exp, share in eu_paguei:
            if not exp.is_active_on(filter_year, filter_month):
                continue
            entries.append({
                "description": exp.description,
                "date": exp.spent_at,
                "amount": float(share.share_amount),
                "direction": "receber",
                "category": exp.category,
                "kind": exp.kind,
                "recurrence_months": exp.recurrence_months,
                "parcela": _parc(exp),
            })
        for exp, share in outro_pagou:
            if not exp.is_active_on(filter_year, filter_month):
                continue
            entries.append({
                "description": exp.description,
                "date": exp.spent_at,
                "amount": float(share.share_amount),
                "direction": "pagar",
                "category": exp.category,
                "kind": exp.kind,
                "recurrence_months": exp.recurrence_months,
                "parcela": _parc(exp),
            })
        entries.sort(key=lambda x: x["date"], reverse=True)
        credits_debits_detail.append({
            "user": other,
            "balance": get_user_balance_with(current_user.id, cd["user"].id, filter_year, filter_month),
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
    household_links = sorted(
        [hh for hh in household_links
         if (hh.owner_id == current_user.id or hh.shared_with_id == current_user.id)
         and getattr(hh, "show_on_dashboard", True)],
        key=lambda h: h.display_order or 0
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

    household_expenses = []
    household_total_planned = 0.0
    household_total_spent = 0.0

    for hh in household_links:
        exp = hh.expense
        if not exp:
            continue
        if exp.kind == 'recorrente':
            visible = exp.is_active_on(filter_year, filter_month)
        else:
            visible = True
        if not visible:
            continue

        # Apenas entries explicitamente vinculados a este gasto via expense_id
        # Garante que os lançamentos batem exatamente com o que aparece no detalhe do cartão
        entries_card = CardEntry.query.filter(
            CardEntry.expense_id == exp.id,
            CardEntry.billing_month == mes_filter,
            CardEntry.status == "ativo",   # igual ao detalhe do cartão
        ).order_by(CardEntry.entry_date.desc()).all()

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
            "entries": entries_card,   # lançamentos individuais com cartão
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
        cf_current=cf_current,
        cf_dec=cf_dec,
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
        mes_filter=mes_filter,
        filter_year=filter_year,
        filter_month=filter_month,
        mes_nome=mes_nome,
        prev_mes=prev_mes,
        next_mes=next_mes,
        mes_atual=today.strftime("%B/%Y").capitalize(),
    )


@dashboard_bp.route("/relatorio-membros")
@login_required
def relatorio_membros():
    """Relatório HTML de contas entre membros — imprimível como PDF."""
    from app.utils import get_user_balance_with as _gubw, get_open_billing_month as _gobm_rel
    from app.models import User as _User, CardEntry, HouseholdExpense
    from datetime import date as _dt

    today = _dt.today()
    _mes = _gobm_rel(current_user.id, today.strftime("%Y-%m"))
    try:
        filter_year  = int(_mes[:4])
        filter_month = int(_mes[5:7])
    except Exception:
        filter_year, filter_month = today.year, today.month

    MESES = ["Janeiro","Fevereiro","Marco","Abril","Maio","Junho",
             "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
    mes_label = f"{MESES[filter_month-1]}/{filter_year}"

    others = _User.query.filter(_User.id != current_user.id).all()
    membros = []
    for o in others:
        bal = _gubw(current_user.id, o.id, filter_year, filter_month)
        membros.append({"name": o.full_name, "balance": float(bal)})
    membros = [m for m in membros if abs(m["balance"]) > 0.005]

    hh_links = HouseholdExpense.query.filter_by(owner_id=current_user.id).all()
    gastos_casa = []
    for hh in hh_links:
        exp = hh.expense
        if not exp or not exp.is_active_on(filter_year, filter_month):
            continue
        entries = CardEntry.query.filter(
            CardEntry.expense_id == exp.id,
            CardEntry.billing_month == _mes,
            CardEntry.status == "ativo",
        ).all()
        total = sum(float(e.amount) for e in entries)
        gastos_casa.append({
            "desc": exp.description,
            "planned": float(exp.amount),
            "spent": total,
            "diff": total - float(exp.amount),
            "entries": entries,
        })

    return render_template("DASHBOARD/relatorio_membros.html",
                           mes_label=mes_label,
                           mes=_mes,
                           membros=membros,
                           gastos_casa=gastos_casa,
                           hoje=today.strftime("%d/%m/%Y"),
                           user=current_user)




@dashboard_bp.route("/configurar-gastos-casa")
@login_required
def configurar_gastos_casa():
    links = HouseholdExpense.query.filter(
        or_(HouseholdExpense.owner_id == current_user.id,
            HouseholdExpense.shared_with_id == current_user.id)
    ).order_by(HouseholdExpense.display_order).all()
    return render_template("DASHBOARD/configurar_gastos_casa.html", links=links)


@dashboard_bp.route("/configurar-gastos-casa/salvar", methods=["POST"])
@login_required
def salvar_config_gastos_casa():
    # ids ordenados conforme posição no form
    ids_ordenados = request.form.getlist("ordem")
    selecionados   = set(request.form.getlist("hh_ids"))
    for idx, hh_id in enumerate(ids_ordenados):
        hh = HouseholdExpense.query.get(int(hh_id))
        if hh and (hh.owner_id == current_user.id or hh.shared_with_id == current_user.id):
            hh.show_on_dashboard = (hh_id in selecionados)
            hh.display_order     = idx
    db.session.commit()
    flash("Configuração salva.", "success")
    return redirect(url_for("dashboard.index"))
