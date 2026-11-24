"""
Painel de Consignados / Empréstimos (menu Planejamento → Consignados).

Junta os empréstimos lançados no menu Gastos (recorrentes com prazo ou com cara
de empréstimo), usa a taxa de juros guardada na Dívida ligada ao gasto e monta o
cronograma das parcelas que faltam. A simulação de liquidação/antecipação mês a mês
roda no navegador (templates/planejamento/consignados.html) com estes dados.
"""
import json
from datetime import date

from app.planejamento_ia import (KW_EMPRESTIMO, _norm, _tem, _parcela_do_usuario,
                                 saldo_estimado, aportes_eventuais, _f)

KW_CONSIGNADO = ["consign"]


def _ym(y, m):
    return f"{y:04d}-{m:02d}"


def _add(y, m, n):
    t = y * 12 + (m - 1) + n
    return t // 12, t % 12 + 1


def _ids_salario_mes(user_id):
    """Gastos já usados como consignado no 'Calcular Salário do Mês'."""
    from app.models import SalarioMes
    ids = set()
    try:
        for s in SalarioMes.query.filter_by(user_id=user_id).all():
            for it in s.itens():
                if it.get("expense_id"):
                    ids.add(int(it["expense_id"]))
    except Exception:
        pass
    return ids


def _cronograma(e, y0, m0):
    """Parcelas que vencem do mês que vem até o fim do empréstimo."""
    fy, fm = _add(e.spent_at.year, e.spent_at.month, e.recurrence_months - 1)
    futuras, k = [], 1
    while True:
        y, m = _add(y0, m0, k)
        if (y, m) > (fy, fm):
            break
        if e.is_active_on(y, m):
            num = (y - e.spent_at.year) * 12 + (m - e.spent_at.month) + 1
            futuras.append({"mes": _ym(y, m), "num": num, "d": k})
        k += 1
    return futuras, _ym(fy, fm)


def emprestimos(user_id, hoje=None):
    """Lista de empréstimos do usuário (consignados primeiro)."""
    from app.models import Expense, Divida
    hoje = hoje or date.today()
    y0, m0 = hoje.year, hoje.month
    salario_ids = _ids_salario_mes(user_id)
    dividas = {d.expense_id: d for d in Divida.query.filter(
        Divida.user_id == user_id, Divida.expense_id != None).all()}  # noqa: E711

    out = []
    for e in Expense.query.filter_by(payer_id=user_id, kind="recorrente").all():
        nome_n = _norm(f"{e.description} {e.category or ''}")
        consig = _tem(nome_n, KW_CONSIGNADO) or e.id in salario_ids
        if not (consig or _tem(nome_n, KW_EMPRESTIMO) or e.id in dividas):
            continue                       # recorrente comum (luz, escola…)
        parcela = _parcela_do_usuario(e, user_id)
        d = dividas.get(e.id)
        item = {
            "id": e.id, "nome": e.description, "categoria": e.category or "",
            "consignado": bool(consig), "parcela": parcela,
            "taxa": _f(d.taxa_mensal) if d else 0.0,
            "sem_prazo": e.recurrence_months is None, "total": e.recurrence_months,
            "inicio": _ym(e.spent_at.year, e.spent_at.month),
            "pagas": None, "atual": None, "futuras": [], "fim": None,
            "saldo_hoje": None, "nominal_restante": None,
        }
        if e.recurrence_months:
            diff = (y0 - e.spent_at.year) * 12 + (m0 - e.spent_at.month)
            if diff >= e.recurrence_months:
                continue                   # já terminou
            futuras, fim = _cronograma(e, y0, m0)
            item.update({
                "pagas": max(diff, 0),                     # até o mês passado
                "atual": diff + 1 if diff >= 0 else None,  # parcela deste mês
                "futuras": futuras, "fim": fim,
                "saldo_hoje": saldo_estimado(parcela, item["taxa"], len(futuras)),
                "nominal_restante": round(parcela * len(futuras), 2),
            })
        out.append(item)
    out.sort(key=lambda x: (not x["consignado"], x["sem_prazo"], -(x["nominal_restante"] or 0)))
    return out


def dados_painel(user_id, hoje=None):
    hoje = hoje or date.today()
    lst = emprestimos(user_id, hoje)
    com_prazo = [x for x in lst if not x["sem_prazo"]]
    try:
        eventuais = {k: round(v, 2) for k, v in sorted(aportes_eventuais(user_id, 100).items())}
    except Exception:
        eventuais = {}
    no_mes = [x for x in lst if x["atual"] or x["sem_prazo"]]
    kpi = {
        "mensal": round(sum(x["parcela"] for x in no_mes), 2),
        "mensal_consig": round(sum(x["parcela"] for x in no_mes if x["consignado"]), 2),
        "nominal": round(sum(x["nominal_restante"] for x in com_prazo), 2),
        "saldo": round(sum(x["saldo_hoje"] for x in com_prazo), 2),
        "fim": max((x["fim"] for x in com_prazo), default=None),
        "sem_taxa": sum(1 for x in com_prazo if not x["taxa"]),
        "n": len(lst), "n_consig": sum(1 for x in lst if x["consignado"]),
    }
    kpi["juros_a_vencer"] = round(kpi["nominal"] - kpi["saldo"], 2)
    hoje_ym = _ym(hoje.year, hoje.month)
    return {"lista": lst, "kpi": kpi, "hoje": hoje_ym, "eventuais": eventuais,
            "json": json.dumps({"emprestimos": [x for x in com_prazo if x["futuras"]],
                                "hoje": hoje_ym, "eventuais": eventuais},
                               ensure_ascii=False).replace("</", "<\\/")}
