from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import (Blueprint, render_template, redirect, url_for, flash, request,
                   abort, jsonify, current_app)
from flask_login import login_required, current_user

from app import db
from app.models import PlanejamentoPerfil, Divida, PlanejamentoAnalise
from app import planejamento_ia as pia

planejamento_bp = Blueprint("planejamento", __name__)


def _dec(s, default=None):
    if s is None or str(s).strip() == "":
        return default
    s = str(s).strip().replace("R$", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return default


def _int(s, default=0):
    try:
        return int(str(s).strip())
    except Exception:
        return default


def _perfil():
    p = PlanejamentoPerfil.query.filter_by(user_id=current_user.id).first()
    if not p:
        p = PlanejamentoPerfil(user_id=current_user.id)
        db.session.add(p)
        db.session.commit()
    return p


def _expirar_travadas():
    """Análises que ficaram 'processando' por mais de 10 min (ex.: deploy no meio) viram erro."""
    lim = datetime.utcnow() - timedelta(minutes=10)
    travadas = PlanejamentoAnalise.query.filter(
        PlanejamentoAnalise.user_id == current_user.id,
        PlanejamentoAnalise.status == "processando",
        PlanejamentoAnalise.created_at < lim).all()
    for a in travadas:
        a.status, a.erro = "erro", "A análise foi interrompida (o app reiniciou). Rode de novo."
    if travadas:
        db.session.commit()


@planejamento_bp.route("/")
@login_required
def index():
    _expirar_travadas()
    p = _perfil()
    rg = pia.resumo_renda_gastos(current_user.id)
    ultimas = {}
    for m in pia.MODULOS:
        ultimas[m] = PlanejamentoAnalise.query.filter_by(user_id=current_user.id, modulo=m)\
            .order_by(PlanejamentoAnalise.id.desc()).first()
    historico = PlanejamentoAnalise.query.filter_by(user_id=current_user.id)\
        .order_by(PlanejamentoAnalise.id.desc()).limit(20).all()
    n_dividas = Divida.query.filter_by(user_id=current_user.id, ativa=True).count()
    return render_template("planejamento/index.html", perfil=p, rg=rg, modulos=pia.MODULOS,
                           ultimas=ultimas, historico=historico, n_dividas=n_dividas)


@planejamento_bp.route("/perfil", methods=["POST"])
@login_required
def salvar_perfil():
    p = _perfil()
    f = request.form
    p.capital_disponivel = _dec(f.get("capital_disponivel"), Decimal("0"))
    p.horas_semana = _int(f.get("horas_semana"), 0)
    p.pessoas_domicilio = _int(f.get("pessoas_domicilio"), 1) or 1
    p.pagamento_extra = _dec(f.get("pagamento_extra"), Decimal("0"))
    p.renda_override = _dec(f.get("renda_override"))
    p.fixas_override = _dec(f.get("fixas_override"))
    p.gatilho_gasto = (f.get("gatilho_gasto") or "").strip()[:1000]
    p.contexto = (f.get("contexto") or "").strip()[:2000]
    db.session.commit()
    flash("Dados de planejamento salvos.", "success")
    return redirect(request.form.get("next") or url_for("planejamento.index"))


@planejamento_bp.route("/rodar/<modulo>", methods=["POST"])
@login_required
def rodar(modulo):
    if modulo not in pia.MODULOS:
        abort(404)
    if modulo == "dividas" and not Divida.query.filter_by(user_id=current_user.id, ativa=True).count():
        flash("Cadastre pelo menos uma dívida antes de rodar o motor.", "warning")
        return redirect(url_for("planejamento.dividas"))
    em_curso = PlanejamentoAnalise.query.filter_by(
        user_id=current_user.id, modulo=modulo, status="processando").first()
    if em_curso and em_curso.created_at > datetime.utcnow() - timedelta(minutes=10):
        return redirect(url_for("planejamento.ver", analise_id=em_curso.id))
    try:
        aid = pia.iniciar_analise(current_app._get_current_object(), current_user.id, modulo)
    except Exception as e:
        db.session.rollback()
        flash(f"Não foi possível preparar a análise: {e}", "danger")
        return redirect(url_for("planejamento.index"))
    return redirect(url_for("planejamento.ver", analise_id=aid))


def _analise_do_usuario(analise_id):
    a = db.session.get(PlanejamentoAnalise, analise_id)
    if not a or a.user_id != current_user.id:
        abort(404)
    return a


@planejamento_bp.route("/analise/<int:analise_id>")
@login_required
def ver(analise_id):
    _expirar_travadas()
    a = _analise_do_usuario(analise_id)
    html = pia.render_markdown(a.resultado) if a.status == "ok" else ""
    return render_template("planejamento/analise.html", a=a, mod=pia.MODULOS[a.modulo],
                           html_resultado=html)


@planejamento_bp.route("/analise/<int:analise_id>/status")
@login_required
def status(analise_id):
    a = _analise_do_usuario(analise_id)
    return jsonify({"status": a.status})


@planejamento_bp.route("/analise/<int:analise_id>/excluir", methods=["POST"])
@login_required
def excluir_analise(analise_id):
    a = _analise_do_usuario(analise_id)
    db.session.delete(a)
    db.session.commit()
    flash("Análise excluída.", "success")
    return redirect(url_for("planejamento.index"))


# ── Dívidas ─────────────────────────────────────────────────────────
@planejamento_bp.route("/dividas", methods=["GET", "POST"])
@login_required
def dividas():
    p = _perfil()
    if request.method == "POST":
        nome = (request.form.get("nome") or "").strip()
        saldo = _dec(request.form.get("saldo"))
        auto = request.form.get("saldo_auto") == "1"
        taxa = _dec(request.form.get("taxa_mensal"), Decimal("0"))
        minimo = _dec(request.form.get("pagamento_minimo"), Decimal("0"))
        if not nome or ((saldo is None or saldo <= 0) and not auto):
            flash("Informe nome e saldo devedor.", "warning")
        else:
            did = _int(request.form.get("divida_id"), 0)
            d = db.session.get(Divida, did) if did else None
            if d and d.user_id != current_user.id:
                abort(404)
            if not d:
                d = Divida(user_id=current_user.id)
                db.session.add(d)
            d.nome, d.taxa_mensal = nome[:120], taxa
            if d.expense_id:
                # parcela vem sempre do gasto; saldo é recalculado, a menos que você fixe um valor
                d.saldo_manual = not auto
                if not auto:
                    d.saldo = saldo
            else:
                d.saldo, d.pagamento_minimo = saldo, minimo
            db.session.commit()
            pia.sincronizar_dividas_de_gastos(current_user.id)
            flash("Dívida salva.", "success")
        return redirect(url_for("planejamento.dividas"))

    pia.sincronizar_dividas_de_gastos(current_user.id)
    lista = Divida.query.filter_by(user_id=current_user.id, ativa=True).order_by(Divida.id).all()
    candidatos = pia.candidatos_emprestimo(current_user.id)
    restantes = {d.id: pia.parcelas_restantes(d.expense) for d in lista if d.expense}
    extra_param = request.args.get("extra")
    extra = float(_dec(extra_param, p.pagamento_extra or 0) or 0)
    sims, base = pia.comparar_estrategias(current_user.id, extra=extra)
    nomes = {d["id"]: d["nome"] for d in base}
    editar = None
    if request.args.get("editar"):
        editar = db.session.get(Divida, _int(request.args.get("editar")))
        if editar and editar.user_id != current_user.id:
            editar = None
    ultima = PlanejamentoAnalise.query.filter_by(user_id=current_user.id, modulo="dividas")\
        .order_by(PlanejamentoAnalise.id.desc()).first()
    return render_template("planejamento/dividas.html", dividas=lista, sims=sims, nomes=nomes,
                           extra=extra, perfil=p, editar=editar, ultima=ultima,
                           candidatos=candidatos, restantes=restantes,
                           mes_br=pia._mes_br)


@planejamento_bp.route("/dividas/<int:divida_id>/excluir", methods=["POST"])
@login_required
def excluir_divida(divida_id):
    d = db.session.get(Divida, divida_id)
    if not d or d.user_id != current_user.id:
        abort(404)
    db.session.delete(d)
    db.session.commit()
    flash("Dívida removida.", "success")
    return redirect(url_for("planejamento.dividas"))


@planejamento_bp.route("/vazamentos/previa")
@login_required
def previa_vazamentos():
    """Mostra o que o app achou nos cartões antes de mandar para a IA."""
    diag = pia.diagnostico_vazamentos(current_user.id, completo=True)
    ultima = PlanejamentoAnalise.query.filter_by(user_id=current_user.id, modulo="vazamentos")\
        .order_by(PlanejamentoAnalise.id.desc()).first()
    return render_template("planejamento/vazamentos.html", d=diag, ultima=ultima)


@planejamento_bp.route("/dividas/importar-gastos", methods=["POST"])
@login_required
def importar_gastos():
    """Cria dívidas a partir de gastos recorrentes (empréstimos/financiamentos) do menu Gastos."""
    from app.models import Expense
    ids = [_int(x) for x in request.form.getlist("expense_id")]
    n = 0
    for eid in ids:
        e = db.session.get(Expense, eid)
        if not e or e.payer_id != current_user.id:
            continue
        if Divida.query.filter_by(user_id=current_user.id, expense_id=e.id).first():
            continue
        taxa = _dec(request.form.get(f"taxa_{eid}"), Decimal("0"))
        saldo_inf = _dec(request.form.get(f"saldo_{eid}"))
        d = Divida(user_id=current_user.id, nome=e.description[:120], expense_id=e.id,
                   taxa_mensal=taxa, saldo=saldo_inf or 0, pagamento_minimo=0,
                   saldo_manual=bool(saldo_inf) or e.recurrence_months is None)
        db.session.add(d)
        n += 1
    db.session.commit()
    pia.sincronizar_dividas_de_gastos(current_user.id)
    flash(f"{n} empréstimo(s) importado(s) de Gastos." if n else "Nenhum gasto selecionado.",
          "success" if n else "warning")
    return redirect(url_for("planejamento.dividas"))
