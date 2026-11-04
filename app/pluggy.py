"""
Integração Open Finance via Pluggy (conector Meu Pluggy).

Credenciais SÓ por variável de ambiente (Railway > Variables):
    PLUGGY_CLIENT_ID
    PLUGGY_CLIENT_SECRET
Nunca são enviadas ao navegador.

Fluxo:
  1. Usuário conecta o banco no widget Pluggy Connect (escolhendo "MeuPluggy").
  2. O app salva o item_id e lista os cartões de crédito da conexão.
  3. Usuário liga cada cartão Pluggy a um Card do app.
  4. Todo dia (ou no botão "Sincronizar agora") as compras novas entram como
     lote "em_avaliacao" — a mesma tela de revisão usada na importação por PDF.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

PLUGGY_API = "https://api.pluggy.ai"
JOB_NAME = "pluggy_sync"


class PluggyError(Exception):
    pass


# ── Cliente HTTP ─────────────────────────────────────────────────────────────

def _request(method, path, api_key=None, body=None, params=None):
    url = f"{PLUGGY_API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["X-API-KEY"] = api_key
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode(errors="ignore")[:300]
        raise PluggyError(f"Pluggy {method} {path} → HTTP {e.code}: {detalhe}") from e
    except urllib.error.URLError as e:
        raise PluggyError(f"Pluggy inacessível: {e.reason}") from e


def configurado():
    return bool(os.environ.get("PLUGGY_CLIENT_ID") and os.environ.get("PLUGGY_CLIENT_SECRET"))


def get_api_key():
    """Troca as credenciais por uma API key temporária (lado servidor)."""
    if not configurado():
        raise PluggyError("PLUGGY_CLIENT_ID / PLUGGY_CLIENT_SECRET não configurados no Railway")
    resp = _request("POST", "/auth", body={
        "clientId": os.environ["PLUGGY_CLIENT_ID"],
        "clientSecret": os.environ["PLUGGY_CLIENT_SECRET"],
    })
    return resp["apiKey"]


# Conector MeuPluggy: o único que acessa bancos reais sem o plano comercial.
# Pode ser trocado pela variável PLUGGY_CONNECTOR_IDS no Railway (ex.: "200").
MEUPLUGGY_CONNECTOR_ID = 200


def connector_ids():
    bruto = os.environ.get("PLUGGY_CONNECTOR_IDS", str(MEUPLUGGY_CONNECTOR_ID))
    return [int(x) for x in bruto.replace(" ", "").split(",") if x.isdigit()]


def create_connect_token(client_user_id=None, item_id=None):
    api_key = get_api_key()
    options = {}
    if client_user_id:
        options["clientUserId"] = str(client_user_id)
    body = {"options": options} if options else {}
    if item_id:  # reconectar/renovar consentimento de um item existente
        body["itemId"] = item_id
    return _request("POST", "/connect_token", api_key=api_key, body=body)["accessToken"]


def get_item(api_key, item_id):
    return _request("GET", f"/items/{item_id}", api_key=api_key)


def list_credit_accounts(api_key, item_id):
    resp = _request("GET", "/accounts", api_key=api_key,
                    params={"itemId": item_id, "type": "CREDIT"})
    return resp.get("results", [])


def list_transactions(api_key, account_id, date_from, date_to):
    """Todas as transações da conta no período.

    Usa GET /v2/transactions (paginação por cursor); o GET /transactions antigo
    foi desativado pela Pluggy (HTTP 410).
    """
    params = {
        "accountId": account_id,
        "dateFrom": date_from.isoformat()[:10],
        "dateTo": date_to.isoformat()[:10],
    }
    todas, vistos = [], set()
    while True:
        resp = _request("GET", "/v2/transactions", api_key=api_key, params=params)
        todas.extend(resp.get("results") or [])
        prox = resp.get("next")
        if not prox:
            return todas
        # "next" é uma query string pronta; pegamos só o cursor "after"
        after = urllib.parse.parse_qs(str(prox).lstrip("?")).get("after", [None])[0]
        if not after or after in vistos:  # proteção contra laço infinito
            return todas
        vistos.add(after)
        params = {**params, "after": after}


# ── Cadastro de conexões ─────────────────────────────────────────────────────

def registrar_item(user_id, item_id):
    """Salva a conexão recém-criada no widget e cria os vínculos de cartão."""
    from app import db
    from app.models import PluggyItem, PluggyAccountLink

    api_key = get_api_key()
    info = get_item(api_key, item_id)
    item = PluggyItem.query.filter_by(item_id=item_id).first()
    if item and item.user_id != user_id:
        raise PluggyError("Esta conexão pertence a outro usuário")
    if not item:
        item = PluggyItem(user_id=user_id, item_id=item_id)
        db.session.add(item)
    item.connector_name = (info.get("connector") or {}).get("name") or item.connector_name
    item.status = info.get("status")
    db.session.flush()

    novos = 0
    for acc in list_credit_accounts(api_key, item_id):
        link = PluggyAccountLink.query.filter_by(account_id=acc["id"]).first()
        if link:
            continue
        numero = str(acc.get("number") or "")
        db.session.add(PluggyAccountLink(
            user_id=user_id,
            pluggy_item_id=item.id,
            account_id=acc["id"],
            account_name=(acc.get("marketingName") or acc.get("name") or "Cartão")[:160],
            number_last4=numero[-4:] if numero else None,
            sync_from=date.today(),
        ))
        novos += 1
    db.session.commit()
    return item, novos


# ── Sincronização ────────────────────────────────────────────────────────────

def _motivo_ignorar(tx):
    """None se for compra a importar; senão, o motivo de ignorar."""
    if (tx.get("status") or "POSTED").upper() != "POSTED":
        return "pendente"  # pendentes podem mudar de ID — só importa lançadas
    valor = float(tx.get("amount") or 0)
    tipo = (tx.get("type") or "").upper()
    # Em conta de cartão na Pluggy: valor positivo = compra; negativo = pagamento/estorno.
    if valor > 0 or (valor < 0 and tipo == "DEBIT" and "PAGAMENTO" not in
                     (tx.get("description") or "").upper()):
        return None
    return "pagamento/estorno"


def _eh_compra(tx):
    return _motivo_ignorar(tx) is None


def _data(s):
    if not s:
        return None
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).date()


def sincronizar_link(api_key, link, diag=None):
    """Importa compras novas de um cartão vinculado. Retorna (batch_id, qtde).

    Se `diag` (dict) for passado, é preenchido com as contagens do que veio da Pluggy.
    """
    if diag is None:
        diag = {}
    from app import db
    from app.models import CardEntry
    from app.routes.cards import finalizar_lote
    from app.utils import get_billing_month, get_open_billing_month

    card = link.card
    if not card:
        return None, 0

    ate = date.today()
    desde = link.sync_from or (ate - timedelta(days=30))
    txs = list_transactions(api_key, link.account_id, desde, ate)
    diag.update(desde=desde, recebidas=len(txs), importadas=0, ja_importadas=0,
                pendentes=0, pagamentos=0, ja_lancadas=0)

    ids_existentes = {
        r[0] for r in db.session.query(CardEntry.pluggy_tx_id)
        .filter(CardEntry.card_id == card.id, CardEntry.pluggy_tx_id.isnot(None)).all()
    }
    # Dedup "suave" contra lançamentos manuais/PDF: (descrição, valor, data)
    existentes_suaves = {
        (e.description[:40].upper().strip(), str(round(float(e.amount), 2)), e.entry_date)
        for e in CardEntry.query.filter(
            CardEntry.card_id == card.id,
            CardEntry.entry_date >= desde,
            CardEntry.status != "excluido",
        ).all()
    }

    batch_id = "of" + uuid.uuid4().hex[:6]
    qtde = 0
    for tx in txs:
        if tx["id"] in ids_existentes:
            diag["ja_importadas"] += 1
            continue
        motivo = _motivo_ignorar(tx)
        if motivo == "pendente":
            diag["pendentes"] += 1
            continue
        if motivo:
            diag["pagamentos"] += 1
            continue
        meta = tx.get("creditCardMetadata") or {}
        entry_date = _data(tx.get("date")) or ate
        valor = Decimal(str(abs(float(tx.get("amount") or 0)))).quantize(Decimal("0.01"))
        if valor <= 0:
            continue
        desc = (tx.get("description") or tx.get("descriptionRaw") or "Sem descrição").strip()[:160]
        if (desc[:40].upper(), str(round(float(valor), 2)), entry_date) in existentes_suaves:
            diag["ja_lancadas"] += 1
            continue

        total_parc = int(meta.get("totalInstallments") or 1)
        parc_no = int(meta.get("installmentNumber") or 1)
        yr, mo = get_billing_month(entry_date, card.closing_day)
        billing_month = get_open_billing_month(link.user_id, f"{yr}-{mo:02d}")

        db.session.add(CardEntry(
            card_id=card.id,
            user_id=link.user_id,
            description=desc,
            amount=valor,
            entry_date=entry_date,
            kind="parcelado" if total_parc > 1 else "pontual",
            installments=total_parc,
            installment_no=parc_no,
            category="A classificar",
            status="em_avaliacao",
            batch_id=batch_id,
            billing_month=billing_month,
            pluggy_tx_id=tx["id"],
            notes="Importado via Open Finance",
        ))
        ids_existentes.add(tx["id"])
        qtde += 1

    diag["importadas"] = qtde
    if qtde:
        db.session.flush()
        finalizar_lote(card.id, batch_id, link.user_id)  # parcelas planejadas + regras
    db.session.commit()
    return (batch_id if qtde else None), qtde


def _atualizar_cartoes(api_key, item):
    """Cria vínculos para cartões que apareceram no item depois do cadastro."""
    from app import db
    from app.models import PluggyAccountLink
    for acc in list_credit_accounts(api_key, item.item_id):
        if PluggyAccountLink.query.filter_by(account_id=acc["id"]).first():
            continue
        numero = str(acc.get("number") or "")
        db.session.add(PluggyAccountLink(
            user_id=item.user_id,
            pluggy_item_id=item.id,
            account_id=acc["id"],
            account_name=(acc.get("marketingName") or acc.get("name") or "Cartão")[:160],
            number_last4=numero[-4:] if numero else None,
            sync_from=date.today() - timedelta(days=30),
        ))
    db.session.flush()


def sincronizar_usuario(user_id, relatorio=None):
    """Sincroniza todos os cartões vinculados do usuário. Retorna lista de resultados.

    Se `relatorio` (lista) for passado, recebe uma linha por cartão/erro para mostrar na tela.
    """
    if relatorio is None:
        relatorio = []
    from app import db
    from app.models import PluggyItem, Notificacao

    api_key = get_api_key()
    resultados = []
    for item in PluggyItem.query.filter_by(user_id=user_id).all():
        try:
            info = get_item(api_key, item.item_id)
            item.status = info.get("status")
            _atualizar_cartoes(api_key, item)
            db.session.refresh(item)
            for link in item.accounts:
                nome = f"{link.account_name}" + (f" ••{link.number_last4}" if link.number_last4 else "")
                if not link.card_id:
                    relatorio.append({"tipo": "aviso", "texto":
                        f"{nome}: não está vinculado a nenhum cartão do app — vincule na tela de Open Finance."})
                    continue
                diag = {}
                batch_id, qtde = sincronizar_link(api_key, link, diag)
                if diag:
                    relatorio.append({"tipo": "info", "texto": (
                        f"{nome} → {link.card.name}: desde {diag['desde']:%d/%m/%Y}, "
                        f"a Pluggy devolveu {diag['recebidas']} transação(ões); "
                        f"{diag['importadas']} importada(s), {diag['ja_importadas']} já importada(s), "
                        f"{diag['ja_lancadas']} já lançada(s) à mão/PDF, {diag['pendentes']} pendente(s), "
                        f"{diag['pagamentos']} pagamento(s)/estorno(s).")})
                if qtde:
                    resultados.append({"card": link.card, "batch_id": batch_id, "qtde": qtde})
                    db.session.add(Notificacao(
                        user_id=user_id,
                        titulo=f"💳 {qtde} compra(s) nova(s) em {link.card.name}",
                        mensagem="Chegaram pelo Open Finance. Revise e aprove os lançamentos.",
                        link=f"/cartoes/{link.card_id}/lote/{batch_id}/revisao",
                    ))
                    db.session.commit()
            item.last_sync_at = datetime.utcnow()
            item.last_error = None
        except PluggyError as e:
            db.session.rollback()
            item.last_error = str(e)[:500]
            relatorio.append({"tipo": "erro", "texto": f"{item.connector_name or 'Conexão'}: {e}"})
            print(f"[pluggy] item {item.item_id}: {e}")
        db.session.commit()
    return resultados


def rodar_sincronizacao_diaria(app):
    """Job do scheduler: uma execução por dia, mesmo com vários workers."""
    from sqlalchemy.exc import IntegrityError
    from app import db
    from app.models import AgenteExecucao, PluggyItem

    with app.app_context():
        if not configurado():
            return
        try:
            db.session.add(AgenteExecucao(job=JOB_NAME, run_date=date.today()))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return
        user_ids = {i.user_id for i in PluggyItem.query.all()}
        for uid in user_ids:
            try:
                r = sincronizar_usuario(uid)
                print(f"[pluggy] usuário {uid}: {sum(x['qtde'] for x in r)} compra(s) importada(s)")
            except Exception as e:
                db.session.rollback()
                print(f"[pluggy] falha usuário {uid}: {e}")
