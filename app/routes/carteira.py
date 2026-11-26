"""Carteira do Google: webhook que recebe as notificações do celular e tela de configuração."""
import json
import re
import secrets

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app import carteira
from app.models import Card, CarteiraConfig, CarteiraNotificacao

carteira_bp = Blueprint("carteira", __name__)


def _campo(dados, *nomes):
    for n in nomes:
        v = dados.get(n)
        if v not in (None, ""):
            return str(v)
    return ""


@carteira_bp.route("/webhook", methods=["POST"])
def webhook():
    """Chamado pelo MacroDroid/Tasker. Sem login: autentica pelo token."""
    token = (request.headers.get("X-Token") or request.args.get("token") or "").strip()
    cfg = CarteiraConfig.query.filter_by(token=token).first() if token else None
    if not cfg:
        return jsonify({"ok": False, "erro": "token inválido"}), 401

    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        dados = request.form.to_dict() or {}
        if not dados and request.data:
            # MacroDroid manda JSON quebrado quando o texto tem aspas: recupera os campos
            # pelo formato do modelo; em último caso, usa o corpo cru como texto.
            bruto = request.get_data(as_text=True)
            m = re.search(r'"titulo"\s*:\s*"(.*)"\s*,\s*"texto"\s*:\s*"(.*?)"\s*'
                          r'(?:,\s*"app"\s*:\s*"(.*?)"\s*)?}\s*$', bruto, re.S)
            dados = ({"titulo": m.group(1), "texto": m.group(2), "app": m.group(3) or ""}
                     if m else {"texto": bruto})
    titulo = _campo(dados, "titulo", "title", "not_title")
    texto = _campo(dados, "texto", "text", "not_text", "body")
    app_origem = _campo(dados, "app", "app_name", "not_app_name")
    if not (titulo or texto):
        return jsonify({"ok": False, "erro": "envie 'titulo' e/ou 'texto'"}), 400

    reg = carteira.receber(cfg.user_id, titulo[:300], texto[:2000], app_origem)
    return jsonify({"ok": True, "resultado": reg.resultado, "detalhe": reg.detalhe,
                    "valor": str(reg.valor) if reg.valor is not None else None,
                    "loja": reg.loja, "final": reg.final, "lancamento": reg.entry_id})


@carteira_bp.route("/")
@login_required
def index():
    cfg = carteira.obter_config(current_user.id)
    cards = Card.query.filter_by(user_id=current_user.id, is_active=True).order_by(Card.name).all()
    recentes = (CarteiraNotificacao.query.filter_by(user_id=current_user.id)
                .order_by(CarteiraNotificacao.id.desc()).limit(30).all())
    url_webhook = url_for("carteira.webhook", token=cfg.token, _external=True, _scheme="https")
    finais_por_cartao = {}
    for final, c_id in sorted(carteira.mapa(cfg).items()):
        finais_por_cartao[c_id] = ", ".join(filter(None, [finais_por_cartao.get(c_id), final]))
    return render_template("carteira/index.html", cfg=cfg, cards=cards, recentes=recentes,
                           finais_por_cartao=finais_por_cartao, url_webhook=url_webhook)


@carteira_bp.route("/config", methods=["POST"])
@login_required
def salvar_config():
    cfg = carteira.obter_config(current_user.id)
    meus = {c.id for c in Card.query.filter_by(user_id=current_user.id).all()}
    padrao = request.form.get("cartao_padrao_id", type=int)
    cfg.cartao_padrao_id = padrao if padrao in meus else None
    novo = {}
    for c_id in meus:
        for final in (request.form.get(f"finais_{c_id}") or "").replace(";", ",").split(","):
            final = final.strip()
            if len(final) == 4 and final.isdigit():
                novo[final] = c_id
    cfg.mapa_finais = json.dumps(novo)
    db.session.commit()
    flash("Configuração da Carteira salva.", "success")
    return redirect(url_for("carteira.index"))


@carteira_bp.route("/novo-token", methods=["POST"])
@login_required
def novo_token():
    cfg = carteira.obter_config(current_user.id)
    cfg.token = secrets.token_urlsafe(24)
    db.session.commit()
    flash("Novo endereço gerado. Atualize a URL no MacroDroid — a antiga parou de funcionar.", "warning")
    return redirect(url_for("carteira.index"))


@carteira_bp.route("/testar", methods=["POST"])
@login_required
def testar():
    """Mostra como o app leria uma notificação, sem lançar nada."""
    titulo = request.form.get("titulo", "")
    texto = request.form.get("texto", "")
    info = carteira.interpretar(titulo, texto)
    cfg = carteira.obter_config(current_user.id)
    card = carteira.resolver_cartao(current_user.id, cfg, info["final"])
    if info["motivo_ignorar"]:
        flash(f"Seria ignorada: {info['motivo_ignorar']}.", "warning")
    else:
        flash(f"Leitura: R$ {info['valor']} · loja \"{info['loja']}\" · final {info['final'] or '—'} "
              f"→ cartão {card.name if card else '(nenhum — configure o final ou o cartão padrão)'}.",
              "info")
    return redirect(url_for("carteira.index"))


@carteira_bp.route("/reprocessar/<int:reg_id>", methods=["POST"])
@login_required
def reprocessar(reg_id):
    """Lança uma notificação que ficou aguardando cartão (depois de configurar o final)."""
    reg = CarteiraNotificacao.query.filter_by(id=reg_id, user_id=current_user.id).first_or_404()
    if reg.resultado != "aguardando_cartao" or not reg.valor:
        flash("Essa notificação não está aguardando cartão.", "info")
        return redirect(url_for("carteira.index"))
    cfg = carteira.obter_config(current_user.id)
    card = carteira.resolver_cartao(current_user.id, cfg, reg.final)
    if not card:
        flash("Ainda não sei o cartão: informe o final na tabela ou escolha um cartão padrão.", "warning")
        return redirect(url_for("carteira.index"))
    data = reg.created_at
    from datetime import timezone, timedelta
    data_local = (data.replace(tzinfo=timezone.utc) - timedelta(hours=3)).date() if data else None
    e = carteira.lancar(current_user.id, card, reg.valor, reg.loja, entry_date=data_local, avisar=False)
    reg.resultado, reg.entry_id = "lancado", e.id
    reg.detalhe = f"{card.name} · fatura {e.billing_month} (lançada depois)"
    db.session.commit()
    if e.status == "ativo":
        flash(f"Lançado em {card.name} · categorizado em {carteira.nome_categoria(e)}.", "success")
        return redirect(url_for("carteira.index"))
    return redirect(url_for("cards.batch_review", card_id=card.id, batch_id=e.batch_id))
