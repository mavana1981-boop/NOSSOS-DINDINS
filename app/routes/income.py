from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from app import db
from app.models import Income

income_bp = Blueprint("income", __name__)


def _parse_decimal(s):
    if not s:
        return None
    try:
        return Decimal(str(s).replace(".", "").replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None


@income_bp.route("/")
@login_required
def list_incomes():
    """
    Rendas do mês escolhido (mesma regra do Fluxo de Caixa):
      - fixas (recorrentes) que começaram até o mês;
      - eventuais só do próprio mês.
    """
    from app.utils import get_open_billing_month
    mes = request.args.get("mes") or get_open_billing_month(
        current_user.id, date.today().strftime("%Y-%m"))
    try:
        ano, mm = int(mes[:4]), int(mes[5:7])
    except (ValueError, IndexError):
        ano, mm = date.today().year, date.today().month
    mes = f"{ano}-{mm:02d}"

    todas = Income.query.filter_by(user_id=current_user.id)\
        .order_by(Income.received_at.desc()).all()
    fixas = [i for i in todas if i.is_recurring
             and (i.received_at.year, i.received_at.month) <= (ano, mm)]
    eventuais = [i for i in todas if not i.is_recurring
                 and i.received_at.year == ano and i.received_at.month == mm]
    from app.utils import salarios_mes_mapa, valor_renda_no_mes
    _sal = salarios_mes_mapa(current_user.id)
    for i in fixas:     # salário calculado do mês substitui o valor cadastrado
        i.valor_mes = valor_renda_no_mes(i, ano, mm, _sal)
        i.salario_calculado = (ano, mm, i.id) in _sal
    for i in eventuais:
        i.valor_mes = float(i.amount)
        i.salario_calculado = False
    total_fixa = sum(i.valor_mes for i in fixas)
    total_eventual = sum(float(i.amount) for i in eventuais)

    MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
             "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
    prev_mes = f"{ano-1}-12" if mm == 1 else f"{ano}-{mm-1:02d}"
    next_mes = f"{ano+1}-01" if mm == 12 else f"{ano}-{mm+1:02d}"
    return render_template("income/list.html",
                           incomes=fixas + eventuais,
                           fixas=fixas, eventuais=eventuais,
                           total_fixa=total_fixa, total_eventual=total_eventual,
                           total=total_fixa + total_eventual,
                           mes=mes, mes_label=f"{MESES[mm-1]}/{ano}",
                           prev_mes=prev_mes, next_mes=next_mes)


@income_bp.route("/<int:income_id>/toggle-recorrente", methods=["POST"])
@login_required
def toggle_recorrente(income_id):
    from app.models import Income
    inc = Income.query.get_or_404(income_id)
    if inc.user_id != current_user.id:
        abort(403)
    inc.is_recurring = not inc.is_recurring
    db.session.commit()
    from flask import jsonify
    return jsonify({"ok": True, "is_recurring": inc.is_recurring})


@income_bp.route("/novo", methods=["GET", "POST"])
@login_required
def new_income():
    if request.method == "POST":
        desc = request.form.get("description", "").strip()
        amount = _parse_decimal(request.form.get("amount"))
        cat = request.form.get("category", "Salário").strip() or "Salário"
        rec = request.form.get("is_recurring", "0") == "1"
        notes = request.form.get("notes", "").strip()
        d_str = request.form.get("received_at")

        if not desc or not amount or amount <= 0:
            flash("Descrição e valor são obrigatórios.", "danger")
            return render_template("income/form.html", income=None)

        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date() if d_str else date.today()
        except ValueError:
            d = date.today()

        i = Income(user_id=current_user.id, description=desc, amount=amount,
                   received_at=d, category=cat, is_recurring=rec, notes=notes)
        db.session.add(i)
        db.session.commit()
        flash("Renda registrada.", "success")
        return redirect(url_for("income.list_incomes"))

    return render_template("income/form.html", income=None)


@income_bp.route("/<int:income_id>/editar", methods=["GET", "POST"])
@login_required
def edit_income(income_id):
    i = Income.query.get_or_404(income_id)
    if i.user_id != current_user.id:
        abort(403)
    if request.method == "POST":
        i.description = request.form.get("description", i.description).strip()
        amount = _parse_decimal(request.form.get("amount"))
        if amount and amount > 0:
            i.amount = amount
        i.category = request.form.get("category", i.category).strip() or "Salário"
        i.is_recurring = request.form.get("is_recurring", "0") == "1"
        i.notes = request.form.get("notes", "").strip()
        d_str = request.form.get("received_at")
        if d_str:
            try:
                i.received_at = datetime.strptime(d_str, "%Y-%m-%d").date()
            except ValueError:
                pass
        db.session.commit()
        flash("Renda atualizada.", "success")
        return redirect(url_for("income.list_incomes"))
    return render_template("income/form.html", income=i)


@income_bp.route("/<int:income_id>/excluir", methods=["POST"])
@login_required
def delete_income(income_id):
    i = Income.query.get_or_404(income_id)
    if i.user_id != current_user.id:
        abort(403)
    db.session.delete(i)
    db.session.commit()
    flash("Renda removida.", "info")
    return redirect(url_for("income.list_incomes"))
 

# ═════════════════════════════════════════════════════════════════════
#  Calcular salário do mês: valor recebido + consignados do mês
#  → substitui a renda fixa escolhida SÓ naquele mês
# ═════════════════════════════════════════════════════════════════════
MESES_PT = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
            "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


def _parse_valor(raw):
    """Aceita 8.500,00 / 8500,00 / 8500.00 / 8500."""
    import re
    t = re.sub(r"[^\d,.\-]", "", str(raw or ""))
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") == 1 and len(t.split(".")[1]) <= 2:
        pass                      # ponto decimal
    else:
        t = t.replace(".", "")    # pontos de milhar
    try:
        return Decimal(t).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _mes_param(raw):
    from app.utils import get_open_billing_month
    mes = raw or get_open_billing_month(current_user.id, date.today().strftime("%Y-%m"))
    try:
        ano, mm = int(mes[:4]), int(mes[5:7])
        if not 1 <= mm <= 12:
            raise ValueError
    except (ValueError, IndexError, TypeError):
        ano, mm = date.today().year, date.today().month
    return ano, mm


@income_bp.route("/salario-do-mes", methods=["GET", "POST"])
@login_required
def salario_mes():
    import json
    from app.models import SalarioMes, CashflowOverride
    from app.utils import consignados_do_mes, format_brl

    ano, mm = _mes_param(request.values.get("mes"))
    mes = f"{ano}-{mm:02d}"

    fixas = [i for i in Income.query.filter_by(user_id=current_user.id, is_recurring=True)
             .order_by(Income.amount.desc()).all()
             if (i.received_at.year, i.received_at.month) <= (ano, mm)]

    if request.method == "POST":
        income_id = request.form.get("income_id", type=int)
        renda = next((i for i in fixas if i.id == income_id), None)
        valor = _parse_valor(request.form.get("valor_informado"))
        if not renda:
            flash("Escolha a renda fixa que será substituída.", "danger")
            return redirect(url_for("income.salario_mes", mes=mes))
        if valor is None or valor <= 0:
            flash("Informe o valor do salário que caiu na conta.", "danger")
            return redirect(url_for("income.salario_mes", mes=mes, income_id=income_id))

        marcados = {int(x) for x in request.form.getlist("expense_id") if x.isdigit()}
        itens = [c for c in consignados_do_mes(current_user.id, ano, mm, marcados)
                 if c["expense"].id in marcados]
        detalhe = [{"expense_id": c["expense"].id, "descricao": c["expense"].description,
                    "parcela": c["parcela"], "valor": c["valor"]} for c in itens]
        total_cons = Decimal(str(round(sum(c["valor"] for c in itens), 2)))
        entrada = valor + total_cons

        reg = SalarioMes.query.filter_by(user_id=current_user.id, mes_ref=mes,
                                         income_id=renda.id).first()
        if reg is None:
            reg = SalarioMes(user_id=current_user.id, mes_ref=mes, income_id=renda.id)
            db.session.add(reg)
        reg.valor_informado = valor
        reg.consignados_total = total_cons
        reg.valor_entrada = entrada
        reg.detalhe = json.dumps(detalhe, ensure_ascii=False)

        # Ajuste manual antigo da coluna "renda fixa" no Fluxo esconderia o salário calculado
        ov = CashflowOverride.query.filter_by(user_id=current_user.id, year=ano, month=mm).first()
        limpou_ov = False
        if ov is not None and ov.income_recurring_override is not None:
            ov.income_recurring_override = None
            limpou_ov = True
        db.session.commit()

        msg = (f"Salário de {MESES_PT[mm-1]}/{ano}: {format_brl(valor)} + consignados "
               f"{format_brl(total_cons)} = {format_brl(entrada)}. "
               f"Substitui \"{renda.description}\" ({format_brl(renda.amount)}) só neste mês.")
        if limpou_ov:
            msg += " O ajuste manual da renda fixa no Fluxo deste mês foi removido."
        flash(msg, "success")
        return redirect(url_for("income.salario_mes", mes=mes, income_id=renda.id))

    # GET
    calculados = {s.income_id: s for s in
                  SalarioMes.query.filter_by(user_id=current_user.id, mes_ref=mes).all()}
    income_id = request.args.get("income_id", type=int)
    renda = next((i for i in fixas if i.id == income_id), None)
    if renda is None:
        renda = next((i for i in fixas if i.id in calculados), None) \
            or next((i for i in fixas if (i.category or "").lower().startswith("sal")), None) \
            or (fixas[0] if fixas else None)
    atual = calculados.get(renda.id) if renda else None

    # Pré-marca: os do cálculo deste mês; senão, os do último cálculo dessa renda
    marcar = None
    if atual:
        marcar = [d.get("expense_id") for d in atual.itens()]
    elif renda:
        ultimo = SalarioMes.query.filter(SalarioMes.user_id == current_user.id,
                                         SalarioMes.income_id == renda.id,
                                         SalarioMes.mes_ref < mes)\
            .order_by(SalarioMes.mes_ref.desc()).first()
        if ultimo:
            marcar = [d.get("expense_id") for d in ultimo.itens()]
    candidatos = consignados_do_mes(current_user.id, ano, mm, marcar)
    if atual:   # no mês já calculado, marca exatamente o que foi usado
        usados = set(marcar or [])
        for c in candidatos:
            c["consignado"] = c["expense"].id in usados

    historico = SalarioMes.query.filter_by(user_id=current_user.id)\
        .order_by(SalarioMes.mes_ref.desc()).limit(12).all()
    ov = CashflowOverride.query.filter_by(user_id=current_user.id, year=ano, month=mm).first()

    prev_mes = f"{ano-1}-12" if mm == 1 else f"{ano}-{mm-1:02d}"
    next_mes = f"{ano+1}-01" if mm == 12 else f"{ano}-{mm+1:02d}"
    return render_template("income/salario_mes.html",
                           mes=mes, mes_label=f"{MESES_PT[mm-1]}/{ano}",
                           prev_mes=prev_mes, next_mes=next_mes,
                           fixas=fixas, renda=renda, atual=atual, calculados=calculados,
                           candidatos=candidatos, historico=historico,
                           tem_override=bool(ov and ov.income_recurring_override is not None),
                           meses_pt=MESES_PT)


@income_bp.route("/salario-do-mes/<int:sal_id>/remover", methods=["POST"])
@login_required
def salario_mes_remover(sal_id):
    from app.models import SalarioMes
    s = SalarioMes.query.get_or_404(sal_id)
    if s.user_id != current_user.id:
        abort(403)
    mes = s.mes_ref
    db.session.delete(s)
    db.session.commit()
    flash("Salário calculado removido — o mês volta a usar a renda cadastrada.", "info")
    return redirect(url_for("income.salario_mes", mes=mes))
