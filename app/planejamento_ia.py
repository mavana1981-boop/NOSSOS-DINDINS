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
    """Renda mensal e despesas fixas (planejadas) do mês aberto, com overrides do perfil."""
    from app.models import Income, Expense
    from app.utils import get_user_monthly_summary, get_open_billing_month

    mes = get_open_billing_month(user_id, date.today().strftime("%Y-%m"))
    ano, mm = int(mes[:4]), int(mes[5:7])
    resumo = get_user_monthly_summary(user_id, ano, mm)

    rendas = []
    for i in Income.query.filter_by(user_id=user_id).all():
        if i.is_recurring and (i.received_at.year, i.received_at.month) <= (ano, mm):
            rendas.append({"descricao": i.description, "categoria": i.category,
                           "valor": _f(i.amount), "tipo": "recorrente"})
        elif i.received_at.year == ano and i.received_at.month == mm:
            rendas.append({"descricao": i.description, "categoria": i.category,
                           "valor": _f(i.amount), "tipo": "eventual"})

    fixas = []
    for e in Expense.query.filter_by(payer_id=user_id).all():
        if e.is_active_on(ano, mm):
            meu = next((s.share_amount for s in e.shares if s.user_id == user_id), None)
            fixas.append({"descricao": e.description, "categoria": e.category,
                          "tipo": e.kind, "valor": _f(meu if meu is not None else e.amount)})
    fixas.sort(key=lambda x: -x["valor"])

    p = _perfil(user_id)
    renda = _f(p.renda_override) if (p and p.renda_override) else _f(resumo["income"])
    despesas = _f(p.fixas_override) if (p and p.fixas_override) else _f(resumo["expense"])
    return {
        "mes_referencia": mes,
        "renda_mensal": renda,
        "despesas_planejadas_mes": despesas,
        "renda_informada_manualmente": bool(p and p.renda_override),
        "despesas_informadas_manualmente": bool(p and p.fixas_override),
        "rendas": rendas,
        "gastos_planejados": fixas[:40],
    }


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


def simular(dividas_in, extra, estrategia, inicio=None, limite_meses=600):
    """Simula mês a mês. Taxa em % a.m. O orçamento mensal total (mínimos + extra)
    é fixo; quando uma dívida acaba, o mínimo dela "rola" para a próxima."""
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

    for n in range(1, limite_meses + 1):
        vivos = [d for d in ordem if d["saldo"] > 0.005]
        if not vivos:
            break
        linha = {"mes": mes, "pagamentos": {}, "saldos": {}}
        for d in vivos:
            j = d["saldo"] * d["taxa"]
            d["saldo"] += j
            juros_tot[d["id"]] += j
        caixa = orcamento
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
    else:
        alerta = ("Com esse pagamento as dívidas não terminam em 50 anos: os juros superam "
                  "o que é pago. Aumente o extra mensal ou renegocie as taxas.")

    total_juros = round(sum(juros_tot.values()), 2)
    fim = max(quitacao.values()) if len(quitacao) == len(dividas) else None
    return {
        "estrategia": estrategia,
        "ordem": [{"id": d["id"], "nome": d["nome"], "quitada_em": quitacao.get(d["id"]),
                   "juros": round(juros_tot[d["id"]], 2)} for d in ordem],
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
        dados = {"perfil": perfil, "renda_e_gastos": {k: rg[k] for k in
                 ("renda_mensal", "despesas_planejadas_mes", "mes_referencia")},
                 "dividas": [{"nome": d["nome"], "saldo": d["saldo"], "taxa_mensal_pct": d["taxa"],
                              "pagamento_minimo": d["minimo"]} for d in base],
                 "simulacoes_calculadas": {k: _resumo_estrategia(v, base) for k, v in (res or {}).items()}}
        tarefa = (
            "Atue como especialista em eliminação de dívidas. As três estratégias JÁ FORAM SIMULADAS pelo "
            "app (dados em simulacoes_calculadas) — use exatamente esses números.\n\n"
            "1. **Tabela comparativa**: Estratégia | Ordem de pagamento | Pagamento mensal | Juros totais | "
            "Data de quitação | Vantagens | Desvantagens. (Bola de Neve = menor saldo primeiro; "
            "Avalanche = maior taxa primeiro; Híbrida = vitórias rápidas primeiro e depois maior taxa.)\n"
            "2. **Qual estratégia recomendo para a minha situação** e por quê (considere a diferença de "
            "juros entre elas e o fator motivacional).\n"
            "3. Oportunidades típicas no Brasil para reduzir a taxa: portabilidade, troca de rotativo/cheque "
            "especial por crédito pessoal ou consignado mais barato, negociação/Desenrola, quitação com "
            "desconto — só quando fizer sentido para as taxas informadas.\n"
            "4. **Plano de ação mês a mês** (tabela) para a estratégia recomendada, usando o cronograma "
            "calculado: Mês | Pagar em cada dívida | Saldo restante | Marco.\n"
            "5. Uma **tabela de acompanhamento** para eu preencher: Mês | Dívida | Pago previsto | Pago real "
            "| Saldo | Observação.\n"
            "Se houver alerta de dívida que não amortiza, comece por ele."
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
        return None, None, f"Claude {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return None, None, f"Claude: {e}"


def _ia_gemini(prompt):
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return None, None, "GEMINI_API_KEY não configurada"
    erros = []
    for model in ("gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"):
        try:
            r = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                      {"systemInstruction": {"parts": [{"text": SISTEMA}]},
                       "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                       "generationConfig": {"maxOutputTokens": 8192}}, {})
            txt = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"])
            if txt.strip():
                return txt, f"Gemini ({model})", None
        except urllib.error.HTTPError as e:
            erros.append(f"{model}:{e.code}")
            if e.code in (429, 503):
                time.sleep(2)
        except Exception as e:
            erros.append(f"{model}:{repr(e)[:60]}")
    return None, None, "Gemini: " + ", ".join(erros)


def _ia_groq(prompt):
    key = os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_KEY") or ""
    if not key:
        return None, None, "GROQ_API_KEY não configurada"
    erros = []
    for model in ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"):
        try:
            r = _post("https://api.groq.com/openai/v1/chat/completions",
                      {"model": model, "max_tokens": 8000,
                       "messages": [{"role": "system", "content": SISTEMA},
                                    {"role": "user", "content": prompt}]},
                      {"Authorization": f"Bearer {key}", "User-Agent": "Mozilla/5.0",
                       "Accept": "application/json"})
            txt = r["choices"][0]["message"]["content"]
            if txt.strip():
                return txt, f"Groq ({model})", None
        except urllib.error.HTTPError as e:
            erros.append(f"{model}:{e.code}")
        except Exception as e:
            erros.append(f"{model}:{repr(e)[:60]}")
    return None, None, "Groq: " + ", ".join(erros)


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
