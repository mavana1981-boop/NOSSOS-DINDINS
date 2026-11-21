"""
Planejamento — quatro análises financeiras com IA a partir dos dados do app.

  receitas    → Plano de Fluxos de Receitas (7 fluxos + roteiro de 12 meses)
  dividas     → Motor de Eliminação de Dívidas (Bola de Neve × Avalanche × Híbrida)
  vazamentos  → Detector de Vazamentos de Gastos (plano de 30 dias)
  orcamento   → Orçamento Sem Vergonha (50/30/20 adaptado + roteiros sociais)

Os números "duros" (simulação das dívidas, assinaturas prováveis, delivery,
tarifas, totais por categoria) são calculados aqui em Python. A IA só
interpreta e escreve o plano — ela não refaz as contas.

Cadeia de IA (texto): Claude → Gemini → Groq, igual à importação de faturas.
"""
import html
import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime

MODULOS = {
    "receitas":   {"titulo": "Plano de Fluxos de Receitas", "icone": "🌱",
                   "resumo": "7 fontes de renda possíveis para a sua situação, com ordem de implementação e roteiro de 12 meses."},
    "dividas":    {"titulo": "Motor de Eliminação de Dívidas", "icone": "⛓️",
                   "resumo": "Bola de Neve × Avalanche × Híbrida com juros, datas de quitação e plano mês a mês."},
    "vazamentos": {"titulo": "Detector de Vazamentos", "icone": "🔎",
                   "resumo": "Assinaturas esquecidas, delivery, tarifas e pequenos gastos recorrentes nos seus cartões."},
    "orcamento":  {"titulo": "Orçamento Sem Vergonha", "icone": "🧭",
                   "resumo": "50/30/20 adaptado à vida real, revisão semanal e roteiros para recusar planos caros."},
}

MESES_ANALISE = 3  # janelas de fatura usadas no detector de vazamentos


# ═════════════════════════════════════════════════════════════════════
#  Utilidades
# ═════════════════════════════════════════════════════════════════════
def _f(v):
    try:
        return round(float(v or 0), 2)
    except Exception:
        return 0.0


def _norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s


def _chave_comerciante(desc):
    """Normaliza a descrição do lançamento para agrupar o mesmo comerciante."""
    s = _norm(desc)
    s = re.sub(r"\b\d{1,2}\s*(de|/)\s*\d{1,2}\b", " ", s)      # 03 DE 10, 3/10
    s = re.sub(r"\bparc\w*\b", " ", s)
    s = re.sub(r"[\d*#\-_.:/]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return " ".join(s.split()[:3]) or _norm(desc)[:20]


def _add_mes(ym, n):
    y, m = int(ym[:4]), int(ym[5:7])
    m += n
    while m > 12:
        y += 1; m -= 12
    while m < 1:
        y -= 1; m += 12
    return f"{y}-{m:02d}"


def _mes_br(ym):
    nomes = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
    return f"{nomes[int(ym[5:7]) - 1]}/{ym[:4]}"


def _brl(v):
    s = f"{float(v):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def render_markdown(texto):
    """Markdown → HTML. O HTML vindo da IA é escapado antes (só markdown passa)."""
    texto = texto or ""
    try:
        import markdown as _md
        seguro = html.escape(texto, quote=False).replace("&gt;", ">")
        return _md.markdown(seguro, extensions=["tables", "sane_lists", "nl2br"])
    except Exception:
        return "<pre style='white-space:pre-wrap'>" + html.escape(texto) + "</pre>"


# ═════════════════════════════════════════════════════════════════════
#  Coleta de dados do app
# ═════════════════════════════════════════════════════════════════════
def _perfil(user_id):
    from app.models import PlanejamentoPerfil
    return PlanejamentoPerfil.query.filter_by(user_id=user_id).first()


def resumo_renda_gastos(user_id):
    """Renda HABITUAL (só rendas recorrentes) e gastos fixos do mês corrente.
    Rendas eventuais (13º, férias, restituição, bônus) ficam separadas em rendas_eventuais."""
    from app.models import Income, Expense

    hoje = date.today()
    ano, mm = hoje.year, hoje.month
    mes = f"{ano}-{mm:02d}"
    ini_ev, fim_ev = _add_mes(mes, -6), _add_mes(mes, 12)

    rendas, eventuais = [], []
    for i in Income.query.filter_by(user_id=user_id).all():
        ym = i.received_at.strftime("%Y-%m")
        if i.is_recurring and ym <= mes:
            rendas.append({"descricao": i.description, "categoria": i.category, "valor": _f(i.amount)})
        elif not i.is_recurring and ini_ev <= ym <= fim_ev:
            eventuais.append({"descricao": i.description, "categoria": i.category, "valor": _f(i.amount),
                              "mes": ym, "data": i.received_at.isoformat(),
                              "situacao": "futura" if ym > mes else ("deste mês" if ym == mes else "já recebida")})
    eventuais.sort(key=lambda x: x["data"])

    fixas = []
    for e in Expense.query.filter_by(payer_id=user_id).all():
        if e.kind == "recorrente" and e.is_active_on(ano, mm):
            meu = next((sh.share_amount for sh in e.shares if sh.user_id == user_id), None)
            item = {"descricao": e.description, "categoria": e.category, "valor": _f(meu if meu is not None else e.amount)}
            if e.recurrence_months:
                n = (ano - e.spent_at.year) * 12 + (mm - e.spent_at.month) + 1
                item["parcela"] = f"{n}/{e.recurrence_months}"
            fixas.append(item)
    fixas.sort(key=lambda x: -x["valor"])

    p = _perfil(user_id)
    renda = _f(p.renda_override) if (p and p.renda_override) else round(sum(r["valor"] for r in rendas), 2)
    despesas = _f(p.fixas_override) if (p and p.fixas_override) else round(sum(x["valor"] for x in fixas), 2)
    return {
        "mes_referencia": mes,
        "renda_mensal": renda,                 # habitual
        "despesas_planejadas_mes": despesas,   # gastos fixos/recorrentes (sua parte)
        "renda_informada_manualmente": bool(p and p.renda_override),
        "despesas_informadas_manualmente": bool(p and p.fixas_override),
        "rendas": rendas,
        "rendas_eventuais": eventuais,
        "gastos_planejados": fixas[:40],
    }


def fluxo_caixa(user_id, passados=6, futuros=12):
    """Série mensal do Fluxo de Caixa do app (mesma conta do menu), separando
    renda habitual × eventual e gastos fixos × eventuais/estouros."""
    from app.utils import get_yearly_cashflow
    mes = date.today().strftime("%Y-%m")
    alvo = [_add_mes(mes, k) for k in range(-passados, futuros + 1)]
    por_ano = {}
    out = []
    for ym in alvo:
        y, m = int(ym[:4]), int(ym[5:7])
        if y not in por_ano:
            try:
                por_ano[y] = get_yearly_cashflow(user_id, y)
            except Exception:
                por_ano[y] = []
        linha = next((r for r in por_ano[y] if r.get("month") == m), None)
        if not linha:
            continue
        out.append({
            "mes": _mes_br(ym), "ym": ym, "passado": ym < mes,
            "renda_habitual": _f(linha["income_recurring"]),
            "renda_eventual": _f(linha["income_eventual"]),
            "rendas_eventuais_itens": [{"desc": x["desc"], "valor": _f(x["amount"])}
                                       for x in linha.get("income_eventual_items", [])][:6],
            "gastos_fixos": _f(linha["fixed_expense"]),
            "gastos_eventuais_e_estouros": _f(linha["eventual_expense"]),
            "maiores_eventuais": [{"desc": x["desc"], "valor": _f(x["amount"])}
                                  for x in linha.get("eventual_items", [])][:5],
            "saldo_do_mes": _f(linha["net"]),
            "saldo_sem_eventuais": round(_f(linha["income_recurring"]) - _f(linha["fixed_expense"])
                                         - _f(linha["eventual_expense"]), 2),
        })
    return out


def estouro_planejados(user_id, meses=6):
    """Planejado × gasto real nos cartões, por gasto planejado, nas últimas faturas fechadas."""
    from app.models import Expense, CardEntry
    from app.utils import get_open_billing_month
    atual = get_open_billing_month(user_id, date.today().strftime("%Y-%m"))
    janela = [_add_mes(atual, -k) for k in range(meses, 0, -1)]
    linhas = []
    for e in Expense.query.filter_by(payer_id=user_id, kind="recorrente").all():
        ents = CardEntry.query.filter(CardEntry.expense_id == e.id,
                                      CardEntry.billing_month.in_(janela + [atual]),
                                      CardEntry.status == "ativo").all()
        if not ents:
            continue
        planejado = _f(e.amount)
        por_mes = defaultdict(float)
        for x in ents:
            por_mes[x.billing_month] += float(x.amount)
        meses_l = []
        for ym in janela:
            if not e.is_active_on(int(ym[:4]), int(ym[5:7])):
                continue
            gasto = round(por_mes.get(ym, 0.0), 2)
            meses_l.append({"fatura": _mes_br(ym), "planejado": planejado, "gasto": gasto,
                            "estouro": round(max(gasto - planejado, 0), 2)})
        if not meses_l:
            continue
        tot_est = round(sum(m["estouro"] for m in meses_l), 2)
        linhas.append({"gasto_planejado": e.description, "planejado_mensal": planejado,
                       "media_gasta": round(sum(m["gasto"] for m in meses_l) / len(meses_l), 2),
                       "meses_estourados": sum(1 for m in meses_l if m["estouro"] > 0),
                       "meses_analisados": len(meses_l), "estouro_total": tot_est,
                       "estouro_medio_mensal": round(tot_est / len(meses_l), 2),
                       "fatura_aberta_ate_agora": round(por_mes.get(atual, 0.0), 2),
                       "por_mes": meses_l})
    linhas.sort(key=lambda x: -x["estouro_total"])
    return linhas


def _lancamentos_cartao(user_id, meses):
    from app.models import CardEntry
    from app.utils import get_open_billing_month
    atual = get_open_billing_month(user_id, date.today().strftime("%Y-%m"))
    # Usa as faturas já fechadas (completas) + a aberta
    janela = [_add_mes(atual, -k) for k in range(meses, -1, -1)]
    q = CardEntry.query.filter(
        CardEntry.user_id == user_id,
        (CardEntry.status == "ativo") | (CardEntry.status == None),  # noqa: E711
        CardEntry.billing_month.in_(janela),
    ).all()
    return q, janela, atual


KW_DELIVERY = ["ifood", "rappi", "uber eats", "ubereats", "99food", "ze delivery", "zedelivery",
               "aiqfome", "james delivery", "daki", "keeta", "delivery"]
KW_TRANSPORTE = ["uber", "99app", "99 pop", "99pop", "99 tecnologia", "cabify", "posto", "shell",
                 "ipiranga", "petrobras", "combust", "estacion", "estapar", "sem parar", "semparar",
                 "conectcar", "veloe", "pedagio", "taxi"]
KW_TARIFAS = ["anuidade", "tarifa", "iof", "juros", "encargo", "multa", "mora", "rotativo",
              "seguro", "protecao", "protegido", "cesta de servic", "saque"]
KW_ASSINATURA = ["netflix", "spotify", "amazon prime", "prime video", "primevideo", "disney",
                 "hbo", "max.com", "globoplay", "paramount", "apple.com", "apple com", "icloud",
                 "google one", "google storage", "youtube", "deezer", "microsoft", "office",
                 "adobe", "canva", "chatgpt", "openai", "claude", "anthropic", "dropbox",
                 "smart fit", "smartfit", "gympass", "wellhub", "totalpass", "kindle", "audible",
                 "assinatura", "clube", "mensalidade", "premium", "plus"]
KW_ENTRETENIMENTO = ["cinema", "cinemark", "ingresso", "sympla", "eventim", "bar ", "choperia",
                     "steam", "playstation", "xbox", "nintendo"]
KW_ENERGIA = ["neoenergia", "ceb", "enel", "cemig", "cpfl", "light", "sabesp", "caesb", "claro",
              "vivo", "tim ", "oi ", "net ", "sky"]


def _tem(desc_n, kws):
    return any(k in desc_n for k in kws)


def diagnostico_vazamentos(user_id, completo=False):
    """completo=True devolve também todos os lançamentos de cada grupo (para a tela)."""
    entradas, janela, atual = _lancamentos_cartao(user_id, MESES_ANALISE)
    fechados = [m for m in janela if m != atual]
    n_meses = max(len({e.billing_month for e in entradas if e.billing_month != atual}), 1)

    por_comerciante = defaultdict(lambda: {"total": 0.0, "qtd": 0, "meses": set(), "valores": [],
                                           "descricao": "", "parcelado": False})
    por_categoria = defaultdict(float)
    por_planejado = defaultdict(float)
    delivery, transporte, tarifas, entretenimento, energia = [], [], [], [], []
    pequenos = {"qtd": 0, "total": 0.0}
    lanc = defaultdict(list)          # grupo → lançamentos (só para a tela)
    itens_comerciante = defaultdict(list)

    for e in entradas:
        v = _f(e.amount)
        if v <= 0:
            continue
        dn = _norm(e.description)
        k = _chave_comerciante(e.description)
        c = por_comerciante[k]
        c["total"] += v; c["qtd"] += 1; c["meses"].add(e.billing_month); c["valores"].append(v)
        c["descricao"] = c["descricao"] or e.description
        if (e.kind == "parcelado") or (e.installments or 1) > 1:
            c["parcelado"] = True

        if e.billing_month in fechados:
            por_categoria[e.category or "Outros"] += v
            if e.expense_id and e.expense:
                por_planejado[e.expense.description] += v

        item = {"descricao": e.description, "valor": v, "data": e.entry_date.isoformat(),
                "fatura": e.billing_month}
        if completo:
            cheio = {**item, "id": e.id, "card_id": e.card_id,
                     "cartao": e.card.name if e.card else "", "categoria": e.category or "Outros",
                     "parcela": (f"{e.installment_no}/{e.installments}"
                                 if (e.installments or 1) > 1 else "")}
            itens_comerciante[k].append(cheio)
            if e.billing_month in fechados:
                lanc["todos_fechados"].append(cheio)
                lanc["cat:" + (e.category or "Outros")].append(cheio)
            if _tem(dn, KW_DELIVERY):
                lanc["delivery"].append(cheio)
            elif _tem(dn, KW_TRANSPORTE):
                lanc["transporte"].append(cheio)
            if _tem(dn, KW_TARIFAS):
                lanc["tarifas"].append(cheio)
            if _tem(dn, KW_ENTRETENIMENTO):
                lanc["entretenimento"].append(cheio)
            if _tem(dn, KW_ENERGIA):
                lanc["consumo"].append(cheio)
            if v < 30 and e.kind != "parcelado":
                lanc["pequenos"].append(cheio)
        if _tem(dn, KW_DELIVERY):
            delivery.append(item)
        elif _tem(dn, KW_TRANSPORTE):
            transporte.append(item)
        if _tem(dn, KW_TARIFAS):
            tarifas.append(item)
        if _tem(dn, KW_ENTRETENIMENTO):
            entretenimento.append(item)
        if _tem(dn, KW_ENERGIA):
            energia.append(item)
        if v < 30 and e.kind != "parcelado":
            pequenos["qtd"] += 1; pequenos["total"] += v

    assinaturas = []
    for k, c in por_comerciante.items():
        if c["parcelado"]:
            continue
        vals = c["valores"]
        media = sum(vals) / len(vals)
        estavel = all(abs(x - media) <= max(0.15 * media, 2) for x in vals)
        nome_assin = _tem(_norm(c["descricao"]), KW_ASSINATURA)
        if (len(c["meses"]) >= 2 and estavel) or (nome_assin and len(vals) >= 1):
            assinaturas.append({"chave": k, "descricao": c["descricao"], "valor_medio": round(media, 2),
                                "meses_em_que_apareceu": len(c["meses"]),
                                "estimativa_anual": round(media * 12, 2)})
    assinaturas.sort(key=lambda x: -x["valor_medio"])

    top = sorted(por_comerciante.items(), key=lambda kv: -kv[1]["total"])[:50]
    top_comerciantes = [{"chave": k, "descricao": c["descricao"], "total_periodo": round(c["total"], 2),
                         "compras": c["qtd"], "meses": len(c["meses"]),
                         "parcelado": c["parcelado"]} for k, c in top]

    def _soma(lst):
        return round(sum(x["valor"] for x in lst), 2)

    resultado = {
        "faturas_analisadas": [_mes_br(m) for m in janela],
        "fatura_aberta_parcial": _mes_br(atual),
        "meses_fechados_considerados": n_meses,
        "total_lancamentos": len(entradas),
        "gasto_medio_mensal_cartoes": round(sum(por_categoria.values()) / n_meses, 2),
        "media_mensal_por_categoria": {k: round(v / n_meses, 2) for k, v in
                                       sorted(por_categoria.items(), key=lambda kv: -kv[1])},
        "media_mensal_por_gasto_planejado": {k: round(v / n_meses, 2) for k, v in
                                             sorted(por_planejado.items(), key=lambda kv: -kv[1])},
        "assinaturas_provaveis": assinaturas[:40],
        "delivery": {"qtd": len(delivery), "total": _soma(delivery), "itens": delivery[:40]},
        "transporte": {"qtd": len(transporte), "total": _soma(transporte), "itens": transporte[:40]},
        "tarifas_juros_seguros": {"qtd": len(tarifas), "total": _soma(tarifas), "itens": tarifas[:40]},
        "entretenimento": {"qtd": len(entretenimento), "total": _soma(entretenimento),
                           "itens": entretenimento[:30]},
        "contas_consumo_telecom": {"qtd": len(energia), "total": _soma(energia), "itens": energia[:30]},
        "pequenas_compras_ate_30": {"qtd": pequenos["qtd"], "total": round(pequenos["total"], 2)},
        "maiores_comerciantes": top_comerciantes,
    }
    if not completo:
        for lst in (resultado["assinaturas_provaveis"], resultado["maiores_comerciantes"]):
            for x in lst:
                x.pop("chave", None)
        return resultado
    for a in resultado["assinaturas_provaveis"] + resultado["maiores_comerciantes"]:
        lanc["com:" + a["chave"]] = itens_comerciante[a["chave"]]
    for lst in lanc.values():
        lst.sort(key=lambda x: (x["data"], x["id"]), reverse=True)
    resultado["lancamentos"] = dict(lanc)
    return resultado


def dados_patrimonio(user_id):
    from app.models import Investment
    invs = Investment.query.filter_by(user_id=user_id, is_active=True).all()
    return [{"descricao": i.description, "categoria": i.category, "objetivo": i.objective,
             "aplicado": _f(i.amount), "valor_atual": _f(i.current_value or i.amount),
             "instituicao": i.institution} for i in invs]


# ═════════════════════════════════════════════════════════════════════
#  Motor de dívidas (cálculo determinístico)
# ═════════════════════════════════════════════════════════════════════
KW_EMPRESTIMO = ["emprest", "financ", "consign", "credito pessoal", "crediario", "cdc",
                 "renegoc", "acordo", "parcelamento", "divida", "consorcio", "fies", "cheque especial"]


def _parcela_do_usuario(exp, user_id):
    meu = next((sh.share_amount for sh in exp.shares if sh.user_id == user_id), None)
    return _f(meu if meu is not None else exp.amount)


def parcelas_restantes(exp, hoje=None):
    """Parcelas que ainda faltam A PARTIR DO MÊS QUE VEM (a do mês atual conta como deste mês).
    None se o gasto não tem prazo."""
    if not exp.recurrence_months:
        return None
    hoje = hoje or date.today()
    passados = (hoje.year - exp.spent_at.year) * 12 + (hoje.month - exp.spent_at.month)
    return max(int(exp.recurrence_months) - passados - 1, 0)


def saldo_estimado(parcela, taxa_mensal_pct, n):
    """Saldo devedor pela Tabela Price: valor presente das parcelas restantes.
    Sem taxa, soma simples das parcelas."""
    if not n:
        return 0.0
    i = float(taxa_mensal_pct or 0) / 100.0
    if i <= 0:
        return round(parcela * n, 2)
    return round(parcela * (1 - (1 + i) ** -n) / i, 2)


def candidatos_emprestimo(user_id):
    """Gastos recorrentes que parecem empréstimo/financiamento e ainda não viraram dívida."""
    from app.models import Expense, Divida
    ja = {d.expense_id for d in Divida.query.filter(Divida.user_id == user_id,
                                                      Divida.expense_id != None).all()}  # noqa: E711
    out = []
    for e in Expense.query.filter_by(payer_id=user_id, kind="recorrente").all():
        if e.id in ja:
            continue
        nome = _norm(f"{e.description} {e.category or ''}")
        parece = _tem(nome, KW_EMPRESTIMO)
        n = parcelas_restantes(e)
        if n is not None and n <= 0:
            continue            # já terminou
        if not parece and n is None:
            continue            # recorrente sem prazo e sem cara de empréstimo (conta de luz etc.)
        parcela = _parcela_do_usuario(e, user_id)
        out.append({"expense": e, "parcela": parcela, "restantes": n,
                    "total_restante": round(parcela * n, 2) if n else None,
                    "parece_emprestimo": parece})
    out.sort(key=lambda x: (not x["parece_emprestimo"], -(x["total_restante"] or 0)))
    return out


def sincronizar_dividas_de_gastos(user_id):
    """Atualiza parcela, saldo e situação das dívidas ligadas a um gasto. Chamado ao abrir a tela."""
    from app import db
    from app.models import Divida
    mudou = False
    for d in Divida.query.filter(Divida.user_id == user_id, Divida.expense_id != None).all():  # noqa: E711
        e = d.expense
        if not e:
            continue
        parcela = _parcela_do_usuario(e, user_id)
        n = parcelas_restantes(e)
        if _f(d.pagamento_minimo) != parcela:
            d.pagamento_minimo = parcela; mudou = True
        if n is not None and not d.saldo_manual:
            novo = saldo_estimado(parcela, d.taxa_mensal, n)
            if _f(d.saldo) != novo:
                d.saldo = novo; mudou = True
        ativa = not (n is not None and n <= 0 and not d.saldo_manual)
        if d.ativa != ativa:
            d.ativa = ativa; mudou = True
    if mudou:
        db.session.commit()


def _ordem(dividas, estrategia, extra):
    if estrategia == "bola_de_neve":
        return sorted(dividas, key=lambda d: (d["saldo"], -d["taxa"]))
    if estrategia == "avalanche":
        return sorted(dividas, key=lambda d: (-d["taxa"], d["saldo"]))
    # Híbrida: primeiro as "vitórias rápidas" (quitáveis em até 3 meses só com o
    # extra + mínimo), menor saldo primeiro; depois o resto por maior taxa.
    folga = max(extra, 0)
    rapidas = [d for d in dividas if d["saldo"] <= 3 * (folga + d["minimo"])]
    resto = [d for d in dividas if d not in rapidas]
    return sorted(rapidas, key=lambda d: d["saldo"]) + sorted(resto, key=lambda d: -d["taxa"])


def simular(dividas_in, extra, estrategia, inicio=None, limite_meses=600, aportes=None):
    """Simula mês a mês. Taxa em % a.m. O orçamento mensal total (mínimos + extra)
    é fixo; quando uma dívida acaba, o mínimo dela "rola" para a próxima.
    aportes = {"YYYY-MM": valor} → pagamentos extras pontuais (verbas eventuais)."""
    aportes = aportes or {}
    inicio = inicio or _add_mes(date.today().strftime("%Y-%m"), 1)
    dividas = [{"id": d["id"], "nome": d["nome"], "saldo": float(d["saldo"]),
                "taxa": float(d["taxa"]) / 100.0, "minimo": float(d["minimo"])}
               for d in dividas_in if float(d["saldo"]) > 0]
    if not dividas:
        return None
    ordem = _ordem(dividas, estrategia, extra)
    orcamento = sum(d["minimo"] for d in dividas) + max(float(extra), 0)
    juros_tot = {d["id"]: 0.0 for d in dividas}
    quitacao = {}
    cronograma = []
    mes = inicio
    alerta = None
    saldo_inicial = sum(d["saldo"] for d in dividas)

    for n in range(1, limite_meses + 1):
        vivos = [d for d in ordem if d["saldo"] > 0.005]
        if not vivos:
            break
        linha = {"mes": mes, "pagamentos": {}, "saldos": {}}
        for d in vivos:
            j = d["saldo"] * d["taxa"]
            d["saldo"] += j
            juros_tot[d["id"]] += j
        caixa = orcamento + float(aportes.get(mes, 0) or 0)
        for d in vivos:  # mínimos
            p = min(d["minimo"], d["saldo"], caixa)
            d["saldo"] -= p; caixa -= p
            linha["pagamentos"][d["id"]] = p
        for d in ordem:  # sobra vai para a dívida-alvo, na ordem da estratégia
            if caixa <= 0.005:
                break
            if d["saldo"] > 0.005:
                p = min(d["saldo"], caixa)
                d["saldo"] -= p; caixa -= p
                linha["pagamentos"][d["id"]] = linha["pagamentos"].get(d["id"], 0) + p
        for d in ordem:
            if d["saldo"] <= 0.005 and d["id"] not in quitacao:
                d["saldo"] = 0.0
                quitacao[d["id"]] = mes
            linha["saldos"][d["id"]] = round(d["saldo"], 2)
        cronograma.append(linha)
        mes = _add_mes(mes, 1)
        if sum(d["saldo"] for d in ordem) > 3 * saldo_inicial:
            alerta = ("Com esse pagamento as dívidas não terminam: os juros superam o que é pago. "
                      "Aumente o extra mensal ou renegocie as taxas.")
            break
    else:
        alerta = ("Com esse pagamento as dívidas não terminam em 50 anos: os juros superam "
                  "o que é pago. Aumente o extra mensal ou renegocie as taxas.")

    fim = max(quitacao.values()) if len(quitacao) == len(dividas) else None
    total_juros = round(sum(juros_tot.values()), 2) if fim else None
    return {
        "estrategia": estrategia,
        "ordem": [{"id": d["id"], "nome": d["nome"], "quitada_em": quitacao.get(d["id"]),
                   "juros": round(juros_tot[d["id"]], 2), "parcela": round(d["minimo"], 2)} for d in ordem],
        "aportes_eventuais": round(sum(float(v or 0) for v in aportes.values()), 2),
        "pagamento_mensal_total": round(orcamento, 2),
        "total_juros": total_juros,
        "meses": len(cronograma) if fim else None,
        "quitacao_final": fim,
        "alerta": alerta,
        "cronograma": cronograma,
    }


def comparar_estrategias(user_id, extra=None):
    from app.models import Divida
    ds = Divida.query.filter_by(user_id=user_id, ativa=True).order_by(Divida.id).all()
    if not ds:
        return None, []
    if extra is None:
        p = _perfil(user_id)
        extra = _f(p.pagamento_extra) if p else 0.0
    base = [{"id": d.id, "nome": d.nome, "saldo": _f(d.saldo), "taxa": float(d.taxa_mensal or 0),
             "minimo": _f(d.pagamento_minimo)} for d in ds]
    res = {}
    for est in ("bola_de_neve", "avalanche", "hibrida"):
        res[est] = simular(base, extra, est)
    return res, base


def aportes_eventuais(user_id, pct):
    """Rendas eventuais FUTURAS (13º, férias, restituição…) → aportes de pct% no mês seguinte ao recebimento."""
    rg = resumo_renda_gastos(user_id)
    ap = defaultdict(float)
    for r in rg["rendas_eventuais"]:
        if r["situacao"] in ("futura", "deste mês"):
            ap[_add_mes(r["mes"], 1)] += r["valor"] * pct / 100.0
    return dict(ap)


def cenarios_antecipacao(user_id, extra=None):
    """Compara: só parcelas × extra mensal × extra + 50%/100% das verbas eventuais,
    usando a estratégia que paga menos juros. Mostra quando cada parcela é liberada."""
    res, base = comparar_estrategias(user_id, extra=extra)
    if not res:
        return None
    validas = [v for v in res.values() if v and v["quitacao_final"]]
    melhor = min(validas, key=lambda v: v["total_juros"])["estrategia"] if validas else "avalanche"
    p = _perfil(user_id)
    if extra is None:
        extra = _f(p.pagamento_extra) if p else 0.0
    defs = [("Só as parcelas", 0.0, 0), (f"Parcelas + extra de {_brl(extra)}/mês", extra, 0),
            ("Extra + 50% das verbas eventuais", extra, 50), ("Extra + 100% das verbas eventuais", extra, 100)]
    out = []
    vistos = set()
    for nome, ex, pct in defs:
        ap = aportes_eventuais(user_id, pct) if pct else {}
        chave = (ex, tuple(sorted(ap.items())))
        if chave in vistos:
            continue
        vistos.add(chave)
        r = simular(base, ex, melhor, aportes=ap)
        if not r:
            continue
        liberadas = sorted([o for o in r["ordem"] if o["quitada_em"]], key=lambda o: o["quitada_em"])
        out.append({"cenario": nome, "estrategia": melhor, "extra_mensal": ex, "pct_eventuais": pct,
                    "aportes": {(_mes_br(k)): round(v, 2) for k, v in sorted(ap.items())},
                    "juros_totais": r["total_juros"],
                    "quitacao_final": _mes_br(r["quitacao_final"]) if r["quitacao_final"] else None,
                    "meses": r["meses"],
                    "parcelas_liberadas": [{"divida": o["nome"], "mes": _mes_br(o["quitada_em"]),
                                            "parcela_liberada": o["parcela"]} for o in liberadas],
                    "_sim": r})
    if out:
        ref = next((c for c in out if c["juros_totais"] is not None), None)
        base_j = ref["juros_totais"] if ref else None
        for c in out:
            c["economia_comparada_com"] = ref["cenario"] if ref else None
            c["economia_juros"] = (round(base_j - c["juros_totais"], 2)
                                                  if base_j is not None and c["juros_totais"] is not None else None)
    return {"estrategia": melhor, "cenarios": out, "base": base}


def _resumo_estrategia(r, base):
    """Versão enxuta para mandar à IA (sem o cronograma inteiro)."""
    if not r:
        return None
    nomes = {d["id"]: d["nome"] for d in base}
    marcos = []
    for ln in r["cronograma"][:36]:
        marcos.append({"mes": _mes_br(ln["mes"]),
                       "pagamentos": {nomes[k]: round(v, 2) for k, v in ln["pagamentos"].items() if v > 0},
                       "saldos": {nomes[k]: v for k, v in ln["saldos"].items()}})
    return {
        "ordem_de_pagamento": [o["nome"] for o in r["ordem"]],
        "quitacao_por_divida": {o["nome"]: (_mes_br(o["quitada_em"]) if o["quitada_em"] else "não quita")
                                for o in r["ordem"]},
        "juros_por_divida": {o["nome"]: o["juros"] for o in r["ordem"]},
        "pagamento_mensal_total": r["pagamento_mensal_total"],
        "juros_totais": r["total_juros"],
        "meses_ate_quitar_tudo": r["meses"],
        "data_quitacao_final": _mes_br(r["quitacao_final"]) if r["quitacao_final"] else None,
        "alerta": r["alerta"],
        "cronograma_primeiros_36_meses": marcos,
    }


# ═════════════════════════════════════════════════════════════════════
#  Montagem dos prompts
# ═════════════════════════════════════════════════════════════════════
SISTEMA = (
    "Você é um planejador financeiro pessoal que trabalha com famílias brasileiras. "
    "Escreva em português do Brasil, em Markdown (títulos ##/###, tabelas Markdown, listas). "
    "Use valores em reais no formato R$ 1.234,56. Considere a realidade brasileira: CDI/Selic, "
    "Tesouro Direto, CDB, LCI/LCA, FIIs, rotativo e parcelamento de cartão, cheque especial, "
    "consignado, portabilidade de dívida, Imposto de Renda, MEI/Simples Nacional. "
    "Os números que vierem calculados nos DADOS são a fonte de verdade: não os recalcule nem os "
    "contradiga; se faltar informação, diga o que falta e faça suposições explícitas. "
    "Seja concreto e prático, sem moralismo. Não invente lançamentos que não estão nos dados. "
    "Você não é consultor de investimentos registrado: dê informação para decisão, sem "
    "recomendar produtos específicos de instituições."
)


def _origem_divida(divida_id):
    from app import db
    from app.models import Divida
    d = db.session.get(Divida, divida_id)
    if not d or not d.expense:
        return {}
    return {"origem": "empréstimo/financiamento de parcela fixa lançado em Gastos "
                      "(o pagamento mínimo é a própria parcela; pagar a mais = amortização antecipada)",
            "parcelas_restantes": parcelas_restantes(d.expense),
            "saldo": ("informado pelo usuário" if d.saldo_manual
                      else "estimado pelo valor presente das parcelas restantes")}


def montar_entrada(user_id, modulo):
    from app.models import Divida
    p = _perfil(user_id)
    rg = resumo_renda_gastos(user_id)
    perfil = {
        "pessoas_no_domicilio": (p.pessoas_domicilio if p else None),
        "capital_disponivel": _f(p.capital_disponivel) if p else 0,
        "horas_livres_por_semana": (p.horas_semana if p else 0),
        "pagamento_extra_mensal_para_dividas": _f(p.pagamento_extra) if p else 0,
        "maior_gatilho_de_gastos": (p.gatilho_gasto if p else None) or "não informado",
        "contexto_pessoal": (p.contexto if p else None) or "não informado",
    }

    if modulo == "receitas":
        dados = {"perfil": perfil, "renda_e_gastos": rg, "investimentos": dados_patrimonio(user_id),
                 "dividas_ativas": [{"nome": d.nome, "saldo": _f(d.saldo),
                                     "taxa_mensal_pct": float(d.taxa_mensal or 0)}
                                    for d in Divida.query.filter_by(user_id=user_id, ativa=True)]}
        tarefa = (
            "Atue como estrategista de finanças pessoais. Quero construir múltiplos fluxos de receita e "
            "reduzir a dependência de uma única fonte.\n\n"
            "1. Comece com um diagnóstico curto: fontes atuais, grau de concentração da renda e riscos.\n"
            "2. Projete **7 fluxos de receita** adequados à minha situação (pode incluir salário/promoção, "
            "investimentos, renda extra com serviços, negócio paralelo, dividendos/FIIs, aluguel, produtos "
            "digitais, afiliados, serviços profissionais). Use meu contexto pessoal e as horas e capital "
            "disponíveis — nada que exija mais tempo ou dinheiro do que tenho.\n"
            "3. Apresente uma **tabela** com uma linha por fluxo e as colunas: Fluxo | Dificuldade de início | "
            "Custo inicial (R$) | Tempo até gerar receita | Risco | Escalabilidade | Ordem recomendada.\n"
            "4. Para cada fluxo, um parágrafo curto com primeiro passo concreto e receita mensal "
            "realista esperada (faixa).\n"
            "5. **Roteiro de 12 meses** em tabela (Mês | Foco | Ações | Meta de R$).\n"
            "6. **Checklist** de resiliência financeira (reserva de emergência em meses de despesa, "
            "seguros, diversificação, IR, etc.) com caixas [ ].\n"
            "Se houver dívidas caras ativas, diga claramente o que priorizar antes de investir."
        )
    elif modulo == "dividas":
        res, base = comparar_estrategias(user_id)
        cen = cenarios_antecipacao(user_id)
        vz = diagnostico_vazamentos(user_id)
        recomendado = None
        if cen and cen["cenarios"]:
            # cronograma do cenário mais agressivo (o último) para o plano mês a mês
            recomendado = _resumo_estrategia(cen["cenarios"][-1]["_sim"], base)
            recomendado.pop("cronograma_primeiros_36_meses", None)
            recomendado["cronograma_primeiros_24_meses"] = _resumo_estrategia(
                cen["cenarios"][-1]["_sim"], base)["cronograma_primeiros_36_meses"][:24]
        dados = {
            "perfil": perfil,
            "renda": {
                "renda_habitual_mensal": rg["renda_mensal"],
                "rendas_habituais": rg["rendas"],
                "rendas_eventuais_6m_atras_ate_12m_a_frente": rg["rendas_eventuais"],
                "gastos_fixos_mensais": rg["despesas_planejadas_mes"],
                "maiores_gastos_fixos": rg["gastos_planejados"][:20],
            },
            "fluxo_de_caixa_mensal": fluxo_caixa(user_id),
            "estouro_de_planejados_ultimas_faturas": [
                {k: v for k, v in x.items() if k != "por_mes"} | {"por_fatura": x["por_mes"]}
                for x in estouro_planejados(user_id)[:12]],
            "vazamentos_cartoes": {
                "gasto_medio_mensal_cartoes": vz["gasto_medio_mensal_cartoes"],
                "media_mensal_por_categoria": vz["media_mensal_por_categoria"],
                "assinaturas_provaveis": vz["assinaturas_provaveis"][:15],
                "delivery": {"qtd": vz["delivery"]["qtd"], "total_periodo": vz["delivery"]["total"]},
                "tarifas_juros_seguros": {"qtd": vz["tarifas_juros_seguros"]["qtd"],
                                          "total_periodo": vz["tarifas_juros_seguros"]["total"],
                                          "itens": vz["tarifas_juros_seguros"]["itens"][:10]},
                "pequenas_compras_ate_30": vz["pequenas_compras_ate_30"],
                "maiores_comerciantes_nao_parcelados": [c for c in vz["maiores_comerciantes"]
                                                        if not c["parcelado"]][:15],
                "meses_considerados": vz["meses_fechados_considerados"],
            },
            "dividas": [{"nome": d["nome"], "saldo": d["saldo"], "taxa_mensal_pct": d["taxa"],
                         "parcela_ou_minimo": d["minimo"], **_origem_divida(d["id"])} for d in base],
            "comparacao_estrategias": {k: {kk: vv for kk, vv in (_resumo_estrategia(v, base) or {}).items()
                                           if kk != "cronograma_primeiros_36_meses"}
                                       for k, v in (res or {}).items()},
            "cenarios_de_antecipacao_calculados": [{k: v for k, v in c.items() if k != "_sim"}
                                                   for c in (cen["cenarios"] if cen else [])],
            "cenario_mais_agressivo_detalhado": recomendado,
        }
        tarefa = (
            "Atue como planejador financeiro focado em FLUXO DE CAIXA e quitação antecipada de empréstimos. "
            "Meta do usuário: garantir um fluxo de recursos para antecipar os empréstimos. Não basta dizer a "
            "ordem de pagamento — quero um plano de onde sai o dinheiro.\n\n"
            "REGRAS DE LEITURA DOS DADOS:\n"
            "- Renda HABITUAL é só renda_habitual_mensal. Rendas eventuais (13º, férias, restituição, bônus, "
            "vendas) NUNCA entram como renda mensal: são verbas pontuais para aportes extras.\n"
            "- Use os números calculados pelo app (fluxo, estouros, cenários, simulações) sem recalcular.\n"
            "- Parcelas de compras parceladas antigas não são vazamento.\n\n"
            "ESTRUTURA DA RESPOSTA:\n"
            "## 1. Diagnóstico do fluxo de caixa\n"
            "Tabela dos últimos 6 meses e próximos meses: Mês | Renda habitual | Renda eventual | Gastos fixos | "
            "Eventuais/estouros | Saldo | Saldo sem eventuais. Diga claramente se o mês a mês se sustenta só "
            "com a renda habitual e quais meses só fecharam por causa de verba eventual.\n"
            "## 2. Onde o dinheiro está escapando\n"
            "a) **Estouros de planejados**: tabela Gasto planejado | Planejado | Média gasta | Meses estourados | "
            "Estouro médio/mês | Ação. b) **Maiores vazamentos** nos cartões (assinaturas, delivery, tarifas, "
            "pequenas compras, comerciantes recorrentes) com economia mensal realista. Some quanto dá para "
            "liberar por mês e proponha esse valor como **extra mensal fixo** para os empréstimos.\n"
            "## 3. Estratégia de antecipação\n"
            "Compare os cenários calculados (só parcelas × extra mensal × extra + 50%/100% das eventuais) em "
            "tabela: Cenário | Juros totais | Economia | Quitação final | Parcelas liberadas e quando. Recomende "
            "um cenário. Explique o efeito bola de neve: cada parcela liberada vira aporte no próximo empréstimo.\n"
            "## 4. Plano para cada verba eventual\n"
            "Para cada renda eventual futura: quanto vai para antecipação, em qual empréstimo, quanto fica de "
            "reserva (sugira manter um colchão mínimo), e o que pedir ao banco: amortização com **redução de "
            "prazo** (mais economia de juros) ou **redução de parcela** (libera fluxo mensal imediato) — "
            "recomende qual em cada caso, lembrando o desconto proporcional de juros garantido pelo CDC "
            "(art. 52, §2º). Para consignados, cite a portabilidade se a taxa estiver acima do mercado.\n"
            "## 5. Calendário de 12 meses\n"
            "Tabela: Mês | Entrada eventual | Extra mensal (cortes) | Aporte em qual empréstimo | Parcela "
            "liberada no mês | Fluxo livre acumulado | Marco. Use o cenário mais agressivo detalhado como base.\n"
            "## 6. Regras do jogo\n"
            "5 a 8 regras práticas (ex.: % fixo de toda verba eventual para dívida, teto de estouro por "
            "categoria, revisão semanal no app) e uma tabela de acompanhamento para preencher.\n"
            "Se faltar alguma informação (ex.: taxa de juros não informada, verbas eventuais não lançadas em "
            "Rendas), diga exatamente o que lançar no app para o plano ficar preciso."
        )
    elif modulo == "vazamentos":
        dados = {"perfil": perfil, "renda_e_gastos": rg, "diagnostico_cartoes": diagnostico_vazamentos(user_id)}
        tarefa = (
            "Atue como auditor de gastos pessoais. Quero achar vazamentos ocultos no orçamento mensal. "
            "Os dados vêm das faturas reais dos meus cartões (categorias, assinaturas prováveis, delivery, "
            "tarifas, pequenos gastos) e dos gastos planejados do mês.\n\n"
            "Procure vazamentos em: assinaturas esquecidas, delivery, compras por impulso, contas com "
            "cobrança excessiva, tarifas bancárias/anuidades/IOF/juros, seguros, energia/água/telecom, "
            "transporte, entretenimento e pequenos gastos recorrentes.\n\n"
            "1. **Tabela de vazamentos**: Vazamento (cite os lançamentos reais) | Gasto mensal atual | "
            "Economia potencial/mês | Dificuldade para eliminar | Impacto na qualidade de vida | Prioridade.\n"
            "2. Total de economia potencial por mês e por ano.\n"
            "3. O que NÃO cortar (gastos que parecem altos mas têm bom retorno para a família).\n"
            "4. **Plano de 30 dias** (Semana 1 a 4, com ações diárias/semanais curtas) que reduza gastos "
            "sem transformar a vida numa prisão financeira.\n"
            "Ignore parcelas de compras antigas como 'vazamento' — elas já estão contratadas."
        )
    elif modulo == "orcamento":
        renda = rg["renda_mensal"]
        dados = {"perfil": perfil, "renda_e_gastos": rg,
                 "referencia_50_30_20": {"necessidades_50": round(renda * .5, 2),
                                         "desejos_30": round(renda * .3, 2),
                                         "poupanca_20": round(renda * .2, 2)},
                 "media_mensal_cartoes_por_categoria":
                     diagnostico_vazamentos(user_id)["media_mensal_por_categoria"]}
        tarefa = (
            "Atue como coach financeiro prático. Quero um sistema de orçamento que eu consiga manter, "
            "sem vergonha, culpa ou pressão social.\n\n"
            "1. Orçamento baseado no **50/30/20 adaptado à minha vida real** (se as despesas fixas passam "
            "de 50%, mostre uma proporção de transição realista e como chegar ao ideal).\n"
            "2. **Tabela de alocação**: Categoria | % da renda | Limite mensal (R$) | Limite semanal (R$) | "
            "Observação — usando as categorias que aparecem nos meus cartões.\n"
            "3. **Revisão semanal** de 15 minutos: roteiro passo a passo (pode usar o app Nosso Dindin: "
            "painel, cartões, andamento do mês).\n"
            "4. **Regras para lazer sem culpa** (verba livre, regra das 48h, etc.) considerando meu gatilho.\n"
            "5. Formas educadas de recusar planos caros sem desconforto.\n"
            "6. **5 roteiros realistas** (falas prontas, em diálogo) para: jantar caro com amigos, viagem "
            "em grupo, aniversário/presente coletivo, compra em grupo (vaquinha/rateio) e plano de última hora."
        )
    else:
        raise ValueError("módulo inválido")

    return tarefa, dados


# ═════════════════════════════════════════════════════════════════════
#  Cadeia de IA (texto livre → markdown)
# ═════════════════════════════════════════════════════════════════════
def _post(url, payload, headers, timeout=170):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _ia_claude(prompt):
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None, None, "ANTHROPIC_API_KEY não configurada"
    model = os.environ.get("PLANEJAMENTO_CLAUDE_MODEL", "claude-sonnet-4-6")
    try:
        r = _post("https://api.anthropic.com/v1/messages",
                  {"model": model, "max_tokens": 8000, "system": SISTEMA,
                   "messages": [{"role": "user", "content": prompt}]},
                  {"x-api-key": key, "anthropic-version": "2023-06-01"})
        txt = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
        return (txt, f"Claude ({model})", None) if txt.strip() else (None, None, "Claude: resposta vazia")
    except urllib.error.HTTPError as e:
        return None, None, f"Claude — {_resumo_erro(e)}"
    except Exception as e:
        return None, None, f"Claude: {e}"


_CACHE_MODELOS = {}   # provedor → (timestamp, [modelos])


def _get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "Mozilla/5.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _versao(nome):
    m = re.search(r"(\d+(?:\.\d+)?)", nome)
    return float(m.group(1)) if m else 0.0


def _modelos_gemini(key):
    """Pergunta à API quais modelos esta chave pode usar (os nomes mudam com frequência)."""
    c = _CACHE_MODELOS.get("gemini")
    if c and time.time() - c[0] < 6 * 3600:
        return c[1]
    fixos = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-flash-latest", "gemini-flash-lite-latest"]
    try:
        r = _get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}&pageSize=200")
        nomes = [m["name"].split("/", 1)[-1] for m in r.get("models", [])
                 if "generateContent" in (m.get("supportedGenerationMethods") or [])]
        ruins = ("image", "tts", "audio", "live", "embedding", "vision", "learnlm", "gemma", "robotics",
                 "computer-use", "thinking-exp", "aqa", "veo", "imagen")
        nomes = [n for n in nomes if n.startswith("gemini") and not any(b in n for b in ruins)]

        def rank(n):
            tipo = 0 if ("flash" in n and "lite" not in n) else 1 if "lite" in n else 2 if "pro" in n else 3
            return (tipo, "preview" in n or "exp" in n, -_versao(n))
        nomes = sorted(set(nomes), key=rank) or fixos
    except Exception:
        nomes = fixos
    _CACHE_MODELOS["gemini"] = (time.time(), nomes)
    return nomes


def _modelos_groq(key):
    c = _CACHE_MODELOS.get("groq")
    if c and time.time() - c[0] < 6 * 3600:
        return c[1]
    fixos = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b"]
    try:
        r = _get("https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {key}"})
        nomes = [m["id"] for m in r.get("data", []) if m.get("active", True)]
        ruins = ("whisper", "tts", "guard", "embed", "playai", "distil", "compound", "orpheus", "safeguard")
        nomes = [n for n in nomes if not any(b in n.lower() for b in ruins)]
        pref = ["gpt-oss-120b", "kimi", "llama-4-maverick", "llama-3.3-70b", "qwen", "llama-4-scout",
                "gpt-oss-20b", "llama"]

        def rank(n):
            low = n.lower()
            return next((i for i, p in enumerate(pref) if p in low), len(pref))
        nomes = sorted(nomes, key=rank) or fixos
    except Exception:
        nomes = fixos
    _CACHE_MODELOS["groq"] = (time.time(), nomes)
    return nomes


def _resumo_erro(e):
    try:
        corpo = e.read().decode()
        msg = json.loads(corpo).get("error", {})
        msg = msg.get("message") if isinstance(msg, dict) else msg
        return f"{e.code} {str(msg or corpo)[:90]}"
    except Exception:
        return str(e.code)


def _ia_gemini(prompt):
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return None, None, "GEMINI_API_KEY não configurada"
    erros = []
    for model in _modelos_gemini(key)[:5]:
        for tentativa in range(2):
            try:
                r = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                          {"systemInstruction": {"parts": [{"text": SISTEMA}]},
                           "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                           "generationConfig": {"maxOutputTokens": 8192}}, {})
                partes = r["candidates"][0].get("content", {}).get("parts", [])
                txt = "".join(p.get("text", "") for p in partes if not p.get("thought"))
                if txt.strip():
                    return txt, f"Gemini ({model})", None
                erros.append(f"{model}: resposta vazia")
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and tentativa == 0:
                    time.sleep(20)          # limite por minuto: espera e tenta de novo
                    continue
                erros.append(f"{model}: {_resumo_erro(e)}")
                break
            except Exception as e:
                erros.append(f"{model}: {repr(e)[:60]}")
                break
    return None, None, "Gemini — " + "; ".join(erros)


def _ia_groq(prompt):
    key = os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_KEY") or ""
    if not key:
        return None, None, "GROQ_API_KEY não configurada"
    erros = []
    for model in _modelos_groq(key)[:4]:
        try:
            r = _post("https://api.groq.com/openai/v1/chat/completions",
                      {"model": model, "max_tokens": 8000,
                       "messages": [{"role": "system", "content": SISTEMA},
                                    {"role": "user", "content": prompt}]},
                      {"Authorization": f"Bearer {key}", "User-Agent": "Mozilla/5.0",
                       "Accept": "application/json"})
            txt = r["choices"][0]["message"].get("content") or ""
            if txt.strip():
                return txt, f"Groq ({model})", None
            erros.append(f"{model}: resposta vazia")
        except urllib.error.HTTPError as e:
            erros.append(f"{model}: {_resumo_erro(e)}")
        except Exception as e:
            erros.append(f"{model}: {repr(e)[:60]}")
    return None, None, "Groq — " + "; ".join(erros)


def chamar_ia(prompt):
    erros = []
    for fn in (_ia_claude, _ia_gemini, _ia_groq):
        txt, prov, err = fn(prompt)
        if txt:
            return txt, prov, None
        erros.append(err)
    return None, None, " | ".join(e for e in erros if e)


# ═════════════════════════════════════════════════════════════════════
#  Execução em segundo plano
# ═════════════════════════════════════════════════════════════════════
def iniciar_analise(app, user_id, modulo):
    from app import db
    from app.models import PlanejamentoAnalise
    tarefa, dados = montar_entrada(user_id, modulo)
    entrada = json.dumps(dados, ensure_ascii=False, default=str)
    a = PlanejamentoAnalise(user_id=user_id, modulo=modulo, status="processando", entrada=entrada)
    db.session.add(a)
    db.session.commit()
    prompt = (f"{tarefa}\n\nData de hoje: {date.today().strftime('%d/%m/%Y')}.\n\n"
              f"DADOS (JSON gerado pelo app Nosso Dindin):\n```json\n{entrada}\n```")
    aid = a.id

    def _rodar():
        with app.app_context():
            txt, prov, err = chamar_ia(prompt)
            an = db.session.get(PlanejamentoAnalise, aid)
            if not an:
                return
            if txt:
                an.status, an.resultado, an.provedor = "ok", txt, prov
            else:
                an.status, an.erro = "erro", err or "Nenhuma IA respondeu."
            an.finished_at = datetime.utcnow()
            db.session.commit()
            db.session.remove()

    threading.Thread(target=_rodar, daemon=True).start()
    return aid
