import os
import uuid
import base64
from io import BytesIO
from decimal import Decimal
from datetime import datetime
from PIL import Image
from flask import current_app
from flask_login import current_user
from sqlalchemy import func
from app import db

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def save_profile_photo(file_storage):
    """Converte foto para base64 e retorna data URI para salvar no banco.
    A foto fica persistida no PostgreSQL — nunca se perde no deploy."""
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_image(file_storage.filename):
        return None
    try:
        img = Image.open(file_storage)
        img = img.convert("RGB")
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img = img.resize((256, 256), Image.LANCZOS)
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=80, optimize=True)
        b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        print(f"Erro processando imagem: {e}")
        return None


def format_brl(value):
    if value is None:
        return "R$ 0,00"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "R$ 0,00"
    s = f"{v:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def format_date_br(d):
    if not d:
        return ""
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%d/%m/%Y")


def register_filters(app):
    app.jinja_env.filters["brl"] = format_brl
    app.jinja_env.filters["data_br"] = format_date_br


def register_context(app):
    @app.context_processor
    def inject_globals():
        return {
            "current_year": datetime.now().year,
            "app_name": "Nosso Dindin",
        }


def get_user_balance_with(user_id, other_user_id, year=None, month=None):
    """Saldo com gastos vigentes no mês indicado (padrão: mês atual)."""
    from app.models import Expense, ExpenseShare
    from datetime import date as _d
    if year is None or month is None:
        today = _d.today()
        year, month = today.year, today.month

    def _sum_active(payer_id, share_uid):
        exps = db.session.query(Expense, ExpenseShare)\
            .join(ExpenseShare, ExpenseShare.expense_id == Expense.id)\
            .filter(Expense.payer_id == payer_id,
                    ExpenseShare.user_id == share_uid).all()
        return sum(float(share.share_amount) for exp, share in exps
                   if exp.is_active_on(year, month))

    a = _sum_active(user_id, other_user_id)
    b = _sum_active(other_user_id, user_id)
    return a - b


def get_user_monthly_summary(user_id, year, month):
    """Saldo = Renda - gastos próprios + cotas recebidas."""
    from app.models import Income, Expense, ExpenseShare
    from datetime import date as _date
    incomes = Income.query.filter_by(user_id=user_id).all()
    income_total = 0.0
    last_day = _date(year, month, 28)
    for i in incomes:
        if i.received_at.year == year and i.received_at.month == month:
            income_total += float(i.amount)
        elif i.is_recurring and i.received_at <= last_day:
            if (year, month) >= (i.received_at.year, i.received_at.month):
                income_total += float(i.amount)

    proprios = 0.0  # o que o usuário paga de verdade
    cotas = 0.0     # o que outros lhe devem

    # Gastos onde o usuário é pagador
    for exp in Expense.query.filter_by(payer_id=user_id).all():
        if not exp.is_active_on(year, month):
            continue
        for s in exp.shares:
            if s.user_id == user_id:
                proprios += float(s.share_amount)
            else:
                cotas += float(s.share_amount)

    # Gastos onde o usuário é devedor (outro pagou)
    debitos = db.session.query(Expense, ExpenseShare)\
        .join(ExpenseShare, ExpenseShare.expense_id == Expense.id)\
        .filter(ExpenseShare.user_id == user_id, Expense.payer_id != user_id).all()
    for exp, share in debitos:
        if exp.is_active_on(year, month):
            proprios += float(share.share_amount)

    return {
        "income": income_total,
        "expense": proprios,
        "cotas": cotas,
        "balance": income_total - proprios + cotas,
    }


def get_credits_debits(user_id):
    from app.models import User
    others = User.query.filter(User.id != user_id).all()
    result = []
    for o in others:
        bal = get_user_balance_with(user_id, o.id)
        if abs(bal) > 0.005:
            result.append({"user": o, "balance": bal})
    return result


def get_parcelados_from_history(user_id):
    """Reconstrói entries parcelados a partir do CardMonthHistory para projeção futura."""
    try:
        from app.models import CardMonthHistory
    except Exception:
        return []
    import json as _json
    from datetime import date as _d3
    import calendar as _cal3

    try:
        historicos = CardMonthHistory.query.filter_by(user_id=user_id).all()
    except Exception:
        return []
    parcelados = []  # lista de dicts compatível com CardEntry

    for hist in historicos:
        snap = _json.loads(hist.snapshot_json)
        for nome, dados in snap.items():
            for entry_data in dados.get("entries", []):
                parcela = entry_data.get("parcela", "")
                if not parcela or "/" not in parcela:
                    continue
                parts = parcela.split("/")
                try:
                    inst_no    = int(parts[0])
                    inst_total = int(parts[1])
                except Exception:
                    continue
                if inst_total <= 1:
                    continue
                try:
                    from datetime import datetime as _dt3
                    d = _dt3.strptime(entry_data.get("date", ""), "%Y-%m-%d").date()
                except Exception:
                    continue

                # Simula objeto compatível com CardEntry
                class _FakeEntry:
                    pass
                e = _FakeEntry()
                e.description    = entry_data.get("desc", nome)
                e.amount         = entry_data.get("amount", 0)
                e.entry_date     = d
                e.installment_no = inst_no
                e.installments   = inst_total
                e.expense_id     = None
                e.expense        = None
                parcelados.append(e)

    return parcelados


def get_open_billing_month(user_id, billing_month):
    """
    Dado um billing_month (YYYY-MM), verifica se está fechado.
    Se fechado, avança para o próximo mês aberto.
    Retorna o billing_month final (sempre aberto).
    """
    from app.models import ClosedMonth
    for _ in range(24):
        closed = ClosedMonth.query.filter_by(
            user_id=user_id, billing_month=billing_month
        ).first()
        if not closed:
            return billing_month
        # Avança um mês
        yr, mo = int(billing_month[:4]), int(billing_month[5:7])
        mo += 1
        if mo > 12:
            yr += 1; mo = 1
        billing_month = f"{yr}-{mo:02d}"
    return billing_month


def get_billing_month(entry_date, closing_day):
    """
    Retorna (year, month) do mês da fatura de uma compra.
    Se closing_day=16: compras até dia 16 → fatura do mês corrente
                       compras após dia 16 → fatura do próximo mês
    """
    if not closing_day:
        return entry_date.year, entry_date.month
    if entry_date.day > closing_day:
        if entry_date.month == 12:
            return entry_date.year + 1, 1
        return entry_date.year, entry_date.month + 1
    return entry_date.year, entry_date.month


def get_consolidated_cards(user_id, year=None, month=None):
    """
    Retorna consolidado de cartões para o mês da FATURA indicado.
    Usa closing_day do cartão para calcular a qual fatura cada entry pertence.
    """
    from app.models import Card, CardEntry
    from collections import defaultdict
    from datetime import date as _d2

    _today = _d2.today()
    if year is None:
        year = _today.year
    if month is None:
        month = _today.month

    cards = Card.query.filter_by(user_id=user_id, is_active=True).all()
    card_ids = [c.id for c in cards]
    card_closing = {c.id: c.closing_day for c in cards}

    # Busca todos os entries ativos e filtra pelo mês da fatura
    # Exclui parcelados — esses são calculados separadamente via parcelados_por_mes
    all_entries = CardEntry.query.filter(
        CardEntry.card_id.in_(card_ids),
        CardEntry.status == "ativo",
        CardEntry.kind != "parcelado",
    ).all() if card_ids else []

    mes_str = f"{year}-{month:02d}"
    consolidated = defaultdict(lambda: {"total": 0.0, "planned": 0.0})
    for entry in all_entries:
        # Usa billing_month se definido, senão usa entry_date
        if entry.billing_month:
            if entry.billing_month != mes_str:
                continue
        else:
            # Fallback: compara entry_date com o mês solicitado
            if entry.entry_date.year != year or entry.entry_date.month != month:
                continue
        if entry.expense_id and entry.expense:
            key = entry.expense.description
            consolidated[key]["planned"] = float(entry.expense.amount)
        else:
            key = entry.description
        consolidated[key]["total"] += float(entry.amount)

    return consolidated


def get_yearly_cashflow(user_id, year):
    from app import db as _db
    from app.models import Income, Expense, ExpenseShare, CashflowOverride
    # Garante sessão limpa — fecha transação antiga se houver
    try:
        _db.session.commit()
    except Exception:
        _db.session.rollback()
    overrides = {(o.year, o.month): o for o in
                 CashflowOverride.query.filter_by(user_id=user_id).all()}
    from datetime import date as _date
    months_pt = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                 "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
    from decimal import Decimal as _Dec

    # Busca EXATAMENTE igual ao menu Gastos: payer_id == user
    # O valor usado é sempre o amount do Expense (o que o user pagou)
    # consolidated_cards é calculado por mês dentro do loop

    all_expenses = Expense.query.filter(
        Expense.payer_id == user_id
    ).all()

    # Converte para lista (exp, valor) — valor é o que impacta o fluxo do user
    expenses = []
    for exp in all_expenses:
        # Para split: o custo do user é o share_amount dele
        if exp.share_mode in ("split", "integral"):
            share = ExpenseShare.query.filter_by(
                expense_id=exp.id, user_id=user_id
            ).first()
            valor = float(share.share_amount) if share else float(exp.amount)
        else:
            valor = float(exp.amount)
        expenses.append((exp, valor))

    # Lançamentos parcelados no cartão do usuário → gasto eventual por mês
    from app.models import CardEntry, Expense
    import calendar as _cal

    def _add_months(dt, n):
        month = dt.month - 1 + n
        year2 = dt.year + month // 12
        month = month % 12 + 1
        day = min(dt.day, _cal.monthrange(year2, month)[1])
        return _date(year2, month, day)

    # Busca o planejado do gasto "Cartão Parcelado" para calcular excedente
    _exp_parc = Expense.query.filter(
        Expense.payer_id == user_id,
        Expense.kind == "recorrente",
        Expense.description.ilike("%cartao parcelado%"),
    ).first()
    if not _exp_parc:
        _exp_parc = Expense.query.filter(
            Expense.payer_id == user_id,
            Expense.kind == "recorrente",
            Expense.description.ilike("%cartão parcelado%"),
        ).first()
    _planned_parc = float(_exp_parc.amount) if _exp_parc else 0.0

    # Busca parcelados via card_ids — mesma lógica da projeção do menu cartões
    from app.models import Card as _Card4
    cards_user = _Card4.query.filter_by(user_id=user_id, is_active=True).all()
    card_closing_map2 = {c.id: c.closing_day for c in cards_user}
    _card_ids2 = [c.id for c in cards_user]

    parcelados = CardEntry.query.filter(
        CardEntry.card_id.in_(_card_ids2),
        CardEntry.status == "ativo",
        CardEntry.installments > 1,
    ).all() if _card_ids2 else []


    # Deduplicar parcelados:
    # - Normaliza a descrição removendo sufixos " XX DE YY" ou " XX/YY"
    # - Agrupa por (desc_normalizada, total_parcelas, card_id)
    # - Usa o de maior installment_no (mais recente importado)
    # - Projeta APENAS installments FUTUROS que NÃO existam ainda no banco
    import re as _re

    def _norm_desc(desc):
        """Remove notação de parcela (XX DE YY, XX/YY, ou XX YY) da descrição."""
        d = (desc or "").upper().strip()
        d = _re.sub(r'\s+\d{1,2}\s+DE\s+\d{1,2}', '', d)   # "04 DE 10"
        d = _re.sub(r'\s+\d{1,2}/\d{1,2}', '', d)            # "04/10"
        d = _re.sub(r'\s+\d{1,2}\s+\d{1,2}(?=\s|$)', '', d) # "04 10" (sem DE)
        return d[:25].strip()

    # Conjunto de (desc_norm, card_id, installment_no) já existentes no banco
    # Usado para NÃO projetar installments que já foram importados
    _existing = set()  # (desc_norm, card_id, installment_no)
    for entry in parcelados:
        if entry.installment_no:
            _existing.add((_norm_desc(entry.description), entry.card_id, entry.installment_no))

    # Para cada série, pegar o entry com maior installment_no (mais recente)
    _latest = {}
    for entry in parcelados:
        if not entry.installments or entry.installment_no is None:
            continue
        _key = (_norm_desc(entry.description), entry.card_id)
        prev = _latest.get(_key)
        if prev is None or entry.installment_no > prev.installment_no:
            _latest[_key] = entry

    parcelados_por_mes = {}
    for (_nk, _cid), entry in _latest.items():
        planned_v = float(entry.expense.amount) if (entry.expense_id and entry.expense) else 0.0
        # Usar billing_month como âncora para projeção (não entry_date)
        # billing_month da parcela atual + N meses = billing_month da parcela N
        if entry.billing_month:
            try:
                _byr = int(entry.billing_month[:4])
                _bmo = int(entry.billing_month[5:7])
            except Exception:
                _byr, _bmo = entry.entry_date.year, entry.entry_date.month
        else:
            _byr, _bmo = entry.entry_date.year, entry.entry_date.month

        delta = entry.installment_no  # parcela atual → offset 0

        for i in range(entry.installment_no + 1, entry.installments + 1):
            # Pular se este installment já existe no banco
            if (_nk, _cid, i) in _existing:
                continue
            # Mês projetado = billing_month da parcela atual + (i - installment_no) meses
            _steps = i - entry.installment_no
            _pmo = _bmo + _steps - 1
            _pyr = _byr + _pmo // 12
            _pmo = (_pmo % 12) + 1
            key = (_pyr, _pmo)
            if key not in parcelados_por_mes:
                parcelados_por_mes[key] = []
            parcelados_por_mes[key].append({
                "desc": f"{entry.description} ({i}/{entry.installments})",
                "amount": round(float(entry.amount), 2),
                "expense_id": entry.expense_id,
                "planned": planned_v,
            })


    # Rendas recorrentes do usuário
    all_incomes = Income.query.filter_by(user_id=user_id).all()

    # Gastos de outros users repassados para este user
    debitos = []
    from app.models import ExpenseShare as _ESd
    shares_recebidos = _ESd.query.filter_by(user_id=user_id).all()
    for sh in shares_recebidos:
        exp2 = sh.expense
        if exp2 and exp2.payer_id != user_id:
            debitos.append((exp2, sh))

    cumulative = 0.0
    result = []

    for m in range(1, 13):
        income_recurring = 0.0
        income_eventual  = 0.0
        fixed_total      = 0.0
        eventual_total   = 0.0
        fixed_items      = []
        eventual_items   = []
        income_recurring_items = []
        income_eventual_items  = []

        # Rendas: is_recurring=True → recorrente todo mês; False → eventual só no mês
        for inc in all_incomes:
            v_inc = float(inc.amount)
            if inc.is_recurring:
                if (year, m) >= (inc.received_at.year, inc.received_at.month):
                    income_recurring += v_inc
                    income_recurring_items.append({"desc": inc.description, "amount": v_inc})
            else:
                if inc.received_at.year == year and inc.received_at.month == m:
                    income_eventual += v_inc
                    income_eventual_items.append({"desc": inc.description, "amount": v_inc})

        # Gastos
        for exp, valor in expenses:
            if not exp.is_active_on(year, m):
                continue
            # Exclui excedentes automáticos (calculados dinamicamente via cartão)
            _desc_low = (exp.description or "").lower()
            if exp.kind == "pontual" and ("excedente" in _desc_low or "cartão parcelado" in _desc_low):
                continue
            # Exclui gastos repassados dos eventuais
            if exp.share_mode in ("integral", "split") and exp.payer_id != user_id:
                continue

            v = float(valor)
            if exp.kind == "recorrente":
                if exp.share_mode in ("integral", "split"):
                    from app.models import ExpenseShare as _ES2
                    repasse_share = _ES2.query.filter(
                        _ES2.expense_id == exp.id,
                        _ES2.user_id != user_id
                    ).all()
                    repasse_v = sum(float(s.share_amount) for s in repasse_share)
                    v = max(0.0, round(v - repasse_v, 2))
                fixed_total += v
                parc_fix = ""
                if exp.recurrence_months:
                    md = (year - exp.spent_at.year) * 12 + (m - exp.spent_at.month) + 1
                    parc_fix = f" ({md}/{exp.recurrence_months})"
                fixed_items.append({
                    "desc": exp.description + parc_fix,
                    "amount": round(float(v), 2),
                })
            else:
                eventual_total += v
                eventual_items.append({
                    "desc": exp.description,
                    "amount": round(float(v), 2),
                })

        # Gastos repassados ao usuário → gastos fixos no fluxo dele
        for exp3, share3 in debitos:
            if not exp3.is_active_on(year, m):
                continue
            v2 = round(float(share3.share_amount), 2)
            if v2 <= 0:
                continue
            parc_fix2 = ""
            if exp3.kind == "recorrente" and exp3.recurrence_months:
                md2 = (year - exp3.spent_at.year) * 12 + (m - exp3.spent_at.month) + 1
                parc_fix2 = f" ({md2}/{exp3.recurrence_months})"
            fixed_total += v2
            fixed_items.append({
                "desc": f"{exp3.description}{parc_fix2}",
                "amount": v2,
            })

        # Excedente parcelados: projeção do mês - planejado "cartao parcelado"
        parc_mes = parcelados_por_mes.get((year, m), [])
        total_parc_mes = round(sum(p["amount"] for p in parc_mes), 2)
        if total_parc_mes > 0:
            excedente_parc = round(total_parc_mes - _planned_parc, 2)
            if excedente_parc > 0:
                eventual_total += excedente_parc
                eventual_items.append({
                    "desc": f"Cartão Parcelado - excedente ({total_parc_mes:.2f} - {_planned_parc:.2f})",
                    "amount": excedente_parc,
                })

        # Excedentes de compras pontuais do cartão
        consolidated_cards = get_consolidated_cards(user_id, year, m)
        for key_cc, grp_cc in consolidated_cards.items():
            if grp_cc["planned"] > 0 and grp_cc["total"] > grp_cc["planned"]:
                exc_cc = round(grp_cc["total"] - grp_cc["planned"], 2)
                eventual_total += exc_cc
                eventual_items.append({
                    "desc": f"Excedente: {key_cc}",
                    "amount": exc_cc,
                })

        # Aplica overrides manuais
        override = overrides.get((year, m))
        def _ov(attr, default):
            v = getattr(override, attr, None) if override else None
            return float(v) if v is not None else default

        income_recurring_f = _ov("income_recurring_override", income_recurring)
        income_eventual_f  = _ov("income_eventual_override",  income_eventual)
        fixed_total_f      = _ov("fixed_override",            fixed_total)
        eventual_total_f   = _ov("eventual_override",         eventual_total)

        if year == 2026 and m == 5:
            income_eventual_f  = _ov("income_eventual_override", 0.0)
            net_final        = _ov("net_override", 0.0)
            cumulative_final = _ov("cumulative_override", 0.0)
            cumulative = cumulative_final
        else:
            net_calc = (income_recurring_f + income_eventual_f) - (fixed_total_f + eventual_total_f)
            net_final        = _ov("net_override", net_calc)
            cumulative_final = _ov("cumulative_override", cumulative + net_final)
            if override and override.cumulative_override is not None:
                cumulative = cumulative_final
            else:
                cumulative += net_final

        result.append({
            "month": m,
            "month_name": months_pt[m - 1],
            "income_recurring": income_recurring_f,
            "income_eventual": income_eventual_f,
            "income": income_recurring_f + income_eventual_f,
            "fixed_expense": fixed_total_f,
            "eventual_expense": eventual_total_f,
            "total_expense": fixed_total_f + eventual_total_f,
            "net": net_final,
            "cumulative": cumulative_final,
            "eventual_items": sorted(eventual_items, key=lambda x: x["amount"], reverse=True),
            "fixed_items": sorted(fixed_items, key=lambda x: x["amount"], reverse=True),
            "income_recurring_items": sorted(income_recurring_items, key=lambda x: x["amount"], reverse=True),
            "income_eventual_items": sorted(income_eventual_items, key=lambda x: x["amount"], reverse=True),
        })
    return result
