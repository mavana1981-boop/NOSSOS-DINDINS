from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, current_app
from flask_login import login_required, current_user
from app import db
from app.models import Card, CardEntry, Expense, ExpenseShare

cards_bp = Blueprint("cards", __name__)

COLORS = ["#6b8db5", "#7ea66b", "#c4654a", "#c9a868", "#9b7bb5",
          "#5bb5a8", "#b5756b", "#6b9eb5", "#a8b56b", "#b56b9b"]


def _parse(s):
    if not s:
        return None
    try:
        return Decimal(str(s).replace(".", "").replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None


def _get_user_fixed_expenses():
    return Expense.query.filter(
        Expense.payer_id == current_user.id,
        Expense.kind == "recorrente"
    ).order_by(Expense.description).all()


# ── Excedente ─────────────────────────────────────────────────────────────────

def _check_excedente(expense_id):
    """Verifica excedente do mês atual para gastos normais.
    Para parcelados, projeta meses futuros. Nunca duplica."""
    from datetime import date as _date
    from app.models import ExpenseShare as _Share
    from decimal import Decimal as _Dec
    from sqlalchemy import extract
    import calendar

    MESES = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho",
             "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]

    def add_months(dt, n):
        month = dt.month - 1 + n
        year = dt.year + month // 12
        month = month % 12 + 1
        day = min(dt.day, calendar.monthrange(year, month)[1])
        return _date(year, month, day)

    def upsert_excedente(payer, desc, amount, cat, year, month):
        """Cria ou atualiza excedente para um mês. Remove se amount <= 0."""
        antigo = Expense.query.filter(
            Expense.payer_id == payer,
            Expense.description == desc,
            Expense.kind == "pontual",
            extract('year', Expense.spent_at) == year,
            extract('month', Expense.spent_at) == month,
        ).first()
        if amount <= 0:
            if antigo:
                _Share.query.filter_by(expense_id=antigo.id).delete()
                db.session.delete(antigo)
                db.session.commit()
            return
        if antigo:
            if round(float(antigo.amount), 2) != round(amount, 2):
                antigo.amount = amount
                db.session.commit()
        else:
            dt = _date(year, month, 1)
            novo = Expense(
                payer_id=payer, description=desc, amount=amount,
                kind="pontual", share_mode="solo", category=cat, spent_at=dt,
            )
            db.session.add(novo)
            db.session.flush()
            db.session.add(_Share(
                expense_id=novo.id, user_id=payer,
                share_amount=_Dec(str(round(amount, 2))),
                share_percent=_Dec("100"),
            ))
            db.session.commit()
            flash(f"Excedente R$ {amount:.2f} registrado: {desc}", "warning")

    exp = Expense.query.get(expense_id)
    if not exp:
        return

    planejado = float(exp.amount)
    payer = exp.payer_id
    today = _date.today()

    parcelados = CardEntry.query.filter_by(
        expense_id=exp.id, kind="parcelado", status="ativo"
    ).all()

    if not parcelados:
        # Gasto normal: agrupa por mês da FATURA (billing_month via closing_day)
        from app.utils import get_billing_month as _gbm2
        from app.models import Card as _Card3
        all_entries_exp = CardEntry.query.filter(
            CardEntry.expense_id == exp.id,
            (CardEntry.status == "ativo") | (CardEntry.status == None)
        ).all()
        month_totals_norm = {}
        for e2 in all_entries_exp:
            card2 = _Card3.query.get(e2.card_id)
            closing2 = card2.closing_day if card2 else None
            bm = _gbm2(e2.entry_date, closing2)
            month_totals_norm[bm] = month_totals_norm.get(bm, 0.0) + float(e2.amount)
        for (yr2, mo2), total in month_totals_norm.items():
            mes_nome = MESES[mo2 - 1]
            desc = f"{exp.description} - excedente {mes_nome}"
            upsert_excedente(payer, desc, round(total - planejado, 2), exp.category, yr2, mo2)
        return

    # Parcelados: projeta meses a partir da parcela atual
    month_totals = {}
    for entry in parcelados:
        if not entry.installments:
            continue
        first_date = add_months(entry.entry_date, 1 - (entry.installment_no or 1))
        for i in range(entry.installment_no or 1, entry.installments + 1):
            d = add_months(first_date, i - 1)
            key = (d.year, d.month)
            month_totals[key] = month_totals.get(key, 0.0) + float(entry.amount)

    # Parcelados: NÃO cria registros no banco — são calculados
    # dinamicamente no utils.py (get_yearly_cashflow) para evitar duplicidade
    pass



@cards_bp.route("/admin/recalcular-excedentes")
@login_required
def recalcular_excedentes():
    """Recalcula todos os excedentes de parcelados - roda manualmente."""
    if not current_user.is_admin:
        abort(403)
    from app.models import ExpenseShare as _Share
    from decimal import Decimal as _Dec
    from datetime import date as _date
    import calendar

    MESES = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho",
             "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]

    def add_months(dt, n):
        month = dt.month - 1 + n
        year = dt.year + month // 12
        month = month % 12 + 1
        return _date(year, month, min(dt.day, calendar.monthrange(year, month)[1]))

    # Apaga todos os excedentes existentes
    todos = Expense.query.filter(
        Expense.description.like("% - excedente %"),
        Expense.kind == "pontual"
    ).all()
    removed = len(todos)
    for exp in todos:
        _Share.query.filter_by(expense_id=exp.id).delete()
        db.session.delete(exp)
    db.session.commit()

    # Recria só para parcelados
    expense_ids = set(
        e.expense_id for e in CardEntry.query.filter(
            CardEntry.expense_id != None,
            CardEntry.kind == "parcelado",
            CardEntry.status == "ativo"
        ).all()
    )
    today = _date.today()
    generated = 0
    for eid in expense_ids:
        exp = Expense.query.get(eid)
        if not exp or "celular denise" in exp.description.lower():
            continue
        planejado = float(exp.amount)
        payer = exp.payer_id
        parcelados = CardEntry.query.filter_by(
            expense_id=eid, kind="parcelado", status="ativo"
        ).all()
        month_totals = {}
        for entry in parcelados:
            if not entry.installments:
                continue
            first_date = add_months(entry.entry_date, 1 - (entry.installment_no or 1))
            for i in range(entry.installment_no or 1, entry.installments + 1):
                d = add_months(first_date, i - 1)
                key = (d.year, d.month)
                month_totals[key] = month_totals.get(key, 0.0) + float(entry.amount)
        for (year, month), total in month_totals.items():
            excedente = round(total - planejado, 2)
            if excedente <= 0:
                continue
            mes_nome = MESES[month - 1]
            desc = f"{exp.description} - excedente {mes_nome}"
            dt = _date(year, month, 1)
            novo = Expense(payer_id=payer, description=desc, amount=excedente,
                kind="pontual", share_mode="solo", category=exp.category, spent_at=dt)
            db.session.add(novo)
            db.session.flush()
            db.session.add(_Share(expense_id=novo.id, user_id=payer,
                share_amount=_Dec(str(excedente)), share_percent=_Dec("100")))
            generated += 1
    db.session.commit()
    return f"<pre>Removidos: {removed}\nGerados: {generated}</pre>"


# ── Cartões ───────────────────────────────────────────────────────────────────

@cards_bp.route("/")
@login_required
def list_cards():
    from datetime import date as _dt
    today = _dt.today()
    from app.utils import get_open_billing_month as _gobm_list
    _mes_param = request.args.get("mes", today.strftime("%Y-%m"))
    mes_filter = _gobm_list(current_user.id, _mes_param)
    try:
        filter_year2  = int(mes_filter[:4])
        filter_month2 = int(mes_filter[5:7])
    except Exception:
        filter_year2, filter_month2 = today.year, today.month
        mes_filter = today.strftime("%Y-%m")

    if filter_month2 == 1:
        prev_mes = f"{filter_year2-1}-12"
    else:
        prev_mes = f"{filter_year2}-{filter_month2-1:02d}"
    if filter_month2 == 12:
        next_mes = f"{filter_year2+1}-01"
    else:
        next_mes = f"{filter_year2}-{filter_month2+1:02d}"

    MESES_PT_C = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho",
                  "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
    mes_label = f"{MESES_PT_C[filter_month2-1]}/{filter_year2}"

    cards = Card.query.filter_by(user_id=current_user.id, is_active=True)\
        .order_by(Card.name).all()

    # Consolidado: soma lançamentos por nome do gasto, agrupando entre todos os cartões
    from collections import defaultdict
    # Consolidado: todos lançamentos do usuário agrupados por gasto vinculado
    card_ids = [card.id for card in cards]
    card_map = {card.id: card.name for card in cards}
    from sqlalchemy import extract as _extract
    import calendar as _cal
    # Filtra por billing_month (mês da fatura), com fallback para entry_date
    _mes_str = f"{filter_year2}-{filter_month2:02d}"
    _all_active = CardEntry.query.filter(
        CardEntry.card_id.in_(card_ids),
        CardEntry.status == "ativo",
    ).all() if card_ids else []
    all_entries = []
    for _e in _all_active:
        if _e.billing_month:
            if _e.billing_month == _mes_str:
                all_entries.append(_e)
        else:
            if _e.entry_date.year == filter_year2 and _e.entry_date.month == filter_month2:
                all_entries.append(_e)


    consolidated = defaultdict(lambda: {"total": 0.0, "planned": 0.0, "cards": {}, "entries": []})
    for entry in all_entries:
        if entry.expense_id and entry.expense:
            key = entry.expense.description
            consolidated[key]["planned"] = float(entry.expense.amount)
        else:
            key = entry.description
        card_name = card_map.get(entry.card_id, "?")
        consolidated[key]["total"] += float(entry.amount)
        consolidated[key]["cards"][card_name] = \
            consolidated[key]["cards"].get(card_name, 0.0) + float(entry.amount)
        parcela_label = ""
        if entry.installments and entry.installments > 1:
            no = entry.installment_no or 1
            parcela_label = f"{no}/{entry.installments}"
        consolidated[key]["entries"].append({
            "desc": entry.description,
            "card": card_name,
            "amount": float(entry.amount),
            "date": entry.entry_date.strftime("%d/%m/%Y") if entry.entry_date else "",
            "parcela": parcela_label,
        })

    consolidated_sorted = sorted(
        [{"name": k, "total": v["total"], "planned": v["planned"],
          "pct": round(v["total"]/v["planned"]*100,1) if v["planned"] > 0 else None,
          "cards": v["cards"],
          "entries": sorted(v["entries"], key=lambda x: x["amount"], reverse=True)}
         for k, v in consolidated.items()],
        key=lambda x: x["total"], reverse=True
    )
    total_geral = sum(x["total"] for x in consolidated_sorted)

    # Consolidado gastos da casa: soma por categoria vinculada a HouseholdExpense
    from app.models import HouseholdExpense
    from collections import defaultdict as _dd
    hh_links = HouseholdExpense.query.filter_by(owner_id=current_user.id).all()
    hh_consolidated = _dd(lambda: {"total": 0.0, "planned": 0.0})
    for hh in hh_links:
        exp = hh.expense
        if not exp:
            continue
        entries = CardEntry.query.filter_by(expense_id=exp.id, status="ativo").all()
        spent = sum(float(e.amount) for e in entries)
        hh_consolidated[exp.description]["total"] += spent
        hh_consolidated[exp.description]["planned"] = float(exp.amount)

    hh_consolidated_sorted = sorted(
        [{"name": k, "total": v["total"], "planned": v["planned"],
          "pct": min(round(v["total"]/v["planned"]*100,1) if v["planned"] > 0 else 0, 999)}
         for k, v in hh_consolidated.items()],
        key=lambda x: x["total"], reverse=True
    )

    # Projeção mês a mês dos parcelados
    import calendar as _cal
    from datetime import date as _date2
    def _add_m(dt, n):
        month = dt.month - 1 + n
        yr = dt.year + month // 12
        month = month % 12 + 1
        return _date2(yr, month, min(dt.day, _cal.monthrange(yr, month)[1]))

    # Projeção parcelados: busca TODOS os parcelados ativos (sem filtro de mês)
    # para projetar parcelas futuras corretamente
    all_parc_entries = CardEntry.query.filter(
        CardEntry.card_id.in_(card_ids),
        CardEntry.installments > 1,
        CardEntry.status == "ativo",
    ).all() if card_ids else []
    parc_entries = all_parc_entries
    MESES_PT = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]
    today2 = _date2.today()
    proj_months = {}
    for ce in parc_entries:
        installment_no = ce.installment_no or 1
        first = _add_m(ce.entry_date, 1 - installment_no)
        for i in range(installment_no, ce.installments + 1):
            d = _add_m(first, i - 1)
            if (d.year, d.month) < (today2.year, today2.month):
                continue
            key = (d.year, d.month)
            proj_months[key] = proj_months.get(key, 0.0) + float(ce.amount)

    # Detalhe por mês: cada entry que impacta o mês
    proj_detail = {}
    for ce in parc_entries:
        installment_no = ce.installment_no or 1
        first = _add_m(ce.entry_date, 1 - installment_no)
        for i in range(installment_no, ce.installments + 1):
            d = _add_m(first, i - 1)
            if (d.year, d.month) < (today2.year, today2.month):
                continue
            key = (d.year, d.month)
            if key not in proj_detail:
                proj_detail[key] = []
            proj_detail[key].append({
                "desc": ce.description,
                "parcela": f"{i}/{ce.installments}",
                "amount": float(ce.amount),
                "card": card_map.get(ce.card_id, "?"),
            })

    import json as _json
    projecao_parcelados = [
        {
            "label": f"{MESES_PT[k[1]-1]}/{k[0]}",
            "total": round(v, 2),
            "key": f"{k[0]}-{k[1]:02d}",
            "items": sorted(proj_detail.get(k, []), key=lambda x: x["amount"], reverse=True),
            "items_json": _json.dumps(sorted(proj_detail.get(k, []), key=lambda x: x["amount"], reverse=True), ensure_ascii=False),
        }
        for k, v in sorted(proj_months.items())
    ]

    # Totais por cartão — só billing_month explícito do mês selecionado
    card_data = {}
    for card in cards:
        entries_card = [e for e in all_entries if e.card_id == card.id]
        card_data[card.id] = {
            "total": sum(float(e.amount) for e in entries_card),
            "count": len(entries_card),
        }


    from app.models import ClosedMonth
    # Meses fechados do usuário
    closed_set = {
        cm.billing_month
        for cm in ClosedMonth.query.filter_by(user_id=current_user.id).all()
    }
    mes_fechado = mes_filter in closed_set

    # Se abrindo sem parâmetro de mês, avançar até primeiro mês não fechado
    if not request.args.get("mes"):
        from datetime import date as _dt2
        _candidate = _dt2.today().strftime("%Y-%m")
        for _ in range(24):
            if _candidate not in closed_set:
                break
            _yr, _mo = int(_candidate[:4]), int(_candidate[5:7])
            _mo += 1
            if _mo > 12: _yr += 1; _mo = 1
            _candidate = f"{_yr}-{_mo:02d}"
        if _candidate != mes_filter:
            from flask import redirect as _redir
            return _redir(url_for("cards.list_cards", mes=_candidate))

    # Histórico de meses fechados (para exibir no menu)
    closed_months_list = ClosedMonth.query.filter_by(
        user_id=current_user.id
    ).order_by(ClosedMonth.billing_month.desc()).all()

    return render_template("cards/list.html", cards=cards,
                           consolidated=consolidated_sorted,
                           total_geral=total_geral,
                           hh_consolidated=hh_consolidated_sorted,
                           projecao_parcelados=projecao_parcelados,
                           card_data=card_data,
                           mes_label=mes_label,
                           mes_filter=mes_filter,
                           prev_mes=prev_mes,
                           next_mes=next_mes,
                           mes_fechado=mes_fechado,
                           closed_months_list=closed_months_list)


@cards_bp.route("/fechar-mes", methods=["POST"])
@login_required
def fechar_mes_geral():
    from app.models import ClosedMonth
    mes = request.form.get("mes", "").strip()
    if not mes:
        flash("Mês não informado.", "danger")
        return redirect(url_for("cards.list_cards"))
    existente = ClosedMonth.query.filter_by(user_id=current_user.id, billing_month=mes).first()
    if not existente:
        db.session.add(ClosedMonth(user_id=current_user.id, billing_month=mes))
        db.session.commit()
        flash(f"Mês {mes} fechado. Avançando para o próximo.", "success")
    return redirect(url_for("cards.list_cards"))


@cards_bp.route("/reabrir-mes", methods=["POST"])
@login_required
def reabrir_mes():
    from app.models import ClosedMonth
    mes = request.form.get("mes", "").strip()
    cm = ClosedMonth.query.filter_by(user_id=current_user.id, billing_month=mes).first()
    if cm:
        db.session.delete(cm)
        db.session.commit()
        flash(f"Mês {mes} reaberto.", "info")
    return redirect(url_for("cards.list_cards", mes=mes))


@cards_bp.route("/virar-mes", methods=["POST"])
@login_required
def virar_mes():
    """
    Virar Mês:
    1. Salva snapshot consolidado no HISTÓRICO
    2. Apaga todos os lançamentos dos cartões
    3. Zera gastos pontuais do mês no menu gastos
    """
    import json as _json
    from datetime import date as _dt
    from app.models import CardMonthHistory, Expense, ExpenseShare

    today = _dt.today()
    mes_atual = today.strftime("%Y-%m")

    cards = Card.query.filter_by(user_id=current_user.id, is_active=True).all()
    card_ids = [c.id for c in cards]

    if not card_ids:
        flash("Nenhum cartão ativo.", "warning")
        return redirect(url_for("cards.list_cards"))

    # 1. Gerar snapshot consolidado
    entries = CardEntry.query.filter(
        CardEntry.card_id.in_(card_ids),
        (CardEntry.status == "ativo") | (CardEntry.status == None)
    ).all()

    from collections import defaultdict
    snap = defaultdict(lambda: {"total": 0.0, "planned": 0.0, "entries": []})
    for e in entries:
        key = e.expense.description if (e.expense_id and e.expense) else e.description
        if e.expense_id and e.expense:
            snap[key]["planned"] = float(e.expense.amount)
        snap[key]["total"] += float(e.amount)
        snap[key]["entries"].append({
            "desc": e.description,
            "amount": float(e.amount),
            "date": str(e.entry_date),
            "parcela": f"{e.installment_no or 1}/{e.installments}" if (e.installments and e.installments > 1) else "",
        })

    total_geral = sum(v["total"] for v in snap.values())
    snapshot = {k: dict(v) for k, v in snap.items()}

    # Upsert histórico
    hist = CardMonthHistory.query.filter_by(
        user_id=current_user.id, billing_month=mes_atual
    ).first()
    if hist:
        hist.snapshot_json = _json.dumps(snapshot, ensure_ascii=False)
        hist.total_geral = total_geral
    else:
        hist = CardMonthHistory(
            user_id=current_user.id,
            billing_month=mes_atual,
            snapshot_json=_json.dumps(snapshot, ensure_ascii=False),
            total_geral=total_geral,
        )
        db.session.add(hist)

    # 2. Apagar todos os lançamentos dos cartões
    count = len(entries)
    for e in entries:
        db.session.delete(e)

    # Nota: gastos do menu Gastos NÃO são apagados — só lançamentos de cartão

    db.session.commit()

    MESES_PT = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho",
                "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
    flash(
        f"✅ {MESES_PT[today.month-1]}/{today.year} arquivado no Histórico — "
        f"{count} lançamento(s) removidos. Cartões zerados para o próximo mês.",
        "success"
    )
    return redirect(url_for("cards.list_cards"))


@cards_bp.route("/reverter-mes", methods=["POST"])
@login_required
def reverter_mes():
    """Restaura o mês mais recente do histórico."""
    import json as _json
    from app.models import CardMonthHistory
    from datetime import datetime as _dt2

    # Pega o histórico mais recente
    hist = CardMonthHistory.query.filter_by(user_id=current_user.id)        .order_by(CardMonthHistory.billing_month.desc()).first()

    if not hist:
        flash("Nenhum histórico encontrado para reverter.", "warning")
        return redirect(url_for("cards.list_cards"))

    snap = _json.loads(hist.snapshot_json)
    cards = Card.query.filter_by(user_id=current_user.id, is_active=True).all()
    card_map = {card.name: card.id for card in cards}
    default_card_id = cards[0].id if cards else None

    if not default_card_id:
        flash("Nenhum cartão ativo para restaurar.", "warning")
        return redirect(url_for("cards.list_cards"))

    count = 0
    skipped = 0

    # Pré-carregar entries existentes E excluídos para não duplicar/restaurar deletados
    _existing_restore = set()
    for _e in CardEntry.query.all():  # inclui excluidos
        _existing_restore.add((
            _e.card_id,
            (_e.description or "")[:60].upper().strip(),
            str(round(float(_e.amount or 0), 2)),
            _e.installment_no or 0,
        ))

    for nome, dados in snap.items():
        for entry_data in dados.get("entries", []):
            try:
                d_str = entry_data.get("date", "")
                try:
                    d = _dt2.strptime(d_str, "%Y-%m-%d").date()
                except Exception:
                    from datetime import date as _dt3
                    d = _dt3.today()

                parcela = entry_data.get("parcela", "")
                inst_no, inst_total = 1, 1
                if "/" in parcela:
                    parts = parcela.split("/")
                    inst_no    = int(parts[0]) if parts[0].isdigit() else 1
                    inst_total = int(parts[1]) if parts[1].isdigit() else 1

                desc = entry_data.get("desc", nome)[:160]
                amount = entry_data.get("amount", 0)

                # Verificar duplicata antes de inserir
                _rk = (
                    default_card_id,
                    desc[:60].upper().strip(),
                    str(round(float(amount), 2)),
                    inst_no,
                )
                if _rk in _existing_restore:
                    skipped += 1
                    continue

                entry = CardEntry(
                    card_id=default_card_id,
                    user_id=current_user.id,
                    description=desc,
                    amount=amount,
                    entry_date=d,
                    kind="parcelado" if inst_total > 1 else "pontual",
                    installments=inst_total,
                    installment_no=inst_no,
                    category="Restaurado",
                    status="ativo",
                )
                db.session.add(entry)
                _existing_restore.add(_rk)
                count += 1
            except Exception:
                continue

    if skipped:
        flash(f"{skipped} lançamento(s) ignorados por já existirem.", "info")

    # Remove o histórico restaurado
    db.session.delete(hist)
    db.session.commit()

    flash(f"✅ Mês {hist.billing_month} restaurado — {count} lançamento(s) recriados.", "success")
    return redirect(url_for("cards.list_cards"))


@cards_bp.route("/novo", methods=["GET", "POST"])
@login_required
def new_card():
    if request.method == "POST":
        return _save_card(None)
    return render_template("cards/form.html", card=None, colors=COLORS)


@cards_bp.route("/<int:card_id>/editar", methods=["GET", "POST"])
@login_required
def edit_card(card_id):
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    if request.method == "POST":
        return _save_card(card)
    return render_template("cards/form.html", card=card, colors=COLORS)


def _save_card(card):
    name = request.form.get("name", "").strip()
    last_digits = request.form.get("last_digits", "").strip()
    limit_amount = _parse(request.form.get("limit_amount"))
    closing_day = request.form.get("closing_day", "").strip()
    due_day = request.form.get("due_day", "").strip()
    color = request.form.get("color", "#6b8db5")

    if not name:
        flash("Nome é obrigatório.", "danger")
        return render_template("cards/form.html", card=card, colors=COLORS)

    is_new = card is None
    if is_new:
        card = Card(user_id=current_user.id)
        db.session.add(card)

    card.name = name
    card.last_digits = last_digits[:4] if last_digits else None
    card.limit_amount = limit_amount or 0
    card.closing_day = int(closing_day) if closing_day.isdigit() else None
    card.due_day = int(due_day) if due_day.isdigit() else None
    card.color = color

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao salvar cartão: {e}", "danger")
        return render_template("cards/form.html", card=card, colors=COLORS)

    flash("Cartão salvo com sucesso.", "success")
    return redirect(url_for("cards.list_cards"))


@cards_bp.route("/<int:card_id>/excluir", methods=["POST"])
@login_required
def delete_card(card_id):
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    card.is_active = False
    db.session.commit()
    flash("Cartão removido.", "info")
    return redirect(url_for("cards.list_cards"))


@cards_bp.route("/<int:card_id>")
@login_required
def detail_card(card_id):
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    # Detalhe filtrado pelo billing_month — usa primeiro mês aberto se não especificado
    from datetime import date as _dt_d
    from app.utils import get_open_billing_month as _gobm_det
    _mes_param_d = request.args.get("mes", _dt_d.today().strftime("%Y-%m"))
    mes_filter_d = _gobm_det(current_user.id, _mes_param_d)
    entries = CardEntry.query.filter(
        CardEntry.card_id == card_id,
        CardEntry.status == "ativo",
        CardEntry.billing_month == mes_filter_d,
    ).order_by(CardEntry.entry_date.desc()).all()
    fixed_expenses = _get_user_fixed_expenses()

    by_expense = {}
    unlinked = []
    for e in entries:
        if e.expense_id:
            eid = e.expense_id
            if eid not in by_expense:
                by_expense[eid] = {
                    "expense": e.expense,
                    "entries": [],
                    "total": 0
                }
            by_expense[eid]["entries"].append(e)
            by_expense[eid]["total"] += float(e.amount)
        else:
            unlinked.append(e)

    MESES_PT = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho",
                "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
    _fy, _fm = int(mes_filter_d[:4]), int(mes_filter_d[5:7])
    if _fm == 1: _prev = f"{_fy-1}-12"
    else:        _prev = f"{_fy}-{_fm-1:02d}"
    if _fm == 12: _next = f"{_fy+1}-01"
    else:         _next = f"{_fy}-{_fm+1:02d}"

    # Histórico de meses fechados para este cartão
    from app.models import CardMonthHistory
    historico_card = CardMonthHistory.query.filter_by(
        user_id=current_user.id, card_id=card_id
    ).order_by(CardMonthHistory.billing_month.desc()).all()

    return render_template("cards/detail.html",
                           card=card,
                           entries=entries,
                           by_expense=by_expense,
                           unlinked=unlinked,
                           fixed_expenses=fixed_expenses,
                           today=date.today(),
                           mes_filter_d=mes_filter_d,
                           mes_label_d=f"{MESES_PT[_fm-1]}/{_fy}",
                           prev_mes_d=_prev,
                           next_mes_d=_next,
                           historico_card=historico_card)


# ── Lançamentos ───────────────────────────────────────────────────────────────

@cards_bp.route("/<int:card_id>/lancamento/novo", methods=["GET", "POST"])
@login_required
def new_entry(card_id):
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    fixed_expenses = _get_user_fixed_expenses()
    mes = request.args.get("mes", date.today().strftime("%Y-%m"))
    if request.method == "POST":
        return _save_entry(None, card)
    return render_template("cards/entry_form.html",
                           card=card, entry=None,
                           fixed_expenses=fixed_expenses,
                           mes_filter=mes)


@cards_bp.route("/<int:card_id>/lancamento/<int:entry_id>/editar", methods=["GET", "POST"])
@login_required
def edit_entry(card_id, entry_id):
    card = Card.query.get_or_404(card_id)
    entry = CardEntry.query.get_or_404(entry_id)
    if card.user_id != current_user.id or entry.card_id != card_id:
        abort(403)
    fixed_expenses = _get_user_fixed_expenses()
    if request.method == "POST":
        return _save_entry(entry, card)
    return render_template("cards/entry_form.html",
                           card=card, entry=entry,
                           fixed_expenses=fixed_expenses)


def _save_entry(entry, card):
    fixed_expenses = _get_user_fixed_expenses()
    desc = request.form.get("description", "").strip()
    amount = _parse(request.form.get("amount"))
    expense_id_raw = request.form.get("expense_id", "").strip()
    expense_id = expense_id_raw if expense_id_raw and expense_id_raw.isdigit() else None
    category = request.form.get("category", "Outros")
    kind = request.form.get("kind", "pontual")
    installments = request.form.get("installments", "1")
    installment_no = request.form.get("installment_no", "1")
    notes = request.form.get("notes", "").strip()
    d_str = request.form.get("entry_date")
    billing_month_form = request.form.get("billing_month", "").strip()

    if not desc or not amount or amount <= 0:
        flash("Descrição e valor são obrigatórios.", "danger")
        return render_template("cards/entry_form.html",
                               card=card, entry=entry,
                               fixed_expenses=fixed_expenses)
    try:
        d = datetime.strptime(d_str, "%Y-%m-%d").date() if d_str else date.today()
    except ValueError:
        d = date.today()

    if entry is None:
        entry = CardEntry(card_id=card.id, user_id=current_user.id)
        db.session.add(entry)

    entry.description = desc
    entry.amount = amount
    entry.expense_id = int(expense_id) if expense_id else None
    if entry.expense_id:
        linked_exp = Expense.query.get(entry.expense_id)
        if linked_exp:
            entry.category = linked_exp.description[:60]
    else:
        entry.category = category
    entry.kind = kind if kind in ("pontual", "recorrente", "parcelado") else "pontual"
    if kind == "parcelado":
        entry.installments = max(1, int(installments) if str(installments).isdigit() else 1)
        entry.installment_no = max(1, int(installment_no) if str(installment_no).isdigit() else 1)
    else:
        entry.installments = 1
        entry.installment_no = 1
    entry.notes = notes
    entry.entry_date = d
    # billing_month: usa o do form se informado, senão calcula pelo closing_day
    if billing_month_form:
        entry.billing_month = billing_month_form
    elif not entry.billing_month:
        from app.utils import get_billing_month as _gbm_save
        byr, bmo = _gbm_save(d, card.closing_day)
        entry.billing_month = f"{byr}-{bmo:02d}"

    # Garantir billing_month em mês aberto — avança automaticamente se fechado
    if entry.billing_month:
        from app.utils import get_open_billing_month as _gobm
        bm_orig = entry.billing_month
        entry.billing_month = _gobm(current_user.id, bm_orig)
        if entry.billing_month != bm_orig:
            flash(
                f"Mês {bm_orig} está fechado. "
                f"Lançamento adicionado em {entry.billing_month}.",
                "warning"
            )

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao salvar lançamento: {e}", "danger")
        return render_template("cards/entry_form.html",
                               card=card, entry=entry,
                               fixed_expenses=fixed_expenses)

    # Verifica excedente ao salvar lançamento normal
    if entry.expense_id:
        _check_excedente(entry.expense_id)

    # Sincronizar planned_installments quando parcelado é salvo/editado
    if entry.installments and entry.installments > 1 and entry.installment_no and entry.billing_month:
        from app.models import PlannedInstallment, PlannedInstallmentDeletion
        try:
            _byr = int(entry.billing_month[:4])
            _bmo = int(entry.billing_month[5:7])
        except Exception:
            _byr, _bmo = None, None
        if _byr:
            # Apagar todos os planned_installments vinculados a este entry (via origin_entry_id)
            # para recriar com dados atualizados
            _old_plans = PlannedInstallment.query.filter_by(
                origin_entry_id=entry.id
            ).all()
            for _op in _old_plans:
                db.session.delete(_op)

            # Também apagar planned com mesma (card_id, description, installment_no)
            # para garantir que não ficam órfãos com descrição antiga
            _old_same = PlannedInstallment.query.filter_by(
                user_id=entry.user_id,
                card_id=entry.card_id,
                installment_no=entry.installment_no,
            ).all()
            for _os in _old_same:
                if _os.description.upper().strip() == entry.description.upper().strip():
                    db.session.delete(_os)

            db.session.flush()

            # Recriar parcela atual
            _del_cur = PlannedInstallmentDeletion.query.filter_by(
                user_id=entry.user_id, card_id=entry.card_id,
                description=entry.description, billing_month=entry.billing_month,
            ).first()
            if not _del_cur:
                db.session.add(PlannedInstallment(
                    user_id=entry.user_id, card_id=entry.card_id,
                    description=entry.description, amount=entry.amount,
                    installment_no=entry.installment_no, installments=entry.installments,
                    billing_month=entry.billing_month, expense_id=entry.expense_id,
                    origin_entry_id=entry.id,
                ))

            # Recriar parcelas futuras
            for _i in range(entry.installment_no + 1, entry.installments + 1):
                _steps = _i - entry.installment_no
                _pmo = _bmo + _steps - 1
                _pyr = _byr + _pmo // 12
                _pmo = (_pmo % 12) + 1
                _proj_bm = f"{_pyr}-{_pmo:02d}"
                _del_fut = PlannedInstallmentDeletion.query.filter_by(
                    user_id=entry.user_id, card_id=entry.card_id,
                    description=entry.description, billing_month=_proj_bm,
                ).first()
                if _del_fut:
                    continue
                db.session.add(PlannedInstallment(
                    user_id=entry.user_id, card_id=entry.card_id,
                    description=entry.description, amount=entry.amount,
                    installment_no=_i, installments=entry.installments,
                    billing_month=_proj_bm, expense_id=entry.expense_id,
                    origin_entry_id=entry.id,
                ))
            db.session.commit()
            flash(f"Projeção de parcelados atualizada para '{entry.description}'.", "info")

    flash("Lançamento salvo.", "success")
    mes_back = entry.billing_month or date.today().strftime("%Y-%m")
    return redirect(url_for("cards.detail_card", card_id=card.id, mes=mes_back))


@cards_bp.route("/<int:card_id>/lancamento/<int:entry_id>/excluir", methods=["POST"])
@login_required
def delete_entry(card_id, entry_id):
    entry = CardEntry.query.get_or_404(entry_id)
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    expense_id = entry.expense_id
    # Soft delete: marca como excluido para não reaparecer em restores
    entry.status = "excluido"
    db.session.commit()
    # Recalcula excedente após exclusão
    if expense_id:
        _check_excedente(expense_id)
    flash("Lançamento removido.", "info")
    # Retorna JSON se chamado via fetch (Ajax), redirect se form normal
    from flask import request as _req
    if _req.headers.get("Accept") == "application/json" or        _req.headers.get("X-Requested-With") == "XMLHttpRequest":
        from flask import jsonify
        return jsonify({"ok": True})
    return redirect(url_for("cards.detail_card", card_id=card_id))


# ── Lançamento em Lote ────────────────────────────────────────────────────────

@cards_bp.route("/<int:card_id>/definir-mes-fatura", methods=["POST"])
@login_required
def definir_mes_fatura(card_id):
    """Redefine o billing_month de todos os entries ativos do cartão."""
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    billing_month = request.form.get("billing_month", "").strip()
    if not billing_month or len(billing_month) != 7:
        flash("Mês da fatura inválido.", "danger")
        return redirect(url_for("cards.detail_card", card_id=card_id))
    entries = CardEntry.query.filter(
        CardEntry.card_id == card_id,
        CardEntry.status == "ativo",
    ).all()
    for e in entries:
        e.billing_month = billing_month
    db.session.commit()
    flash(f"✅ {len(entries)} lançamento(s) atualizados para fatura {billing_month}.", "success")
    return redirect(url_for("cards.detail_card", card_id=card_id))


@cards_bp.route("/<int:card_id>/lote", methods=["GET", "POST"])
@login_required
def batch_upload(card_id):
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    if request.method == "POST":
        return _process_batch(card)
    # Default: mês passado via ?mes= (do detalhe do cartão) ou primeiro mês aberto
    from datetime import date as _dt_bu
    from app.utils import get_open_billing_month as _gobm_bu
    _mes_default = request.args.get("mes", _dt_bu.today().strftime("%Y-%m"))
    _mes_default = _gobm_bu(current_user.id, _mes_default)
    # Verificar se o mês já tem entries (aviso de possível reimportação)
    _ja_tem = CardEntry.query.filter(
        CardEntry.card_id == card_id,
        CardEntry.billing_month == _mes_default,
        CardEntry.status != "excluido",
    ).count()
    return render_template("cards/batch_upload.html", card=card,
                           billing_month_default=_mes_default,
                           ja_tem_entries=_ja_tem)


def _process_batch(card):
    import uuid, base64, json, re, os, urllib.request
    from datetime import date as _dt_now

    files = request.files.getlist("files")
    if not files or not files[0].filename:
        flash("Selecione pelo menos um arquivo.", "danger")
        return render_template("cards/batch_upload.html", card=card)

    # Mês da fatura: definido pelo usuário no formulário (padrão = mês atual)
    _today_bm = _dt_now.today()
    billing_month = request.form.get("billing_month", _today_bm.strftime("%Y-%m"))

    # Avançar automaticamente para mês aberto
    from app.utils import get_open_billing_month as _gobm_batch
    _bm_orig = billing_month
    billing_month = _gobm_batch(current_user.id, billing_month)
    if billing_month != _bm_orig:
        flash(f"Mês {_bm_orig} está fechado. Importando para {billing_month}.", "warning")

    PROMPT = (
        "Analise este extrato de cartão de crédito brasileiro e extraia TODAS as transações de compra. "
        "Retorne SOMENTE JSON válido, sem markdown, sem explicações. "
        'Formato: [{"description": "NOME DA COMPRA", "amount": 99.90, "date": "2026-05-15", "kind": "pontual"}] '
        "REGRAS IMPORTANTES:\n"
        "1. amount: sempre número positivo em reais (converta vírgula para ponto: 1.234,56 -> 1234.56)\n"
        "2. date: formato YYYY-MM-DD. Se só tiver DD/MM, use ano 2026\n"
        "3. kind: 'pontual' para compras normais, 'parcelado' para '01 DE 06' etc, 'recorrente' para assinaturas\n"
        "4. Se parcelado (ex: '03 DE 10'): inclua installment_no=3 e installments=10\n"
        "5. Extraia APENAS linhas marcadas com 'D' (débito). Ignore linhas com 'C' (crédito)\n"
        "6. Ignore: TOTAL DA FATURA, PAGAMENTO, AJUSTE, saldos, limites, encargos, juros, IOF\n"
        "7. Extraia compras de TODOS os cartões do extrato (0410, 6458, 8231, 3221)\n"
        "8. O formato das linhas é: DD/MM DESCRICAO CIDADE VALOR D"
    )

    file_data = []
    for fi in files:
        data = fi.read()
        mime = fi.content_type or "image/jpeg"
        file_data.append({"mime": mime, "b64": base64.b64encode(data).decode()})

    def _extract_pdf_text():
        """Extrai texto de PDFs separando colunas. Retorna string ou vazia."""
        import io
        all_lines = []
        for fd in file_data:
            if "pdf" not in fd["mime"]:
                continue
            try:
                pdf_bytes = base64.b64decode(fd["b64"])
                try:
                    import pdfplumber
                    from collections import defaultdict as _dd_p
                    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                        for page in pdf.pages:
                            mid = page.width / 2
                            words = page.extract_words(x_tolerance=3, y_tolerance=3)
                            if not words:
                                t = page.extract_text()
                                if t: all_lines.extend(t.split("\n"))
                                continue
                            left_l = _dd_p(list); right_l = _dd_p(list)
                            for w in words:
                                y = round(w["top"] / 5) * 5
                                (left_l if w["x0"] < mid else right_l)[y].append(w["text"])
                            for y in sorted(left_l): all_lines.append(" ".join(left_l[y]))
                            for y in sorted(right_l):
                                if right_l[y]: all_lines.append(" ".join(right_l[y]))
                except Exception:
                    # Fallback pypdf
                    try:
                        import pypdf
                        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
                        for page in reader.pages:
                            t = page.extract_text()
                            if t: all_lines.extend(t.split("\n"))
                    except Exception:
                        pass
            except Exception:
                pass
        return "\n".join(all_lines)

    def _parse_json(raw):
        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        raw = re.sub(r"\n?```$", "", raw)
        return json.loads(raw)

    def _try_gemini():
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            return None, "GEMINI_API_KEY não configurada"

        extracted_text = _extract_pdf_text()
        if not extracted_text.strip():
            return None, "Gemini: sem texto extraído do PDF"
        errors = []

        prompt_parcelado = (
            "Analise este extrato de cartão de crédito brasileiro e extraia TODAS as transações. "
            "Retorne SOMENTE JSON válido, sem markdown, sem explicações. "
            'Formato: [{"description":"NOME","amount":99.90,"date":"2026-05-15","kind":"pontual"}] '
            "REGRAS CRÍTICAS:\n"
            "1. amount: número positivo em reais (1.234,56 → 1234.56)\n"
            "2. date: YYYY-MM-DD. Se só DD/MM use ano 2026\n"
            "3. Extraia APENAS linhas com 'D' (débito). Ignore 'C' (crédito)\n"
            "4. Ignore: TOTAL DA FATURA, PAGAMENTO, AJUSTE, IOF, encargos, juros\n"
            "5. PARCELADOS — MUITO IMPORTANTE: quando a linha contiver 'XX DE YY' (ex: '03 DE 10'):\n"
            '   - kind = "parcelado"\n'
            "   - installment_no = XX (número da parcela atual)\n"
            "   - installments = YY (total de parcelas)\n"
            '   Exemplo: "VIA ODONTOLOGIA 03 DE 10 BRASILIA 1000.00D" →\n'
            '   {"description":"VIA ODONTOLOGIA","amount":1000.00,"date":"2026-04-02",'
            '"kind":"parcelado","installment_no":3,"installments":10}\n'
            "6. Extraia compras de TODOS os cartões (0410, 6458, 8231, 3221 etc.)\n"
            "7. Retorne TODOS os lançamentos sem omitir nenhum"
        )

        parts = [
            {"text": prompt_parcelado},
            {"text": "Extrato:\n" + extracted_text[:20000]},
        ]
        payload = json.dumps({"contents": [{"parts": parts}]}).encode()

        # Cache dinâmico: tenta o último modelo que funcionou primeiro
        # Tenta v1 e v1beta para cada modelo
        CANDIDATES = [
            ("v1",    "gemini-2.0-flash-001"),
            ("v1",    "gemini-2.0-flash"),
            ("v1",    "gemini-1.5-flash-8b"),
            ("v1",    "gemini-1.5-flash-001"),
            ("v1",    "gemini-1.5-flash-002"),
            ("v1",    "gemini-1.5-flash"),
            ("v1",    "gemini-1.5-pro-001"),
            ("v1beta","gemini-2.5-flash"),
            ("v1beta","gemini-2.5-flash-preview-05-20"),
            ("v1beta","gemini-2.0-flash-exp"),
            ("v1beta","gemini-2.0-flash-lite"),
            ("v1beta","gemini-1.5-flash-002"),
            ("v1beta","gemini-1.5-flash-001"),
            ("v1beta","gemini-1.5-flash"),
            ("v1beta","gemini-1.5-flash-8b-001"),
            ("v1beta","gemini-1.5-pro-002"),
            ("v1beta","gemini-1.5-pro"),
            ("v1beta","gemini-pro"),
        ]
        # Cache dinâmico: tenta o último que funcionou primeiro
        cached = getattr(current_app, "_gemini_batch_model", None)
        if cached:
            CANDIDATES = [c for c in CANDIDATES if c[1]==cached] +                          [c for c in CANDIDATES if c[1]!=cached]

        for api_ver, model in CANDIDATES:
            try:
                url = (f"https://generativelanguage.googleapis.com/{api_ver}/"
                       f"models/{model}:generateContent?key={key}")
                req = urllib.request.Request(url, data=payload,
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=90) as resp:
                    result = json.loads(resp.read())
                raw = result["candidates"][0]["content"]["parts"][0]["text"]
                parsed = _parse_json(raw)
                try:
                    current_app._gemini_batch_model = model
                    current_app._gemini_last_used = f"{api_ver}/{model}"
                except Exception:
                    pass
                return parsed, None
            except urllib.error.HTTPError as e:
                body = e.read().decode()
                errors.append(f"{api_ver}/{model}:{e.code}")
                if e.code in (429, 503):
                    # Alta demanda ou quota: aguarda e tenta próximo
                    import time as _time; _time.sleep(3)
                continue
            except Exception as _ex:
                errors.append(f"{api_ver}/{model}:{repr(_ex)[:60]}")
                continue
        return None, f"Gemini indisponível. Tentados: {', '.join(errors)}"

    def _try_groq():
        key = (os.environ.get("GROQ_API_KEY") or
               os.environ.get("GROQ_KEY") or
               os.environ.get("groq_api_key") or "")
        if not key:
            env_keys = [k for k in os.environ if "groq" in k.lower() or "GROQ" in k]
            return None, f"GROQ_API_KEY não encontrada (vars disponíveis: {env_keys})"

        def _groq_call(text_chunk):
            """Chama Groq com um chunk de texto."""
            msgs = [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "text", "text": "Extrato:\n" + text_chunk},
            ]}]
            payload = json.dumps({
                "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                "messages": msgs,
                "max_tokens": 8192,
            }).encode()
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                    "User-Agent": "Mozilla/5.0 (compatible; Python/3.13)",
                    "Accept": "application/json",
                },
                method="POST")
            with urllib.request.urlopen(req, timeout=90) as resp:
                result = json.loads(resp.read())
            raw = result["choices"][0]["message"]["content"]
            return _parse_json(raw)

        # Usa _extract_pdf_text compartilhada
        all_text = _extract_pdf_text()
        has_image = any("image" in fd["mime"] for fd in file_data)

        if not all_text.strip() and not has_image:
            return None, "Groq: sem conteúdo para processar"

        all_transactions = []
        CHUNK = 12000
        OVERLAP = 500

        if all_text.strip():
            chunks = []
            start = 0
            while start < len(all_text):
                end = min(start + CHUNK, len(all_text))
                chunks.append(all_text[start:end])
                if end >= len(all_text):
                    break
                start = end - OVERLAP

            for i, chunk in enumerate(chunks):
                try:
                    result = _groq_call(chunk)
                    if isinstance(result, list):
                        all_transactions.extend(result)
                except urllib.error.HTTPError as e:
                    return None, f"Groq {e.code}: {e.read().decode()[:200]}"
                except Exception as e:
                    return None, f"Groq chunk {i+1}: {e}"
        else:
            try:
                img_msgs = [{"role": "user", "content": [{"type": "text", "text": PROMPT}]}]
                for fd in file_data:
                    if "image" in fd["mime"]:
                        img_msgs[0]["content"].append({"type": "image_url",
                            "image_url": {"url": f"data:{fd['mime']};base64,{fd['b64']}"}})
                payload = json.dumps({
                    "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                    "messages": img_msgs, "max_tokens": 8192,
                }).encode()
                req = urllib.request.Request(
                    "https://api.groq.com/openai/v1/chat/completions",
                    data=payload,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {key}",
                             "User-Agent": "Mozilla/5.0"},
                    method="POST")
                with urllib.request.urlopen(req, timeout=90) as resp:
                    result = json.loads(resp.read())
                raw = result["choices"][0]["message"]["content"]
                all_transactions = _parse_json(raw)
            except urllib.error.HTTPError as e:
                return None, f"Groq {e.code}: {e.read().decode()[:200]}"
            except Exception as e:
                return None, f"Groq: {e}"

        seen = set()
        unique = []
        for t in all_transactions:
            key2 = (str(t.get("description",""))[:40], str(t.get("amount","")), str(t.get("date","")))
            if key2 not in seen:
                seen.add(key2)
                unique.append(t)

        return unique if unique else None, None if unique else "Groq: nenhuma transação encontrada"

    def _try_cloudflare():
        acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        key  = os.environ.get("CLOUDFLARE_API_TOKEN", "")
        if not acct or not key:
            return None, "CLOUDFLARE_ACCOUNT_ID ou CLOUDFLARE_API_TOKEN não configurados"

        def _cf_call(model, messages):
            payload = json.dumps({"messages": messages}).encode()
            url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
            req = urllib.request.Request(url, data=payload,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {key}"},
                method="POST")
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read())

        # Extrai texto do PDF para usar modelo de texto (sem precisar de agreement)
        pdf_text = ""
        has_img = False
        for fd in file_data:
            if "pdf" in fd["mime"]:
                try:
                    import io, pdfplumber
                    from collections import defaultdict as _dd3
                    pdf_bytes = base64.b64decode(fd["b64"])
                    lines_cf = []
                    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                        for page in pdf.pages:
                            mid = page.width / 2
                            words = page.extract_words(x_tolerance=3, y_tolerance=3)
                            if not words:
                                t = page.extract_text()
                                if t: lines_cf.extend(t.split("\n"))
                                continue
                            left_l = _dd3(list); right_l = _dd3(list)
                            for w in words:
                                y = round(w["top"] / 5) * 5
                                (left_l if w["x0"] < mid else right_l)[y].append(w["text"])
                            for y in sorted(left_l): lines_cf.append(" ".join(left_l[y]))
                            for y in sorted(right_l):
                                if right_l[y]: lines_cf.append(" ".join(right_l[y]))
                    pdf_text = "\n".join(lines_cf)
                except Exception:
                    pass
            elif "image" in fd["mime"]:
                has_img = True

        try:
            if pdf_text.strip():
                # PDF: usa modelo de texto (sem agreement)
                msgs = [
                    {"role": "user", "content": PROMPT + "\n\nExtrato:\n" + pdf_text[:12000]}
                ]
                result = _cf_call("@cf/meta/llama-3.1-8b-instruct", msgs)
                raw = result.get("result", {}).get("response", "")
            elif has_img:
                # Imagem: aceita agreement do modelo de visão, depois envia
                agree_msgs = [{"role": "user", "content": "agree"}]
                try:
                    _cf_call("@cf/meta/llama-3.2-11b-vision-instruct", agree_msgs)
                except Exception:
                    pass
                img_content = [{"type": "text", "text": PROMPT}]
                for fd in file_data:
                    if "image" in fd["mime"]:
                        img_content.append({"type": "image_url",
                            "image_url": {"url": f"data:{fd['mime']};base64,{fd['b64']}"}})
                msgs = [{"role": "user", "content": img_content}]
                result = _cf_call("@cf/meta/llama-3.2-11b-vision-instruct", msgs)
                raw = result.get("result", {}).get("response", "")
            else:
                return None, "Cloudflare: sem conteúdo para processar"
            return _parse_json(raw), None
        except urllib.error.HTTPError as e:
            return None, f"Cloudflare {e.code}: {e.read().decode()[:200]}"
        except Exception as e:
            return None, f"Cloudflare: {e}"

    transactions = None
    errors = []
    # Chain: Gemini → Groq → Cloudflare
    ia_usada = None
    for _fn, _name in [(_try_gemini, "Gemini"), (_try_groq, "Groq"), (_try_cloudflare, "Cloudflare")]:
        _result, _err = _fn()
        if _result is not None:
            ia_usada = _name
            transactions = _result
            break
        errors.append(f"{_name}: {_err or 'falhou'}")

    if transactions is None:
        flash("Falha ao analisar extrato. " + " | ".join(errors), "danger")
        return render_template("cards/batch_upload.html", card=card)

    # Aviso de qual IA processou
    if ia_usada == "Gemini":
        modelo = getattr(current_app, "_gemini_last_used", "Gemini")
        flash(f"✅ Extrato processado via {modelo}", "success")
    elif ia_usada:
        flash(f"✅ Extrato processado via {ia_usada}", "success")

    if not isinstance(transactions, list) or not transactions:
        flash("Nenhuma transação encontrada no arquivo.", "warning")
        return render_template("cards/batch_upload.html", card=card)

    batch_id = str(uuid.uuid4())[:8]
    count = 0
    skipped = 0

    import re as _re_b
    def _norm_b(s):
        s = (s or "").upper().strip()
        s = _re_b.sub(r"[ ]+[0-9]{1,2}[ ]+DE[ ]+[0-9]{1,2}", "", s)
        s = _re_b.sub(r"[ ]+[0-9]{1,2}/[0-9]{1,2}", "", s)
        s = _re_b.sub(r"[ ]+[0-9]{1,2}[ ]+[0-9]{1,2}(?=[ ]|$)", "", s)
        return s[:30].strip()

    # Dedup para parcelados: (desc_norm, installment_no) único por cartão
    _parc_existentes = CardEntry.query.filter(
        CardEntry.card_id == card.id,
        CardEntry.installments > 1,
        CardEntry.status != "excluido",
    ).all()
    _parc_set = set(
        (_norm_b(e.description), e.installment_no or 0)
        for e in _parc_existentes
    )

    # Dedup para pontuais: (desc[:40], amount, entry_date, billing_month) no mesmo mês
    _pont_existentes = CardEntry.query.filter(
        CardEntry.card_id == card.id,
        CardEntry.billing_month == billing_month,
        CardEntry.installments <= 1,
        CardEntry.status != "excluido",
    ).all()
    _pont_set = set(
        (e.description[:40].upper().strip(), str(round(float(e.amount), 2)),
         str(e.entry_date))
        for e in _pont_existentes
    )

    for t in transactions:
        try:
            d_str = t.get("date", "")
            try:
                d = datetime.strptime(d_str, "%Y-%m-%d").date()
            except Exception:
                d = date.today()
            amount = Decimal(str(t.get("amount", 0)))
            if amount <= 0:
                continue
            kind = t.get("kind", "pontual")
            inst    = int(t.get("installments", 1))
            inst_no = int(t.get("installment_no", 1))
            if inst > 1:
                kind = "parcelado"
            desc = str(t.get("description", "Sem descrição"))[:160]

            # Parcelado: verificar se esta parcela já existe em qualquer mês
            if inst > 1:
                _ck = (_norm_b(desc), inst_no)
                if _ck in _parc_set:
                    skipped += 1
                    continue

            # Pontual: verificar se mesmo lançamento já existe no billing_month
            if inst <= 1:
                _pk = (desc[:40].upper().strip(),
                       str(round(float(amount), 2)),
                       str(d))
                if _pk in _pont_set:
                    skipped += 1
                    continue

            entry = CardEntry(
                card_id=card.id,
                user_id=current_user.id,
                description=desc,
                amount=amount,
                entry_date=d,
                kind=kind,
                installments=inst,
                installment_no=inst_no,
                category="A classificar",
                status="em_avaliacao",
                batch_id=batch_id,
                billing_month=billing_month,
            )
            db.session.add(entry)
            if inst > 1:
                _parc_set.add((_norm_b(desc), inst_no))  # evitar dup dentro do lote
            count += 1
        except Exception:
            continue

    if skipped:
        flash(f"⚠️ {skipped} parcela(s) ignorada(s) por já existirem no cartão.", "warning")

    # Gerar planned_installments: parcela atual + futuras
    from app.models import MerchantRule, PlannedInstallment
    _batch_entries = CardEntry.query.filter_by(batch_id=batch_id).all()
    for _e in _batch_entries:
        if not _e.installments or _e.installments <= 1 or not _e.installment_no:
            continue
        if not _e.billing_month:
            continue
        try:
            _byr = int(_e.billing_month[:4])
            _bmo = int(_e.billing_month[5:7])
        except Exception:
            continue
        # Adicionar a parcela atual ao planned_installments
        # Pular se foi excluído intencionalmente pelo usuário
        from app.models import PlannedInstallmentDeletion as _PID2
        # Checar se esta série foi excluída para este billing_month
        _was_deleted = _PID2.query.filter_by(
            user_id=current_user.id, card_id=card.id,
            description=_e.description, billing_month=_e.billing_month,
        ).first()
        _exists_cur = PlannedInstallment.query.filter_by(
            user_id=current_user.id, card_id=card.id,
            description=_e.description, installment_no=_e.installment_no,
        ).first()
        if not _exists_cur and not _was_deleted:
            db.session.add(PlannedInstallment(
                user_id=current_user.id, card_id=card.id,
                description=_e.description, amount=_e.amount,
                installment_no=_e.installment_no, installments=_e.installments,
                billing_month=_e.billing_month,
                expense_id=_e.expense_id, origin_entry_id=_e.id,
            ))
        for _i in range(_e.installment_no + 1, _e.installments + 1):
            # Mês projetado: billing_month da parcela importada + N meses
            _steps = _i - _e.installment_no
            _pmo = _bmo + _steps - 1
            _pyr = _byr + _pmo // 12
            _pmo = (_pmo % 12) + 1
            _proj_bm = f"{_pyr}-{_pmo:02d}"
            # Pular se foi excluído intencionalmente ou já existe
            # Checar se esta série foi excluída para o billing_month projetado
            _was_del_fut = _PID2.query.filter_by(
                user_id=current_user.id, card_id=card.id,
                description=_e.description, billing_month=_proj_bm,
            ).first()
            if _was_del_fut:
                continue
            _exists_plan = PlannedInstallment.query.filter_by(
                user_id=current_user.id,
                card_id=card.id,
                description=_e.description,
                installment_no=_i,
            ).first()
            if _exists_plan:
                continue
            _pi = PlannedInstallment(
                user_id=current_user.id,
                card_id=card.id,
                description=_e.description,
                amount=_e.amount,
                installment_no=_i,
                installments=_e.installments,
                billing_month=_proj_bm,
                expense_id=_e.expense_id,
                origin_entry_id=_e.id,
            )
            db.session.add(_pi)
    db.session.commit()

    # Aplicar regras de categorização automática
    rules = MerchantRule.query.filter_by(user_id=current_user.id).all()
    if rules:
        pending = CardEntry.query.filter_by(batch_id=batch_id, status="em_avaliacao").all()
        for e in pending:
            desc_lower = e.description.lower()
            for rule in rules:
                if rule.keyword.lower() in desc_lower:
                    e.category  = rule.category
                    if rule.expense_id:
                        e.expense_id = rule.expense_id
                    break
    db.session.commit()
    flash(f"{count} lançamento(s) importado(s) para avaliação.", "success")
    return redirect(url_for("cards.batch_review", card_id=card.id, batch_id=batch_id))


@cards_bp.route("/<int:card_id>/lote/<batch_id>/revisao")
@login_required
def batch_review(card_id, batch_id):
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    entries = CardEntry.query.filter_by(
        card_id=card_id, batch_id=batch_id, status="em_avaliacao"
    ).order_by(CardEntry.amount.desc()).all()
    fixed_expenses = _get_user_fixed_expenses()
    return render_template("cards/batch_review.html",
                           card=card, entries=entries,
                           batch_id=batch_id,
                           fixed_expenses=fixed_expenses)


@cards_bp.route("/<int:card_id>/lote/pendentes")
@login_required
def batch_pending(card_id):
    """Lista todos os lotes pendentes de avaliação."""
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    try:
        batches = db.session.query(
            CardEntry.batch_id,
            db.func.count(CardEntry.id).label("count"),
            db.func.sum(CardEntry.amount).label("total"),
            db.func.min(CardEntry.entry_date).label("min_date"),
        ).filter(
            CardEntry.card_id == card_id,
            CardEntry.status == "em_avaliacao"
        ).group_by(CardEntry.batch_id).all()
    except Exception as e:
        from flask import abort
        return f"<pre>ERRO batch_pending: {e}</pre>", 500
    return render_template("cards/batch_pending.html",
                           card=card, batches=batches)


@cards_bp.route("/<int:card_id>/lote/<batch_id>/aprovar/<int:entry_id>", methods=["POST"])
@login_required
def batch_approve_entry(card_id, batch_id, entry_id):
    card = Card.query.get_or_404(card_id)
    entry = CardEntry.query.get_or_404(entry_id)
    if card.user_id != current_user.id:
        abort(403)
    expense_id_raw = request.form.get("expense_id", "").strip()
    expense_id = int(expense_id_raw) if expense_id_raw.isdigit() else None
    entry.expense_id = expense_id
    if expense_id:
        linked = Expense.query.get(expense_id)
        if linked:
            entry.category = linked.description[:60]
    else:
        entry.category = request.form.get("category", "Outros")
    entry.description = request.form.get("description", entry.description)
    entry.status = "ativo"
    db.session.commit()

    # Salvar regra de categorização para uso futuro
    if entry.category and entry.category != "A classificar":
        from app.models import MerchantRule
        keyword = entry.description.split()[0] if entry.description else ""
        if len(keyword) >= 3:
            existing = MerchantRule.query.filter_by(
                user_id=current_user.id, keyword=keyword
            ).first()
            if existing:
                existing.category   = entry.category
                existing.expense_id = entry.expense_id
            else:
                rule = MerchantRule(
                    user_id=current_user.id,
                    keyword=keyword,
                    category=entry.category,
                    expense_id=entry.expense_id,
                )
                db.session.add(rule)
            db.session.commit()

    if entry.expense_id:
        _check_excedente(entry.expense_id)

    flash("Lançamento aprovado.", "success")
    return redirect(url_for("cards.batch_review",
                            card_id=card_id, batch_id=batch_id))


@cards_bp.route("/<int:card_id>/lote/<batch_id>/rejeitar-todos", methods=["POST"])
@login_required
def batch_reject_all(card_id, batch_id):
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    entries = CardEntry.query.filter_by(
        card_id=card_id, batch_id=batch_id, status="em_avaliacao"
    ).all()
    count = len(entries)
    for e in entries:
        db.session.delete(e)
    db.session.commit()
    flash(f"{count} lançamento(s) rejeitado(s) e removidos.", "info")
    return redirect(url_for("cards.detail_card", card_id=card_id))


@cards_bp.route("/<int:card_id>/lote/<batch_id>/aprovar-todos", methods=["POST"])
@login_required
def batch_approve_all(card_id, batch_id):
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    entries = CardEntry.query.filter_by(
        card_id=card_id, batch_id=batch_id, status="em_avaliacao"
    ).all()
    for e in entries:
        e.status = "ativo"
    db.session.commit()
    flash(f"{len(entries)} lançamento(s) aprovado(s).", "success")
    return redirect(url_for("cards.detail_card", card_id=card_id))


@cards_bp.route("/<int:card_id>/lote/<batch_id>/excluir/<int:entry_id>", methods=["POST"])
@login_required
def batch_delete_entry(card_id, batch_id, entry_id):
    entry = CardEntry.query.get_or_404(entry_id)
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    db.session.delete(entry)
    db.session.commit()
    remaining = CardEntry.query.filter_by(
        card_id=card_id, batch_id=batch_id, status="em_avaliacao"
    ).count()
    if remaining == 0:
        flash("Lote concluído.", "info")
        return redirect(url_for("cards.detail_card", card_id=card_id))
    return redirect(url_for("cards.batch_review",
                            card_id=card_id, batch_id=batch_id))


@cards_bp.route("/duplicados/apagar-planejados", methods=["POST"])
@login_required
def apagar_planejados_duplicados():
    """Apaga planned_installments duplicados mantendo o de menor id."""
    from app.models import PlannedInstallment as _PI
    ids_str = request.form.get("ids", "")
    manter_id = request.form.get("manter_id", "").strip()
    ids = [i.strip() for i in ids_str.split(",") if i.strip()]
    count = 0
    for id_str in ids:
        if id_str == manter_id:
            continue
        try:
            p = _PI.query.get(int(id_str))
            if p and p.user_id == current_user.id:
                db.session.delete(p)
                count += 1
        except Exception:
            continue
    db.session.commit()
    flash(f"{count} planejado(s) duplicado(s) removido(s).", "success")
    return redirect(url_for("cards.duplicados"))


@cards_bp.route("/<int:card_id>/fechar-mes", methods=["POST"])
@login_required
def fechar_mes(card_id):
    from app.models import CardMonthHistory
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)

    mes = request.form.get("mes", "").strip()
    if not mes:
        flash("Mês não informado.", "danger")
        return redirect(url_for("cards.detail_card", card_id=card_id))

    # Evita fechar o mesmo mês duas vezes
    existente = CardMonthHistory.query.filter_by(
        user_id=current_user.id, card_id=card_id, billing_month=mes
    ).first()
    if existente:
        flash(f"Mês {mes} já foi fechado.", "warning")
        return redirect(url_for("cards.detail_card", card_id=card_id, mes=mes))

    # Busca entries do mês
    entries = CardEntry.query.filter(
        CardEntry.card_id == card_id,
        CardEntry.status == "ativo",
        CardEntry.billing_month == mes,
    ).all()

    total = round(sum(float(e.amount) for e in entries), 2)
    count = len(entries)

    import json as _json
    snapshot = _json.dumps([{
        "id": e.id,
        "description": e.description,
        "amount": float(e.amount),
        "date": str(e.entry_date),
        "kind": e.kind,
        "category": e.category,
        "installment_no": e.installment_no,
        "installments": e.installments,
    } for e in entries], ensure_ascii=False)

    hist = CardMonthHistory(
        user_id=current_user.id,
        card_id=card_id,
        billing_month=mes,
        total_geral=total,
        entry_count=count,
        snapshot=snapshot,
    )
    db.session.add(hist)
    db.session.commit()

    flash(f"Mês {mes} fechado com {count} lançamentos — total {total:,.2f}.", "success")
    return redirect(url_for("cards.detail_card", card_id=card_id, mes=mes))


@cards_bp.route("/<int:card_id>/historico/<string:mes>")
@login_required
def historico_mes(card_id, mes):
    from app.models import CardMonthHistory
    import json as _json
    card = Card.query.get_or_404(card_id)
    if card.user_id != current_user.id:
        abort(403)
    hist = CardMonthHistory.query.filter_by(
        user_id=current_user.id, card_id=card_id, billing_month=mes
    ).first_or_404()
    entries = _json.loads(hist.snapshot or "[]")
    return render_template("cards/historico_mes.html",
                           card=card, hist=hist,
                           entries=entries, mes=mes)


@cards_bp.route("/duplicados")
@login_required
def duplicados():
    """Detecta:
    - Parcelados duplicados: mesma desc_norm + installment_no + installments + amount
    - Pontuais duplicados: mesma entry_date + amount
    """
    import re as _re_dup
    from collections import defaultdict

    def _norm(s):
        s = (s or "").upper().strip()
        s = _re_dup.sub(r"[ ]+[0-9]{1,2}[ ]+DE[ ]+[0-9]{1,2}", "", s)
        s = _re_dup.sub(r"[ ]+[0-9]{1,2}/[0-9]{1,2}", "", s)
        s = _re_dup.sub(r"[ ]+[0-9]{1,2}[ ]+[0-9]{1,2}(?=[ ]|$)", "", s)
        return s[:40].strip()

    entries = CardEntry.query.filter(
        CardEntry.user_id == current_user.id,
        CardEntry.status == "ativo",
    ).order_by(CardEntry.id).all()

    # Parcelados: agrupar por (desc_norm, installment_no, installments, amount)
    parc_grupos = defaultdict(list)
    pont_grupos = defaultdict(list)

    for e in entries:
        if e.installments and e.installments > 1:
            k = (
                _norm(e.description),
                e.installment_no or 0,
                e.installments or 0,
                str(round(float(e.amount or 0), 2)),
            )
            parc_grupos[k].append(e)
        else:
            # Pontuais: duplicata = mesma data + mesmo valor
            k = (
                str(e.entry_date or ""),
                str(round(float(e.amount or 0), 2)),
            )
            pont_grupos[k].append(e)

    # Parcelados duplicados
    dup_parcelados = []
    for k, itens in parc_grupos.items():
        if len(itens) > 1:
            dup_parcelados.append({
                "tipo": "parcelado",
                "label": f"{k[0]} {k[1]}/{k[2]} — R$ {float(k[3]):.2f}",
                "chave": f"parc|{k[0]}|{k[1]}|{k[2]}|{k[3]}",
                "entries": sorted(itens, key=lambda e: e.id),
                "count": len(itens),
            })
    dup_parcelados.sort(key=lambda x: x["count"], reverse=True)

    # Pontuais duplicados
    dup_pontuais = []
    for k, itens in pont_grupos.items():
        if len(itens) > 1:
            dup_pontuais.append({
                "tipo": "pontual",
                "label": f"{k[0]} — R$ {float(k[1]):.2f} ({len(itens)}x: {', '.join(e.description[:20] for e in itens[:3])})",
                "chave": f"pont|{k[0]}|{k[1]}",
                "entries": sorted(itens, key=lambda e: e.id),
                "count": len(itens),
            })
    dup_pontuais.sort(key=lambda x: x["count"], reverse=True)

    # Duplicatas no menu Parcelados (planned_installments)
    from app.models import PlannedInstallment as _PI
    planned = _PI.query.filter_by(user_id=current_user.id).order_by(_PI.id).all()

    # Duplicata: mesmo (card_id, description, installment_no)
    plan_grupos = defaultdict(list)
    for p in planned:
        k = (p.card_id, (p.description or "").upper().strip(), p.installment_no or 0)
        plan_grupos[k].append(p)

    dup_planejados = []
    for k, itens in plan_grupos.items():
        if len(itens) > 1:
            dup_planejados.append({
                "desc": k[1],
                "installment_no": k[2],
                "entries": sorted(itens, key=lambda p: p.id),
                "count": len(itens),
            })
    dup_planejados.sort(key=lambda x: x["count"], reverse=True)

    total_dup = len(dup_parcelados) + len(dup_pontuais) + len(dup_planejados)
    return render_template("cards/duplicados.html",
                           dup_parcelados=dup_parcelados,
                           dup_pontuais=dup_pontuais,
                           dup_planejados=dup_planejados,
                           total_dup=total_dup)


@cards_bp.route("/duplicados/apagar", methods=["POST"])
@login_required
def apagar_por_descricao():
    """Apaga duplicatas pelo id_list enviado, mantendo o manter_id."""
    ids_str = request.form.get("ids", "")
    manter_id = request.form.get("manter_id", "").strip()
    ids = [i.strip() for i in ids_str.split(",") if i.strip()]
    if not ids:
        flash("Nenhum lançamento selecionado.", "warning")
        return redirect(url_for("cards.duplicados"))
    count = 0
    for id_str in ids:
        if id_str == manter_id:
            continue
        try:
            e = CardEntry.query.get(int(id_str))
            if e and e.user_id == current_user.id and e.status == "ativo":
                e.status = "excluido"
                count += 1
        except Exception:
            continue
    db.session.commit()
    flash(f"{count} duplicata(s) removida(s).", "success")
    return redirect(url_for("cards.duplicados"))
