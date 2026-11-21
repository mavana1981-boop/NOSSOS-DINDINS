"""
Reconstrói o mês de fatura (billing_month) dos lançamentos de um cartão depois
do antigo botão "Definir mês fatura", que jogava TODOS os lançamentos ativos do
cartão para um único mês.

Ordem de confiança para descobrir a fatura certa de cada lançamento:
  1. Snapshot do "Fechar mês" do cartão (CardMonthHistory) que contém o id → exato.
  2. Lote de importação (batch_id): todas as compras de um PDF são da mesma
     fatura → usa o mês mais comum calculado pelas compras à vista do lote.
  3. Parcela automática ("[parcela auto · origem #ID]") → mês da compra de
     origem + diferença de parcelas.
  4. Data da compra + dia de fechamento do cartão, ajustado pelas datas reais
     de fechamento informadas (mesma regra do Open Finance).
"""
import json
import re
from collections import Counter, defaultdict

from app import db
from app.models import Card, CardEntry, CardMonthHistory, FaturaReparoLog
from app.utils import add_meses_bm

_RE_ORIGEM = re.compile(r"\[parcela auto · origem #(\d+)\]")


def _por_data(user_id, card, e, _cache={}):
    """Convenção do app: a fatura M fecha no mês M+1 (ex.: fatura de outubro fechou
    em 13/11). Compra de d pertence à fatura M quando fechou(M-1) <= d < fechou(M).
    Usa as datas de fechamento registradas; nos meses sem registro, estima pelo
    dia típico de fechamento (mediana dos registrados, ou o do cartão, ou 13)."""
    import calendar
    from datetime import date as _d
    from app.utils import fechamentos_usuario
    f = fechamentos_usuario(user_id)
    dias = sorted(v.day for v in f.values() if v)
    dia = dias[len(dias) // 2] if dias else (card.closing_day or 13)

    def fech(bm):
        if f.get(bm):
            return f[bm]
        nx = add_meses_bm(bm, 1)
        y, m = int(nx[:4]), int(nx[5:7])
        return _d(y, m, min(dia, calendar.monthrange(y, m)[1]))

    d = e.entry_date
    bm = f"{d.year}-{d.month:02d}"
    for _ in range(24):
        if d >= fech(bm):
            bm = add_meses_bm(bm, 1)
        elif d < fech(add_meses_bm(bm, -1)):
            bm = add_meses_bm(bm, -1)
        else:
            break
    return bm


def calcular(user_id, card_id, mes_errado):
    """Devolve a proposta: lista de {entry, de, para, fonte} para os lançamentos
    ativos do cartão que estão em `mes_errado`."""
    card = db.session.get(Card, card_id)
    if not card or card.user_id != user_id:
        return None
    alvo = CardEntry.query.filter(CardEntry.card_id == card_id,
                                  CardEntry.status == "ativo",
                                  CardEntry.billing_month == mes_errado).all()

    # 1) snapshots de "Fechar mês" deste cartão
    snap = {}
    for h in CardMonthHistory.query.filter_by(user_id=user_id, card_id=card_id).all():
        try:
            for it in json.loads(h.snapshot or "[]"):
                if isinstance(it, dict) and it.get("id"):
                    snap[int(it["id"])] = h.billing_month
        except Exception:
            pass

    # 2) mês de cada lote, pelas compras à vista (datas confiáveis)
    lote_meses = defaultdict(Counter)
    lotes = {e.batch_id for e in alvo if e.batch_id}
    if lotes:
        for e in CardEntry.query.filter(CardEntry.card_id == card_id,
                                        CardEntry.batch_id.in_(lotes)).all():
            if (e.installments or 1) <= 1 and not _RE_ORIGEM.search(e.notes or ""):
                lote_meses[e.batch_id][_por_data(user_id, card, e)] += 1
    lote_mes = {b: c.most_common(1)[0][0] for b, c in lote_meses.items() if c}

    proposta = {}
    pend_auto = []
    for e in alvo:
        if e.id in snap:
            proposta[e.id] = (snap[e.id], "histórico do fechamento")
        elif _RE_ORIGEM.search(e.notes or ""):
            pend_auto.append(e)
        elif e.batch_id and e.batch_id in lote_mes:
            proposta[e.id] = (lote_mes[e.batch_id], "lote da fatura importada")
        else:
            proposta[e.id] = (_por_data(user_id, card, e), "data da compra")

    # 3) parcelas automáticas seguem a origem (já corrigida, se estava no lote errado)
    for e in pend_auto:
        oid = int(_RE_ORIGEM.search(e.notes).group(1))
        orig = db.session.get(CardEntry, oid)
        if orig:
            bm_orig = proposta.get(oid, (orig.billing_month, ""))[0]
            if bm_orig:
                delta = (e.installment_no or 1) - (orig.installment_no or 1)
                proposta[e.id] = (add_meses_bm(bm_orig, delta), "parcela da compra de origem")
                continue
        proposta[e.id] = (_por_data(user_id, card, e), "data da compra")

    linhas = []
    for e in sorted(alvo, key=lambda x: (x.entry_date, x.id)):
        para, fonte = proposta[e.id]
        linhas.append({"entry": e, "de": e.billing_month, "para": para, "fonte": fonte})

    from app.utils import fechamentos_usuario
    from app.models import ClosedMonth
    diag = {
        "fechamentos": sorted((k, v.strftime("%d/%m/%Y")) for k, v in fechamentos_usuario(user_id).items()),
        "meses_fechados": sorted(c.billing_month for c in ClosedMonth.query.filter_by(user_id=user_id).all()),
        "snapshots_cartao": sorted({m for m in snap.values()}),
        "lotes": {b: m for b, m in lote_mes.items()},
        "por_fonte": {},
        "datas": (min(e.entry_date for e in alvo).strftime("%d/%m/%Y"),
                  max(e.entry_date for e in alvo).strftime("%d/%m/%Y")) if alvo else None,
    }
    pf = defaultdict(Counter)
    for ln in linhas:
        pf[ln["fonte"]][ln["para"]] += 1
    diag["por_fonte"] = {k: dict(sorted(v.items())) for k, v in pf.items()}
    return {"card": card, "linhas": linhas, "diag": diag}


def resumo_por_mes(linhas):
    r = defaultdict(lambda: {"qtd": 0, "total": 0.0})
    for ln in linhas:
        r[ln["para"]]["qtd"] += 1
        r[ln["para"]]["total"] += float(ln["entry"].amount)
    return dict(sorted(r.items()))


def aplicar(user_id, card_id, mes_errado):
    res = calcular(user_id, card_id, mes_errado)
    if not res:
        return 0
    mud = [ln for ln in res["linhas"] if ln["para"] != ln["de"]]
    log = FaturaReparoLog(user_id=user_id, card_id=card_id,
                          descricao=f"Correção de {mes_errado} — {res['card'].name}",
                          dados=json.dumps([{"id": ln["entry"].id, "de": ln["de"], "para": ln["para"]}
                                            for ln in mud]))
    db.session.add(log)
    for ln in mud:
        ln["entry"].billing_month = ln["para"]
    db.session.commit()
    return len(mud)


def desfazer(user_id, log_id):
    log = db.session.get(FaturaReparoLog, log_id)
    if not log or log.user_id != user_id or log.desfeito:
        return 0
    n = 0
    for it in json.loads(log.dados or "[]"):
        e = db.session.get(CardEntry, it["id"])
        if e and e.user_id == user_id and e.billing_month == it["para"]:
            e.billing_month = it["de"]
            n += 1
    log.desfeito = True
    db.session.commit()
    return n
