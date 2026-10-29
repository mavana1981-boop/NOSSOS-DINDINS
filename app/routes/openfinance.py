"""Tela de Open Finance: conectar bancos (Pluggy), vincular cartões e sincronizar."""
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app import pluggy
from app.models import Card, PluggyAccountLink, PluggyItem

openfinance_bp = Blueprint("openfinance", __name__)


@openfinance_bp.route("/")
@login_required
def index():
    items = PluggyItem.query.filter_by(user_id=current_user.id).order_by(PluggyItem.created_at).all()
    cards = Card.query.filter_by(user_id=current_user.id, is_active=True).order_by(Card.name).all()
    return render_template("openfinance/index.html", items=items, cards=cards,
                           configurado=pluggy.configurado())


@openfinance_bp.route("/connect-token", methods=["POST"])
@login_required
def connect_token():
    body = request.get_json(silent=True) or {}
    try:
        token = pluggy.create_connect_token(
            client_user_id=current_user.id,
            item_id=body.get("itemId"),  # informado = renovar consentimento
        )
    except pluggy.PluggyError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"accessToken": token})


@openfinance_bp.route("/item", methods=["POST"])
@login_required
def salvar_item():
    body = request.get_json(silent=True) or {}
    item_id = (body.get("itemId") or "").strip()
    if not item_id:
        return jsonify({"error": "itemId ausente"}), 400
    try:
        item, novos = pluggy.registrar_item(current_user.id, item_id)
    except pluggy.PluggyError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 502
    return jsonify({"ok": True, "banco": item.connector_name, "cartoes_novos": novos})


@openfinance_bp.route("/vincular/<int:link_id>", methods=["POST"])
@login_required
def vincular(link_id):
    link = PluggyAccountLink.query.filter_by(id=link_id, user_id=current_user.id).first_or_404()
    card_id = request.form.get("card_id", type=int)
    if card_id:
        Card.query.filter_by(id=card_id, user_id=current_user.id).first_or_404()
    link.card_id = card_id or None
    sync_from = request.form.get("sync_from", "").strip()
    if sync_from:
        from datetime import date
        link.sync_from = date.fromisoformat(sync_from)
    db.session.commit()
    flash("Vínculo salvo.", "success")
    return redirect(url_for("openfinance.index"))


@openfinance_bp.route("/sincronizar", methods=["POST"])
@login_required
def sincronizar():
    try:
        resultados = pluggy.sincronizar_usuario(current_user.id)
    except pluggy.PluggyError as e:
        flash(f"Falha ao sincronizar: {e}", "danger")
        return redirect(url_for("openfinance.index"))
    if not resultados:
        flash("Nenhuma compra nova desde a última sincronização.", "info")
        return redirect(url_for("openfinance.index"))
    total = sum(r["qtde"] for r in resultados)
    flash(f"{total} compra(s) nova(s) importada(s) para avaliação.", "success")
    if len(resultados) == 1:
        r = resultados[0]
        return redirect(url_for("cards.batch_review", card_id=r["card"].id, batch_id=r["batch_id"]))
    return redirect(url_for("openfinance.index"))


@openfinance_bp.route("/item/<int:item_pk>/remover", methods=["POST"])
@login_required
def remover_item(item_pk):
    item = PluggyItem.query.filter_by(id=item_pk, user_id=current_user.id).first_or_404()
    db.session.delete(item)
    db.session.commit()
    flash("Conexão removida do app. Para revogar o consentimento, use o app do banco ou meu.pluggy.ai.", "info")
    return redirect(url_for("openfinance.index"))
