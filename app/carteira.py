"""
Compras por aproximação na Carteira do Google → lançamento na hora.

O celular (MacroDroid/Tasker) lê a notificação de compra da Carteira e faz um
POST em /carteira/webhook?token=... com o título e o texto. Aqui:
  1. extraímos valor, loja e final do cartão;
  2. criamos o lançamento no cartão (origem="carteira", sem pluggy_tx_id);
  3. quando a Pluggy trouxer a mesma compra, `casar_com_pluggy` só confirma o
     lançamento existente (grava o pluggy_tx_id) em vez de criar outro.
"""
import hashlib
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

MARCA_AGUARDANDO = "📱 Carteira do Google — aguardando confirmação do Open Finance"
MARCA_CONFIRMADO = "📱 Carteira do Google — ✓ confirmado pelo Open Finance"
JANELA_DIAS = 3            # diferença máxima de data entre notificação e Pluggy
JANELA_DUPLICADA_MIN = 10  # mesma notificação repetida nesse intervalo é ignorada

_RX_VALOR = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}|\d+(?:\.\d{2})?)")
_RX_VALOR_SOLTO = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})*,\d{2})(?![\d])")
_RX_FINAL = re.compile(
    r"(?:[•·*∙●.xX]{2,}\s*(\d{4})\b)"
    r"|(?:final(?:izado)?\s*(?:em\s*)?(\d{4})\b)"
    r"|(?:terminad[oa]\s*em\s*(\d{4})\b)",
    re.IGNORECASE)
_RX_LOJA = re.compile(
    r"\b(?:em|na|no|at|para)\s+(.+?)(?=\s+(?:com|usando|pelo|pela|no cart[aã]o|via)\b|[•·*∙●]|\s+R\$|[.;]\s|$)",
    re.IGNORECASE)
_RX_NAO_COMPRA = re.compile(
    r"estorn|reembols|recusad|negad|n[aã]o (?:foi )?aprovad|cancelad|devolu|cashback|"
    r"fatura|limite dispon|adicionad[oa] [àa] carteira|verifica",
    re.IGNORECASE)
_TITULOS_GENERICOS = re.compile(
    r"^(google( wallet| pay)?|carteira( do google)?|wallet|pagamento|pagamento aprovado|"
    r"compra( aprovada| realizada)?|transa[cç][aã]o( aprovada)?|caixa|cart[aã]o caixa)$",
    re.IGNORECASE)


# ── Leitura do texto da notificação ──────────────────────────────────────────

def _valor_br(bruto):
    s = bruto.strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        v = Decimal(s).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return v if v > 0 else None


def _limpar_loja(s):
    s = re.sub(r"\s+", " ", (s or "")).strip(" -–—:·•.,")
    s = _RX_VALOR.sub("", s).strip(" -–—:·•.,")
    return s[:160]


_RX_JSON_CAMPO = r'"{}"\s*:\s*"((?:[^"\\]|\\.)*)"'


def desembrulhar(titulo, texto):
    """O MacroDroid às vezes manda o modelo JSON inteiro dentro de um único campo
    (ex.: texto = '{"titulo": "A MUNDIAL", "texto": "R$ 24,94 com ...", ...}. Se preferir…').
    Nesse caso extrai o título e o texto de dentro dele."""
    titulo = (titulo or "").strip()
    texto = (texto or "").strip()
    for campo in (texto, titulo):
        if '"titulo"' not in campo and '"texto"' not in campo:
            continue
        dados = None
        ini, fim = campo.find("{"), campo.find("}", campo.find("{") + 1)
        if ini >= 0 and fim > ini:
            try:
                dados = json.loads(campo[ini:fim + 1])
            except Exception:
                dados = None
        if not isinstance(dados, dict):
            dados = {}
            for nome in ("titulo", "texto"):
                m = re.search(_RX_JSON_CAMPO.format(nome), campo)
                if m:
                    dados[nome] = m.group(1).replace('\\"', '"')
        t_in = str(dados.get("titulo") or "").strip()
        x_in = str(dados.get("texto") or "").strip()
        if t_in or x_in:
            # Variável não substituída pelo MacroDroid ("{not_title}") não vale
            t_in = "" if re.fullmatch(r"\{\w+\}", t_in) else t_in
            x_in = "" if re.fullmatch(r"\{\w+\}", x_in) else x_in
            return t_in, x_in
    return titulo, texto


def interpretar(titulo, texto):
    """Devolve dict(valor, loja, final, motivo_ignorar)."""
    titulo, texto = desembrulhar(titulo, texto)
    junto = f"{titulo}\n{texto}"

    if _RX_NAO_COMPRA.search(junto):
        return {"valor": None, "loja": None, "final": None,
                "motivo_ignorar": "não parece compra (estorno, recusa, aviso etc.)"}

    m = _RX_VALOR.search(junto) or _RX_VALOR_SOLTO.search(junto)
    valor = _valor_br(m.group(1)) if m else None

    f = _RX_FINAL.search(junto)
    final = next((g for g in f.groups() if g), None) if f else None

    loja = None
    # 1) Título com o nome da loja (formato mais comum da Carteira)
    if titulo and not _RX_VALOR.search(titulo) and not _TITULOS_GENERICOS.match(titulo):
        loja = _limpar_loja(titulo)
    # 2) "R$ 25,90 em PADARIA X" / "Compra na LOJA Y"
    if not loja:
        for parte in (texto, titulo):
            ml = _RX_LOJA.search(parte or "")
            if ml:
                cand = _limpar_loja(ml.group(1))
                if cand and not re.fullmatch(r"[\d\s•·*.]+", cand):
                    loja = cand
                    break

    return {"valor": valor, "loja": loja or LOJA_PADRAO, "final": final,
            "motivo_ignorar": None if valor else "valor não encontrado no texto"}


# ── Configuração ─────────────────────────────────────────────────────────────

def obter_config(user_id, criar=True):
    from app import db
    from app.models import CarteiraConfig
    cfg = CarteiraConfig.query.filter_by(user_id=user_id).first()
    if not cfg and criar:
        cfg = CarteiraConfig(user_id=user_id, token=secrets.token_urlsafe(24), mapa_finais="{}")
        db.session.add(cfg)
        db.session.commit()
    return cfg


def mapa(cfg):
    try:
        return {str(k): int(v) for k, v in json.loads(cfg.mapa_finais or "{}").items()}
    except Exception:
        return {}


def resolver_cartao(user_id, cfg, final):
    """Cartão do app para o final informado; None se não der para saber."""
    from app.models import Card, PluggyAccountLink
    if final:
        cid = mapa(cfg).get(final)
        if cid:
            c = Card.query.filter_by(id=cid, user_id=user_id).first()
            if c:
                return c
        c = Card.query.filter_by(user_id=user_id, last_digits=final, is_active=True).first()
        if c:
            return c
        link = PluggyAccountLink.query.filter_by(user_id=user_id, number_last4=final).first()
        if link and link.card:
            return link.card
    return cfg.cartao_padrao if cfg.cartao_padrao_id else None


# ── Lançamento ───────────────────────────────────────────────────────────────

def _fatura(user_id, card, entry_date):
    from app.utils import (ajustar_fatura_por_fechamento, get_billing_month,
                           get_open_billing_month)
    yr, mo = get_billing_month(entry_date, card.closing_day)
    calc = f"{yr}-{mo:02d}"
    aj = ajustar_fatura_por_fechamento(user_id, entry_date, calc, permitir_recuar=True)
    return aj if aj < calc else get_open_billing_month(user_id, aj)


LOJA_PADRAO = "Compra por aproximação"


def _classificar(e):
    """Mesmas regras da importação: palavra-chave aprendida e regras fixas.
    Marca como ativo se classificou. Retorna True/False."""
    from app.models import MerchantRule
    from app.utils import aplicar_regra_fixa
    e._motivo_categoria = None
    for rule in MerchantRule.query.filter_by(user_id=e.user_id).all():
        if rule.keyword and rule.keyword.lower() in (e.description or "").lower():
            e.category = rule.category
            if rule.expense_id:
                e.expense_id = rule.expense_id
                e.subcategoria_id = getattr(rule, "subcategoria_id", None)
            e._motivo_categoria = f"regra aprendida “{rule.keyword}”"
            break
    if aplicar_regra_fixa(e):
        e._motivo_categoria = "regra fixa"
    classificado = bool(e.expense_id) or bool(e.category and e.category != "A classificar")
    if classificado and e.status == "em_avaliacao":
        e.status = "ativo"
    return classificado


def nome_categoria(e):
    """'Gasto › Subcategoria' (ou a categoria livre) do lançamento."""
    cat = (e.expense.description if getattr(e, "expense", None) else e.category) or ""
    sub = getattr(e, "subcategoria", None)
    if sub is not None and getattr(sub, "nome", None):
        cat = f"{cat} › {sub.nome}"
    return cat


def avisar_compra(e, card, classificado, depois=False, confirmado=False):
    """Push da compra da Carteira. Classificada → diz onde foi categorizada;
    senão → pede para escolher. Sempre com o resumo das metas da parte do mês.
    `confirmado` = aviso disparado quando o Open Finance confirmou a compra."""
    try:
        from app.notificar import notificar
        resumo = ""
        try:
            from app.routes.dashboard import resumo_metas_partes
            resumo = resumo_metas_partes(e.user_id,
                                         primeiro=[e.expense_id] if e.expense_id else [])
        except Exception as ex:
            print(f"[carteira] resumo das metas falhou: {ex}")
        if classificado:
            motivo = getattr(e, "_motivo_categoria", None)
            corpo = f"✅ Categorizada em {nome_categoria(e)}"
            if depois:
                corpo += " (agora que o nome da loja chegou)"
            corpo += f"\nCartão {card.name} · fatura {e.billing_month}"
            if confirmado:
                corpo += "\n🏦 Confirmada pelo banco (Open Finance)"
            if motivo:
                corpo += f"\nPor {motivo}. Toque para conferir."
            link = f"/cartoes/{card.id}"
        else:
            corpo = f"Lançado em {card.name}. Toque para escolher a categoria."
            if confirmado:
                corpo += "\n🏦 Confirmada pelo banco (Open Finance)"
            link = f"/cartoes/{card.id}/lote/{e.batch_id}/revisao"
        if resumo:
            corpo += "\n\n🎯 Metas da parte do mês:\n" + resumo
        loja = (e.description or "")[:60]
        cat = nome_categoria(e) if classificado else "A classificar"
        valor = f"R$ {float(e.amount):.2f}".replace(".", ",")
        notificar(e.user_id, f"📱 {cat} · {loja} · {valor}",
                  corpo, link=link,
                  tag=f"carteira-{e.id}", email=False, urgencia="high")
    except Exception as ex:
        print(f"[carteira] aviso falhou: {ex}")


def renomear_generico(e, nova_desc):
    """Troca a descrição genérica 'Compra por aproximação' pelo nome da loja e
    tenta classificar de novo. Retorna True se mudou."""
    nova_desc = (nova_desc or "").strip()[:160]
    if not nova_desc or nova_desc == LOJA_PADRAO or (e.description or "") != LOJA_PADRAO:
        return False
    e.description = nova_desc
    if not e.expense_id and (e.category or "A classificar") == "A classificar":
        if _classificar(e):
            # Avisa depois do commit de quem chamou (ver avisar_pendentes)
            e._avisar_categoria = True
    return True


def _esta_classificado(e):
    return bool(e.expense_id) or bool(e.category and e.category != "A classificar")


def avisar_pendentes(entries):
    """Manda o aviso dos lançamentos da Carteira que mudaram depois:
    classificados só agora (nome da loja corrigido) ou confirmados pelo banco.
    Confirmação pelo Open Finance SEMPRE avisa — inclusive quando a compra já
    estava categorizada (diz onde ficou)."""
    for e in entries:
        if e is None:
            continue
        cat_nova = getattr(e, "_avisar_categoria", False)
        confirmado = getattr(e, "_avisar_confirmacao", False)
        if not (cat_nova or confirmado):
            continue
        e._avisar_categoria = False
        e._avisar_confirmacao = False
        if e.card is not None:
            avisar_compra(e, e.card, _esta_classificado(e),
                          depois=cat_nova, confirmado=confirmado)


def corrigir_lojas(user_id=None):
    """Relê notificações já lançadas sem o nome da loja e corrige os lançamentos."""
    from app import db
    from app.models import CardEntry, CarteiraNotificacao
    q = CarteiraNotificacao.query.filter(CarteiraNotificacao.loja == LOJA_PADRAO)
    if user_id:
        q = q.filter(CarteiraNotificacao.user_id == user_id)
    n, mudados = 0, []
    for reg in q.all():
        info = interpretar(reg.titulo, reg.texto)
        if not info["loja"] or info["loja"] == LOJA_PADRAO:
            continue
        reg.loja = info["loja"]
        e = CardEntry.query.get(reg.entry_id) if reg.entry_id else None
        if e and renomear_generico(e, info["loja"]):
            n += 1
            mudados.append(e)
    db.session.commit()
    avisar_pendentes(mudados)
    return n


def lancar(user_id, card, valor, loja, entry_date=None, avisar=True):
    """Cria o lançamento. Já classificado pelas regras → ativo; senão → revisão."""
    from app import db
    from app.models import CardEntry

    if entry_date is None:
        from app.agente_andamento import hoje_brasilia
        entry_date = hoje_brasilia()

    batch_id = "cg" + uuid.uuid4().hex[:6]
    e = CardEntry(
        card_id=card.id, user_id=user_id, description=loja[:160], amount=valor,
        entry_date=entry_date, kind="pontual", installments=1, installment_no=1,
        category="A classificar", status="em_avaliacao", batch_id=batch_id,
        billing_month=_fatura(user_id, card, entry_date),
        origem="carteira", notes=MARCA_AGUARDANDO,
    )
    db.session.add(e)

    classificado = _classificar(e)
    db.session.commit()

    if classificado and e.expense_id:
        try:
            from app.routes.cards import _check_excedente
            _check_excedente(e.expense_id)
        except Exception as ex:
            print(f"[carteira] excedente: {ex}")

    if avisar:
        avisar_compra(e, card, classificado)
    return e


def receber(user_id, titulo, texto, app_origem=None):
    """Processa uma notificação. Retorna o registro CarteiraNotificacao."""
    from app import db
    from app.models import CarteiraNotificacao

    cfg = obter_config(user_id)
    h = hashlib.sha256(f"{user_id}|{titulo}|{texto}".encode()).hexdigest()
    reg = CarteiraNotificacao(user_id=user_id, titulo=(titulo or "")[:300], texto=texto or "",
                              app_origem=(app_origem or "")[:120], hash=h)

    recente = CarteiraNotificacao.query.filter(
        CarteiraNotificacao.user_id == user_id, CarteiraNotificacao.hash == h,
        CarteiraNotificacao.created_at >= datetime.utcnow() - timedelta(minutes=JANELA_DUPLICADA_MIN),
    ).first()
    if recente:
        reg.resultado, reg.detalhe = "duplicada", f"repetição da notificação #{recente.id}"
        db.session.add(reg)
        db.session.commit()
        return reg

    info = interpretar(titulo, texto)
    reg.valor, reg.loja, reg.final = info["valor"], info["loja"], info["final"]
    if info["motivo_ignorar"]:
        reg.resultado, reg.detalhe = "ignorada", info["motivo_ignorar"]
        db.session.add(reg)
        db.session.commit()
        return reg

    card = resolver_cartao(user_id, cfg, info["final"])
    if not card:
        reg.resultado = "aguardando_cartao"
        reg.detalhe = (f"não sei qual cartão do app tem o final {info['final']} na Carteira"
                       if info["final"] else "a notificação não mostra o final do cartão")
        db.session.add(reg)
        db.session.commit()
        return reg

    try:
        e = lancar(user_id, card, info["valor"], info["loja"])
        reg.resultado, reg.entry_id = "lancado", e.id
        reg.detalhe = f"{card.name} · fatura {e.billing_month}" + (
            "" if e.status == "ativo" else " · aguardando categoria")
    except Exception as ex:
        db.session.rollback()
        reg.resultado, reg.detalhe = "erro", str(ex)[:300]
    db.session.add(reg)
    db.session.commit()
    return reg


# ── Confirmação pelo Open Finance ────────────────────────────────────────────

def casar_com_pluggy(card, valor, data_tx, tx_id, total_parc=1, parc_no=1, desc=None):
    """Se houver lançamento da Carteira aguardando com o mesmo valor e data
    próxima, grava nele o pluggy_tx_id e devolve-o; senão devolve None."""
    from app.models import CardEntry
    from app.utils import aplicar_regra_fixa, gerar_parcelas_futuras

    cands = CardEntry.query.filter(
        CardEntry.card_id == card.id,
        CardEntry.origem == "carteira",
        CardEntry.pluggy_tx_id.is_(None),
        CardEntry.status != "excluido",
        CardEntry.amount == valor,
        CardEntry.entry_date >= data_tx - timedelta(days=JANELA_DIAS),
        CardEntry.entry_date <= data_tx + timedelta(days=JANELA_DIAS),
    ).all()
    if not cands:
        return None
    e = min(cands, key=lambda c: (abs((c.entry_date - data_tx).days), c.id))
    e.pluggy_tx_id = tx_id
    e.notes = (e.notes or "").replace(MARCA_AGUARDANDO, MARCA_CONFIRMADO) or MARCA_CONFIRMADO
    e._avisar_confirmacao = True   # avisa mesmo se já estava categorizada
    if e.entry_date != data_tx:
        e.entry_date = data_tx
    # Notificação sem o nome da loja: usa a descrição do banco
    if desc:
        renomear_generico(e, desc)
    # A notificação não diz se foi parcelado; o banco diz.
    if total_parc > 1 and (e.installments or 1) <= 1 and parc_no == 1:
        e.kind, e.installments, e.installment_no = "parcelado", total_parc, 1
        aplicar_regra_fixa(e)
        if e.status == "ativo":
            gerar_parcelas_futuras(e, commit=False)
    return e
