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

    def _hora_brt(dt):
        """datetime gravado em UTC → texto dd/mm HH:MM no horário de Brasília."""
        if not dt:
            return ""
        from datetime import timezone
        from zoneinfo import ZoneInfo
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m %H:%M")
    app.jinja_env.filters["hora_brt"] = _hora_brt


def register_context(app):
    @app.context_processor
    def inject_globals():
        _nao_lidas = 0
        try:
            from flask_login import current_user as _cu
            if _cu.is_authenticated:
                from app.models import Notificacao
                _nao_lidas = Notificacao.query.filter_by(user_id=_cu.id, lida=False).count()
        except Exception:
            _nao_lidas = 0
        return {
            "current_year": datetime.now().year,
            "app_name": "Nosso Dindin",
            "notificacoes_nao_lidas": _nao_lidas,
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
    _sal = salarios_mes_mapa(user_id)
    income_total = 0.0
    last_day = _date(year, month, 28)
    for i in incomes:
        if i.received_at.year == year and i.received_at.month == month:
            income_total += valor_renda_no_mes(i, year, month, _sal)
        elif i.is_recurring and i.received_at <= last_day:
            if (year, month) >= (i.received_at.year, i.received_at.month):
                income_total += valor_renda_no_mes(i, year, month, _sal)

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


# ─────────────────────────────────────────────────────────────
#  Parcelados projetados — fonte única (menu Parcelados, Fluxo de
#  Caixa, Relatório e Saldo Proj. usam exatamente estes números)
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
#  "Mesma compra" com descrição diferente (PDF × Open Finance):
#  mesmo cartão, mês de fatura, parcela k/N, valor (±R$ 0,05) e ao
#  menos uma palavra significativa em comum na descrição.
# ─────────────────────────────────────────────────────────────
_PALAVRAS_IGNORAR = {"PARC", "PAULO", "BRASILIA", "SAO", "RIO", "JANEIRO", "BELO", "HORIZONTE",
                     "LTDA", "COMERCIO", "BRASIL", "EMISSAO", "COMPRA", "PAGAMENTO"}


def palavras_desc(s):
    import unicodedata as _ud
    s = _ud.normalize("NFKD", (s or "").upper()).encode("ascii", "ignore").decode()
    s = _re_parc.sub(r"PARC\s*\d+\s*/\s*\d+", " ", s)
    return {w for w in _re_parc.split(r"[^A-Z]+", s) if len(w) >= 4 and w not in _PALAVRAS_IGNORAR}


def mesma_compra(desc_a, desc_b):
    if norm_desc_parcela(desc_a) == norm_desc_parcela(desc_b):
        return True
    return bool(palavras_desc(desc_a) & palavras_desc(desc_b))


def pi_equivalente(user_id, card_id, billing_month, amount, installment_no, installments,
                   description, ignorar_ids=()):
    """Parcela projetada que já representa esta mesma compra (ou None)."""
    from app.models import PlannedInstallment
    try:
        v = float(amount or 0)
    except (TypeError, ValueError):
        return None
    for p in PlannedInstallment.query.filter_by(
            user_id=user_id, card_id=card_id, billing_month=billing_month,
            installment_no=installment_no, installments=installments).all():
        if p.id in ignorar_ids:
            continue
        if abs(float(p.amount or 0) - v) <= 0.05 and mesma_compra(p.description, description):
            return p
    # também pendente na sessão (ainda não gravado)
    for obj in list(db.session.new):
        if (isinstance(obj, PlannedInstallment) and obj.user_id == user_id and obj.card_id == card_id
                and obj.billing_month == billing_month and obj.installment_no == installment_no
                and obj.installments == installments
                and abs(float(obj.amount or 0) - v) <= 0.05 and mesma_compra(obj.description, description)):
            return obj
    return None


def projetar_series_em_andamento(user_id, dias=60):
    """Lançamentos de cartão k/N (k>1) feitos à mão nos últimos `dias` cuja série
    não tem projeção nem 1/N (ex.: consórcio cadastrado na parcela 15/80): projeta
    as parcelas k..N no menu Parcelados, do mês atual em diante. Idempotente."""
    from datetime import datetime as _dt, timedelta as _td, date as _date
    from app.models import CardEntry, PlannedInstallment, PlannedInstallmentDeletion
    limite = _dt.utcnow() - _td(days=dias)
    hoje = _date.today()
    mes_atual = f"{hoje.year}-{hoje.month:02d}"
    cands = CardEntry.query.filter(
        CardEntry.user_id == user_id, CardEntry.status == "ativo",
        CardEntry.installments > 1, CardEntry.installment_no > 1,
        CardEntry.billing_month.isnot(None), CardEntry.created_at >= limite).all()
    criadas = 0
    for e in cands:
        if "[parcela auto · origem #" in (e.notes or ""):
            continue                                   # gerada automaticamente
        if PlannedInstallment.query.filter_by(origin_entry_id=e.id).first():
            continue                                   # já projetada
        base = norm_desc_parcela(e.description)
        if any(norm_desc_parcela(p.description) == base for p in PlannedInstallment.query.filter_by(
                user_id=user_id, card_id=e.card_id, installments=e.installments).all()):
            continue                                   # série já tem projeção
        if any(norm_desc_parcela(c.description) == base for c in CardEntry.query.filter(
                CardEntry.user_id == user_id, CardEntry.card_id == e.card_id,
                CardEntry.installments == e.installments, CardEntry.installment_no == 1,
                CardEntry.status == "ativo").all()):
            continue                                   # série começou no app (1/N)
        dels = {(norm_desc_parcela(d.description), d.billing_month) for d in
                PlannedInstallmentDeletion.query.filter_by(user_id=user_id, card_id=e.card_id).all()}
        k = e.installment_no
        for i in range(k, e.installments + 1):
            bm = add_meses_bm(e.billing_month, i - k)
            if bm < mes_atual or (base, bm) in dels:
                continue
            if pi_equivalente(user_id, e.card_id, bm, e.amount, i, e.installments, e.description):
                continue
            db.session.add(PlannedInstallment(
                user_id=user_id, card_id=e.card_id, description=e.description,
                amount=e.amount, installment_no=i, installments=e.installments,
                billing_month=bm, expense_id=e.expense_id, origin_entry_id=e.id))
            criadas += 1
    if criadas:
        db.session.commit()
    return criadas


def parcelados_projetados(user_id):
    """Todas as parcelas projetadas (planned_installments) do usuário, já
    sincronizadas com o lançamento de cartão de origem (valor, nº de
    parcelas e descrição). Mesma lista exibida no menu Parcelados."""
    from app.models import PlannedInstallment, CardEntry
    from app import db
    try:
        projetar_series_em_andamento(user_id)
    except Exception as _e:
        db.session.rollback()
        print(f"[parcelados] projetar séries em andamento: {_e}")
    pis = PlannedInstallment.query.filter_by(user_id=user_id).all()
    mudou = False
    for p in pis:
        if not p.origin_entry_id:
            continue
        ce = CardEntry.query.get(p.origin_entry_id)
        if not ce or ce.status != "ativo":
            continue
        if float(p.amount) != float(ce.amount):
            p.amount = ce.amount
            mudou = True
        if p.installments != ce.installments:
            p.installments = ce.installments
            mudou = True
        if p.description != ce.description:
            p.description = ce.description
            mudou = True
    if mudou:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            pis = PlannedInstallment.query.filter_by(user_id=user_id).all()
    return pis


def planejado_cartao_parcelado(user_id):
    """Valor planejado do gasto "Cartão Parcelado" (com ou sem acento).
    Prefere o gasto fixo (recorrente); None se não houver."""
    from app.models import Expense
    import unicodedata as _ud
    def _n(s):
        return _ud.normalize("NFKD", (s or "")).encode("ascii", "ignore").decode().upper()
    exps = [e for e in Expense.query.filter(Expense.payer_id == user_id)
            .order_by(Expense.id).all() if "CARTAO PARCELADO" in _n(e.description)]
    if not exps:
        return None
    rec = [e for e in exps if e.kind == "recorrente"]
    return float((rec or exps)[0].amount)


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

    # Parcelados projetados e planejado "Cartão Parcelado": mesma fonte do
    # menu Parcelados (todas as parcelas, sincronizadas com o cartão)
    _planned_parc = planejado_cartao_parcelado(user_id) or 0.0
    _planned = parcelados_projetados(user_id)

    parcelados_por_mes = {}
    for _p in _planned:
        try:
            _pyr = int(_p.billing_month[:4])
            _pmo = int(_p.billing_month[5:7])
        except Exception:
            continue
        key = (_pyr, _pmo)
        if key not in parcelados_por_mes:
            parcelados_por_mes[key] = []
        _pval = float(_p.expense.amount) if (_p.expense_id and _p.expense) else 0.0
        parcelados_por_mes[key].append({
            "desc": f"{_p.description} ({_p.installment_no}/{_p.installments})",
            "amount": round(float(_p.amount), 2),
            "expense_id": _p.expense_id,
            "planned": _pval,
            "planned_id": _p.id,
        })



    # Rendas recorrentes do usuário
    all_incomes = Income.query.filter_by(user_id=user_id).all()
    _salarios = salarios_mes_mapa(user_id)   # salário calculado substitui a renda fixa no mês
    _excl = exclusoes_fluxo(user_id)          # itens tirados do fluxo só em um mês

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
        excluded_items         = []

        def _excluido(tipo, ref, grupo, desc, valor):
            if (year, m, tipo, str(ref)) in _excl:
                excluded_items.append({"desc": desc, "amount": round(float(valor), 2),
                                       "tipo": tipo, "ref": str(ref), "grupo": grupo})
                return True
            return False

        # Rendas: is_recurring=True → recorrente todo mês; False → eventual só no mês
        for inc in all_incomes:
            v_inc = valor_renda_no_mes(inc, year, m, _salarios)
            if inc.is_recurring:
                if (year, m) >= (inc.received_at.year, inc.received_at.month):
                    if _excluido("income", inc.id, "income_recurring", inc.description, v_inc):
                        continue
                    income_recurring += v_inc
                    income_recurring_items.append({"desc": inc.description, "amount": v_inc,
                                                   "tipo": "income", "ref": str(inc.id)})
            else:
                if inc.received_at.year == year and inc.received_at.month == m:
                    if _excluido("income", inc.id, "income_eventual", inc.description, v_inc):
                        continue
                    income_eventual += v_inc
                    income_eventual_items.append({"desc": inc.description, "amount": v_inc,
                                                  "tipo": "income", "ref": str(inc.id)})

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
                parc_fix = ""
                if exp.recurrence_months:
                    md = (year - exp.spent_at.year) * 12 + (m - exp.spent_at.month) + 1
                    parc_fix = f" ({md}/{exp.recurrence_months})"
                if _excluido("expense", exp.id, "fixed", exp.description + parc_fix, v):
                    continue
                fixed_total += v
                fixed_items.append({
                    "desc": exp.description + parc_fix,
                    "amount": round(float(v), 2),
                    "tipo": "expense", "ref": str(exp.id),
                })
            else:
                if _excluido("expense", exp.id, "eventual", exp.description, v):
                    continue
                eventual_total += v
                eventual_items.append({
                    "desc": exp.description,
                    "amount": round(float(v), 2),
                    "tipo": "expense", "ref": str(exp.id),
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
            if _excluido("expense", exp3.id, "fixed", f"{exp3.description}{parc_fix2}", v2):
                continue
            fixed_total += v2
            fixed_items.append({
                "desc": f"{exp3.description}{parc_fix2}",
                "amount": v2,
                "tipo": "expense", "ref": str(exp3.id),
            })

        # Excedente parcelados: projeção do mês - planejado "cartao parcelado"
        parc_mes = parcelados_por_mes.get((year, m), [])
        total_parc_mes = round(sum(p["amount"] for p in parc_mes), 2)
        if total_parc_mes > 0:
            excedente_parc = round(total_parc_mes - _planned_parc, 2)  # igual ao menu
            _desc_exc = f"Cartão Parcelado - excedente ({total_parc_mes:.2f} - {_planned_parc:.2f})"
            if excedente_parc > 0 and not _excluido("parcelado", "excedente", "eventual",
                                                    _desc_exc, excedente_parc):
                eventual_total += excedente_parc
                eventual_items.append({
                    "desc": _desc_exc,
                    "amount": excedente_parc,
                    "tipo": "parcelado", "ref": "excedente",
                })

        # Excedentes de compras pontuais do cartão
        # Exclui "Cartão Parcelado" — já tratado como excedente de parcelados acima
        consolidated_cards = get_consolidated_cards(user_id, year, m)
        for key_cc, grp_cc in consolidated_cards.items():
            if "cartao parcelado" in key_cc.lower() or "cartão parcelado" in key_cc.lower():
                continue  # já contabilizado em parcelados_por_mes
            if grp_cc["planned"] > 0 and grp_cc["total"] > grp_cc["planned"]:
                exc_cc = round(grp_cc["total"] - grp_cc["planned"], 2)
                if _excluido("excedente", key_cc, "eventual", f"Excedente: {key_cc}", exc_cc):
                    continue
                eventual_total += exc_cc
                eventual_items.append({
                    "desc": f"Excedente: {key_cc}",
                    "amount": exc_cc,
                    "tipo": "excedente", "ref": str(key_cc),
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
            "saldo_breakdown": {
                "income_recurring": income_recurring_f,
                "income_eventual":  income_eventual_f,
                "fixed_total":      fixed_total_f,
                "eventual_total":   eventual_total_f,
                "net":              net_final,
            },
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
            "excluded_items": excluded_items,
        })
    return result


# ─────────────────────────────────────────────────────────────
#  Saldo projetado do mês — fonte única usada pelo Relatório
#  (Projeção de Saldo) e pelo Fluxo de Caixa (coluna Saldo)
#  Saldo Proj. = Renda fixa − Fixos − Excedentes − Excedente parcelados
# ─────────────────────────────────────────────────────────────
def _norm_sem_acento(s):
    import unicodedata as _ud
    return _ud.normalize("NFKD", (s or "")).encode("ascii", "ignore").decode().upper()


def saldo_projetado_meses(user_id, meses, cf_cache=None):
    """meses: lista de (ano, mes). Devolve {(ano, mes): {...}} com renda,
    fixos, parcelados, excedente, ev_itens, total_ev, saldo e os itens que
    compõem renda e fixos. cf_cache opcional: {(ano, mes): mês do
    get_yearly_cashflow} para evitar recalcular."""
    from app.models import Expense, PlannedInstallment
    cf_cache = dict(cf_cache or {})
    for _ano in {a for a, _ in meses}:
        if not any(k[0] == _ano for k in cf_cache):
            for _m in get_yearly_cashflow(user_id, _ano):
                cf_cache[(_ano, _m["month"])] = _m

    # Parcelados projetados (mesma fonte do menu Parcelados)
    por_mes_pi = {}
    for _p in parcelados_projetados(user_id):
        por_mes_pi.setdefault(_p.billing_month, []).append(float(_p.amount))

    # Planejado do gasto "Cartão Parcelado"
    planned_cards = planejado_cartao_parcelado(user_id) or 0.0

    out = {}
    for ano, mes in meses:
        cf = cf_cache.get((ano, mes), {})
        renda = float(cf.get("income_recurring", 0) or 0)
        fixos = float(cf.get("fixed_expense", 0) or 0)
        total_pi = sum(por_mes_pi.get(f"{ano}-{mes:02d}", []))
        exc = max(0.0, total_pi - planned_cards)
        ev_itens, total_ev = [], 0.0
        for ei in cf.get("eventual_items", []):
            desc = str(ei.get("desc", ""))
            amt = float(ei.get("amount", 0))
            dn = _norm_sem_acento(desc)
            # Excedente de cartão parcelado tem coluna própria
            if "CARTAO PARCELADO" in dn and "EXCEDENTE" in dn:
                continue
            if amt <= 0:
                continue
            ev_itens.append({"desc": desc, "valor": amt})
            total_ev += amt
        out[(ano, mes)] = {
            "renda": round(renda, 2),
            "fixos": round(fixos, 2),
            "parcelados": round(total_pi, 2),
            "excedente": round(exc, 2),
            "ev_itens": ev_itens,
            "total_ev": round(total_ev, 2),
            "saldo": round(renda - fixos - exc - total_ev, 2),
            "renda_itens": [{"desc": i["desc"], "valor": i["amount"]}
                            for i in cf.get("income_recurring_items", [])],
            "fixos_itens": [{"desc": i["desc"], "valor": i["amount"]}
                            for i in cf.get("fixed_items", [])],
        }
    return out


def anexar_ano_seguinte(user_id, year, months):
    """Acrescenta os 12 meses de (year + 1) ao fluxo de `year`, marcados com
    is_next_year. O acumulado continua a partir de dez/year; se algum mês do
    ano seguinte tiver acumulado fixado à mão, dali em diante vale o valor
    fixado (sem somar o saldo de dezembro)."""
    from app.models import CashflowOverride
    try:
        prox = get_yearly_cashflow(user_id, year + 1)
    except Exception:
        prox = []
    if not prox:
        return months
    fixados = {o.month for o in CashflowOverride.query.filter_by(
        user_id=user_id, year=year + 1).all() if o.cumulative_override is not None}
    if year + 1 == 2026:
        fixados.add(5)
    base = months[-1]["cumulative"] if months else 0.0
    extra = []
    for m in prox:
        m = dict(m)
        m["is_next_year"] = True
        m.setdefault("eventual_items", [])
        if m["month"] in fixados:
            base = 0.0
        m["cumulative"] = round(base + m["cumulative"], 2)
        extra.append(m)
    return months + extra


def aplicar_saldo_projetado(user_id, meses_cf):
    """meses_cf: lista de (ano, dict do mês do get_yearly_cashflow), em ordem.
    Troca 'net' pelo saldo projetado (mantém ajuste manual de saldo, se houver)
    e recalcula 'cumulative' preservando os acumulados fixados manualmente."""
    from app.models import CashflowOverride
    ovs = {(o.year, o.month): o for o in
           CashflowOverride.query.filter_by(user_id=user_id).all()}
    cache = {(a, m["month"]): m for a, m in meses_cf}
    proj = saldo_projetado_meses(user_id, list(cache.keys()), cf_cache=cache)
    ajuste = 0.0
    for ano, m in meses_cf:
        ov = ovs.get((ano, m["month"]))
        p = proj[(ano, m["month"])]
        m["saldo_proj"] = p
        m["net_fluxo"] = m["net"]
        m["net_override"] = bool(ov and ov.net_override is not None)
        fixo_manual = (ano == 2026 and m["month"] == 5) or m["net_override"]
        novo = m["net"] if fixo_manual else p["saldo"]
        if (ov and ov.cumulative_override is not None) or (ano == 2026 and m["month"] == 5):
            ajuste = 0.0
        else:
            # Acumulado soma o saldo projetado + a renda eventual do mês
            # (ajuste manual de saldo já é o valor final do mês)
            contrib = novo if fixo_manual else novo + float(m.get("income_eventual", 0) or 0)
            ajuste += contrib - m["net"]
            m["cumulative"] = round(m["cumulative"] + ajuste, 2)
        m["net"] = round(novo, 2)
    return meses_cf


def parse_vinculo(raw, user_id):
    """
    Lê o valor do select de vínculo: "" (avulso), "12" (gasto 12) ou
    "12:5" (gasto 12, subcategoria 5). Valida que a subcategoria pertence
    ao gasto e ao usuário. Retorna (expense_id, subcategoria_id).
    """
    from app.models import Subcategoria
    raw = (raw or "").strip()
    if not raw:
        return None, None
    exp_part, _, sub_part = raw.partition(":")
    if not exp_part.isdigit():
        return None, None
    exp_id = int(exp_part)
    sub_id = None
    if sub_part.isdigit():
        sub = Subcategoria.query.get(int(sub_part))
        if sub and sub.expense_id == exp_id and sub.user_id == user_id:
            sub_id = sub.id
    return exp_id, sub_id


# ─────────────────────────────────────────────────────────────
#  Parcelas futuras como lançamentos reais de cartão
#  Regra: ao entrar uma compra parcelada (1/N — ou k/N lançada à mão),
#  o app já cria as parcelas seguintes (k+1..N) como CardEntry "ativo"
#  no mês da fatura correspondente. Importações (PDF/Open Finance) de
#  parcelas 2+ são ignoradas, pois essas parcelas já existem.
# ─────────────────────────────────────────────────────────────
import re as _re_parc

PARC_AUTO_TAG = "[parcela auto · origem #{id}]"


def norm_desc_parcela(s):
    """Descrição sem o número da parcela (ex.: 'NOVA PARC 04/12' → 'NOVA PARC')."""
    s = (s or "").upper().strip()
    s = _re_parc.sub(r"\s+\d{1,2}\s+DE\s+\d{1,2}", "", s)
    s = _re_parc.sub(r"\s*\b\d{1,2}/\d{1,2}\b", "", s)
    s = _re_parc.sub(r"\bPARC(ELA)?\b", "", s)
    s = _re_parc.sub(r"\s+", " ", s)
    return s[:30].strip()


def chave_serie_parcela(card_id, description, installments, amount):
    """Identifica uma série parcelada (mesma compra) independente da parcela."""
    return (card_id, norm_desc_parcela(description), int(installments or 1),
            round(float(amount or 0), 2))


def add_meses_bm(bm, n):
    yr, mo = int(bm[:4]), int(bm[5:7])
    mo = mo - 1 + n
    return f"{yr + mo // 12}-{mo % 12 + 1:02d}"


def _desc_parcela_i(description, k, n, i):
    """Troca 'k/N' por 'i/N' na descrição, preservando zeros à esquerda."""
    def _rep(m):
        a, b = m.group(1), m.group(2)
        if int(a) == k and int(b) == n:
            return f"{i:0{len(a)}d}/{b}"
        return m.group(0)
    return _re_parc.sub(r"\b(\d{1,2})/(\d{1,2})\b", _rep, description or "", count=1)


def _add_meses_data(d, n):
    import calendar as _cal
    from datetime import date as _date
    mo = d.month - 1 + n
    yr = d.year + mo // 12
    mo = mo % 12 + 1
    return _date(yr, mo, min(d.day, _cal.monthrange(yr, mo)[1]))


def _parcela_ja_existe(entry, i):
    """Já existe lançamento para a parcela i desta série? Considera ativos/em
    avaliação e também parcelas automáticas desta origem que o usuário excluiu
    (para não recriar o que foi apagado de propósito)."""
    from app.models import CardEntry
    alvo = chave_serie_parcela(entry.card_id, entry.description,
                               entry.installments, entry.amount)
    tag = PARC_AUTO_TAG.format(id=entry.id)
    cands = CardEntry.query.filter(
        CardEntry.card_id == entry.card_id,
        CardEntry.user_id == entry.user_id,
        CardEntry.installments == entry.installments,
        CardEntry.installment_no == i,
        CardEntry.id != entry.id,
    ).all()
    for c in cands:
        if c.status == "excluido" and tag not in (c.notes or ""):
            continue
        k = chave_serie_parcela(c.card_id, c.description, c.installments, c.amount)
        if k[1] == alvo[1] and abs(k[3] - alvo[3]) <= 0.05:
            return True
    return False


def gerar_parcelas_futuras(entry, mes_minimo=None, commit=True):
    """Cria as parcelas installment_no+1..N de um lançamento parcelado ativo,
    cada uma no mês de fatura correspondente. Idempotente.
    mes_minimo (YYYY-MM): não cria parcelas em meses anteriores a ele.
    Retorna a quantidade criada."""
    from app.models import CardEntry, PlannedInstallmentDeletion
    if (not entry or entry.status != "ativo" or not entry.billing_month
            or not entry.installments or entry.installments <= 1):
        return 0
    k = entry.installment_no or 1
    n = entry.installments
    if k >= n:
        return 0
    tag = PARC_AUTO_TAG.format(id=entry.id)
    exclusoes = PlannedInstallmentDeletion.query.filter_by(
        user_id=entry.user_id, card_id=entry.card_id).all()
    base = norm_desc_parcela(entry.description)
    criadas = 0
    for i in range(k + 1, n + 1):
        bm = add_meses_bm(entry.billing_month, i - k)
        if mes_minimo and bm < mes_minimo:
            continue
        # (Exclusões do controle de parcelados não bloqueiam: elas se referem a
        #  projeções duplicadas, não à parcela real lida da fatura.)
        if _parcela_ja_existe(entry, i):
            continue
        db.session.add(CardEntry(
            card_id=entry.card_id,
            user_id=entry.user_id,
            description=_desc_parcela_i(entry.description, k, n, i)[:160],
            amount=entry.amount,
            entry_date=_add_meses_data(entry.entry_date, i - k),
            expense_id=entry.expense_id,
            subcategoria_id=entry.subcategoria_id,
            category=entry.category,
            kind="parcelado",
            installments=n,
            installment_no=i,
            status="ativo",
            billing_month=bm,
            notes=tag,
        ))
        criadas += 1
    if criadas and commit:
        db.session.commit()
    return criadas


def atualizar_parcelas_futuras(entry, commit=True):
    """Propaga valor/vínculo/categoria da origem para as parcelas automáticas
    ainda ativas e cria as que faltarem."""
    from app.models import CardEntry
    tag = PARC_AUTO_TAG.format(id=entry.id)
    k = entry.installment_no or 1
    for c in CardEntry.query.filter(
            CardEntry.notes.contains(tag), CardEntry.status == "ativo").all():
        c.amount = entry.amount
        c.expense_id = entry.expense_id
        c.subcategoria_id = entry.subcategoria_id
        c.category = entry.category
        c.description = _desc_parcela_i(entry.description, k, entry.installments,
                                        c.installment_no)[:160]
    criadas = gerar_parcelas_futuras(entry, commit=False)
    if commit:
        db.session.commit()
    return criadas


def excluir_parcelas_futuras(entry, commit=True):
    """Ao excluir a origem, exclui (soft) as parcelas automáticas dela."""
    from app.models import CardEntry
    tag = PARC_AUTO_TAG.format(id=entry.id)
    n = 0
    for c in CardEntry.query.filter(
            CardEntry.notes.contains(tag), CardEntry.status == "ativo").all():
        c.status = "excluido"
        n += 1
    if n and commit:
        db.session.commit()
    return n


def e_parcela_seguinte_importada(installments, installment_no):
    """Importação (PDF/Open Finance) de parcela 2+ → deve ser ignorada."""
    try:
        return int(installments or 1) > 1 and int(installment_no or 1) > 1
    except (TypeError, ValueError):
        return False


# ─────────────────────────────────────────────────────────────
#  Regras fixas de classificação (definidas pelo usuário)
#   1. Compra parcelada (N > 1)          → "Cartão Parcelado"
#   2. Drogaria/farmácia  < R$ 50        → "Cartão Avulso";  ≥ R$ 50 → "Farmácia"
#   3. Posto/combustível  < R$ 50        → "Cartão Avulso";  ≥ R$ 50 → "Gasolina"
# ─────────────────────────────────────────────────────────────
_RX_DROGARIA = _re_parc.compile(
    r"DROGA|DROGARIA|DROGASIL|\bRAIA\b|FARMA|PANVEL|PAGUE ?MENOS|ULTRAFARMA|NISSEI|PACHECO")
_RX_POSTO = _re_parc.compile(
    r"POSTO|COMBUST|SHELL|IPIRANGA|PETROBRAS|\bBR MANIA|ALE COMB|REDE AUTO|BARATAO|GASOLINA")


def _expense_por_nome(user_id, *nomes):
    from app.models import Expense
    import unicodedata as _ud
    def _n(s):
        return _ud.normalize("NFKD", (s or "")).encode("ascii", "ignore").decode().upper().strip()
    alvos = {_n(x) for x in nomes}
    exps = [e for e in Expense.query.filter(Expense.payer_id == user_id)
            .order_by(Expense.id).all() if _n(e.description) in alvos]
    rec = [e for e in exps if e.kind == "recorrente"]
    return (rec or exps or [None])[0]


def regra_fixa_gasto(user_id, description, amount, installments):
    """Devolve o Expense que a regra fixa manda usar, ou None se nenhuma se aplica."""
    try:
        n = int(installments or 1)
    except (TypeError, ValueError):
        n = 1
    if n > 1:
        return _expense_por_nome(user_id, "Cartão Parcelado", "Cartao Parcelado")
    d = (description or "").upper()
    v = float(amount or 0)
    if _RX_DROGARIA.search(d):
        return (_expense_por_nome(user_id, "Cartão Avulso", "Cartao Avulso") if v < 50
                else _expense_por_nome(user_id, "Farmácia", "Farmacia"))
    if _RX_POSTO.search(d):
        return (_expense_por_nome(user_id, "Cartão Avulso", "Cartao Avulso") if v < 50
                else _expense_por_nome(user_id, "Gasolina"))
    return None


def aplicar_regra_fixa(entry):
    """Aplica a regra fixa ao lançamento (se houver). Retorna True se mudou."""
    exp = regra_fixa_gasto(entry.user_id, entry.description, entry.amount, entry.installments)
    if not exp:
        return False
    entry.expense_id = exp.id
    entry.subcategoria_id = None
    entry.category = (exp.description or "")[:60]
    return True


# ─────────────────────────────────────────────────────────────
#  Data real de fechamento da fatura
#  Ao fechar a fatura M informando a data (ex.: 13/11), toda compra com
#  data >= fechamento vai para a fatura seguinte (M+1) — inclusive as
#  que já estavam lançadas em M e as que forem importadas depois.
# ─────────────────────────────────────────────────────────────
def fechamentos_usuario(user_id):
    """{billing_month: data_de_fechamento} das faturas com data informada."""
    from app.models import ClosedMonth
    return {cm.billing_month: cm.fechou_em for cm in
            ClosedMonth.query.filter(ClosedMonth.user_id == user_id,
                                     ClosedMonth.fechou_em.isnot(None)).all()}


def ajustar_fatura_por_fechamento(user_id, entry_date, billing_month, permitir_recuar=False):
    """Corrige a fatura de uma compra pela data real de fechamento.

    - Avança: se a fatura M já fechou e a compra é do dia do fechamento em
      diante, vai para M+1 (repetindo enquanto houver fechamentos).
    - Recua (só quando a fatura foi calculada pelo dia de fechamento do
      cartão, ex.: Open Finance): se a fatura anterior fechou DEPOIS da
      compra, a compra pertence a ela.
    """
    from datetime import timedelta
    if not billing_month or not entry_date:
        return billing_month
    f = fechamentos_usuario(user_id)
    if not f:
        return billing_month
    bm = billing_month
    for _ in range(24):
        d = f.get(bm)
        if d and entry_date >= d:
            bm = add_meses_bm(bm, 1)
        else:
            break
    if permitir_recuar and bm == billing_month:
        ant = add_meses_bm(bm, -1)
        d = f.get(ant)
        if d and d - timedelta(days=45) <= entry_date < d:
            bm = ant
    return bm


def _e_parcela_auto(entry):
    return "[parcela auto · origem #" in (entry.notes or "")


def mover_lancamento_de_fatura(entry, delta):
    """Move a compra `delta` meses de fatura, levando junto as parcelas
    automáticas e os parcelados planejados que nasceram dela."""
    from app.models import CardEntry, PlannedInstallment
    entry.billing_month = add_meses_bm(entry.billing_month, delta)
    tag = PARC_AUTO_TAG.format(id=entry.id)
    for c in CardEntry.query.filter(CardEntry.notes.contains(tag)).all():
        if c.billing_month:
            c.billing_month = add_meses_bm(c.billing_month, delta)
    for p in PlannedInstallment.query.filter_by(origin_entry_id=entry.id).all():
        if p.billing_month:
            p.billing_month = add_meses_bm(p.billing_month, delta)


def reatribuir_apos_fechamento(user_id, billing_month, fechou_em):
    """Aplica a data de fechamento aos lançamentos já existentes:
      - compras da fatura M com data >= fechamento → M+1
      - compras da fatura M+1 com data < fechamento (fechamento atrasado) → M
    Parcelas 2/N em diante seguem a compra de origem. Retorna (movidos_p_frente, movidos_p_tras)."""
    from datetime import timedelta
    from app.models import CardEntry
    prox = add_meses_bm(billing_month, 1)
    vivos = CardEntry.status.in_(["ativo", "em_avaliacao"])
    frente = tras = 0
    for e in CardEntry.query.filter(CardEntry.user_id == user_id,
                                    CardEntry.billing_month == billing_month,
                                    CardEntry.entry_date >= fechou_em, vivos).all():
        if _e_parcela_auto(e) or (e.installment_no or 1) > 1:
            continue
        mover_lancamento_de_fatura(e, +1)
        frente += 1
    for e in CardEntry.query.filter(CardEntry.user_id == user_id,
                                    CardEntry.billing_month == prox,
                                    CardEntry.entry_date < fechou_em,
                                    CardEntry.entry_date >= fechou_em - timedelta(days=12),
                                    vivos).all():
        if _e_parcela_auto(e) or (e.installment_no or 1) > 1:
            continue
        mover_lancamento_de_fatura(e, -1)
        tras += 1
    return frente, tras


def fatura_que_fechou_em(user_id, fechou_em):
    """Descobre qual fatura (YYYY-MM) contém as compras dos dias anteriores
    ao fechamento — é a fatura que fechou nessa data."""
    from datetime import timedelta
    from collections import Counter
    from app.models import CardEntry
    cont = Counter()
    for e in CardEntry.query.filter(CardEntry.user_id == user_id,
                                    CardEntry.entry_date < fechou_em,
                                    CardEntry.entry_date >= fechou_em - timedelta(days=12),
                                    CardEntry.billing_month.isnot(None),
                                    CardEntry.status.in_(["ativo", "em_avaliacao"])).all():
        if _e_parcela_auto(e) or (e.installment_no or 1) > 1:
            continue
        cont[e.billing_month] += 1
    return cont.most_common(1)[0][0] if cont else None


def registrar_fechamento(user_id, billing_month, fechou_em):
    """Fecha a fatura M na data informada e realoca os lançamentos.
    Não faz commit. Retorna (movidos_p_frente, movidos_p_tras)."""
    from app.models import ClosedMonth
    cm = ClosedMonth.query.filter_by(user_id=user_id, billing_month=billing_month).first()
    if not cm:
        cm = ClosedMonth(user_id=user_id, billing_month=billing_month)
        db.session.add(cm)
    cm.fechou_em = fechou_em
    db.session.flush()
    return reatribuir_apos_fechamento(user_id, billing_month, fechou_em)


# ═════════════════════════════════════════════════════════════════════
#  Salário do mês — valor recebido + consignados substitui a renda fixa
# ═════════════════════════════════════════════════════════════════════
def salarios_mes_mapa(user_id):
    """{(ano, mes, income_id): valor_entrada} dos salários calculados do usuário."""
    from app.models import SalarioMes
    out = {}
    try:
        for s in SalarioMes.query.filter_by(user_id=user_id).all():
            try:
                out[(int(s.mes_ref[:4]), int(s.mes_ref[5:7]), s.income_id)] = float(s.valor_entrada)
            except (ValueError, TypeError):
                continue
    except Exception as e:  # tabela ainda não criada, etc.
        print(f"[salario_mes] erro ao ler: {e}")
    return out


def valor_renda_no_mes(inc, ano, mes, mapa=None):
    """Valor da renda no mês: o salário calculado, se houver para essa renda fixa; senão o cadastrado."""
    if mapa and inc.is_recurring:
        v = mapa.get((ano, mes, inc.id))
        if v is not None:
            return v
    return float(inc.amount)


KW_CONSIGNADO = ["consign"]


def consignados_do_mes(user_id, ano, mes, marcar_ids=None):
    """
    Gastos recorrentes do usuário ativos no mês com cara de empréstimo (ou com prazo).
    Vêm marcados ('consignado'): os com 'consign' na descrição/categoria e os
    usados no último cálculo (marcar_ids).
    Retorna lista de dicts {expense, valor, consignado, parcela}.
    """
    from app.models import Expense
    from app.planejamento_ia import KW_EMPRESTIMO, _norm, _tem
    marcar_ids = set(marcar_ids or [])
    out = []
    for e in Expense.query.filter_by(payer_id=user_id, kind="recorrente").all():
        if not e.is_active_on(ano, mes):
            continue
        nome = _norm(f"{e.description} {e.category or ''}")
        if not (_tem(nome, KW_EMPRESTIMO) or e.recurrence_months or e.id in marcar_ids):
            continue
        meu = next((sh.share_amount for sh in e.shares if sh.user_id == user_id), None)
        valor = float(meu if meu is not None else e.amount)
        out.append({"expense": e, "valor": round(valor, 2),
                    "consignado": _tem(nome, KW_CONSIGNADO) or e.id in marcar_ids,
                    "parcela": e.parcel_label(ano, mes)})
    out.sort(key=lambda x: (not x["consignado"], x["expense"].description.lower()))
    return out


def exclusoes_fluxo(user_id):
    """{(ano, mes, tipo, ref)} dos itens tirados do Fluxo de Caixa num mês específico."""
    from app.models import FluxoExclusao
    try:
        return {(e.year, e.month, e.tipo, e.ref)
                for e in FluxoExclusao.query.filter_by(user_id=user_id).all()}
    except Exception as e:
        print(f"[fluxo_exclusao] erro ao ler: {e}")
        return set()
