"""
Agente do Andamento do Mês
==========================

Roda 1x por dia (à noite) e avisa quando um gasto selecionado no
"Andamento do Mês" ENTRA ou SAI da faixa desejável.

Regra (a mesma da tela do relatório):
  - % ideal  = % do ciclo de fechamento (dia 16 → dia 16) já decorrido
  - dentro   = % gasto <= % ideal + TOLERÂNCIA (5 p.p.)
  - fora     = % gasto  > % ideal + TOLERÂNCIA

Avisos:
  - dentro → fora   : "🔴 saiu da faixa"
  - fora → dentro   : "🟢 voltou para a faixa"
  - 1ª leitura do mês já fora : "🔴 começou o mês fora da faixa"

Canais: notificação dentro do app (sininho) e, se configurado, e-mail
(variáveis SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM).
"""
import os
import smtplib
from datetime import date, datetime
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

TZ = ZoneInfo("America/Sao_Paulo")
TOLERANCIA_PP = 5          # mesma folga usada na tela ("ok": pct <= ideal + 5)
JOB_NAME = "andamento_alertas"


def hoje_brasilia() -> date:
    """Data local (o servidor do Railway roda em UTC; às 21h BRT já é outro dia em UTC)."""
    return datetime.now(TZ).date()


# ─────────────────────────────────────────────────────────────
#  Cálculo (compartilhado com a tela do relatório)
# ─────────────────────────────────────────────────────────────
def pct_ideal_ciclo(hoje: date) -> float:
    """% do ciclo de fechamento (dia 16 ao próximo dia 16) já decorrido."""
    if hoje.day >= 16:
        cs = hoje.replace(day=16)
        ce = (hoje.replace(month=hoje.month + 1, day=16) if hoje.month < 12
              else hoje.replace(year=hoje.year + 1, month=1, day=16))
    else:
        cs = (hoje.replace(month=hoje.month - 1, day=16) if hoje.month > 1
              else hoje.replace(year=hoje.year - 1, month=12, day=16))
        ce = hoje.replace(day=16)
    total = (ce - cs).days
    return min(round((hoje - cs).days / total * 100, 1) if total > 0 else 0, 100)


def mes_fatura_do_ciclo(hoje: date) -> str:
    """Fatura (YYYY-MM) do ciclo em curso: do dia 16 em diante já conta para o mês seguinte."""
    if hoje.day >= 16:
        y, m = (hoje.year + 1, 1) if hoje.month == 12 else (hoje.year, hoje.month + 1)
    else:
        y, m = hoje.year, hoje.month
    return f"{y}-{m:02d}"


def calcular_andamento(user_id: int, billing_month: str, hoje: date, somente_dono: bool = False):
    """
    Devolve (itens, pct_ideal) dos gastos marcados no Andamento do Mês
    para o usuário. Cada item: expense_id, desc, planned, spent, pct, ok.
    """
    from app.models import HouseholdExpense, CardEntry

    links = sorted(
        [hh for hh in HouseholdExpense.query.filter(
            or_(HouseholdExpense.owner_id == user_id,
                HouseholdExpense.shared_with_id == user_id)).all()
         if getattr(hh, "show_on_dashboard", True) is not False
         and (not somente_dono or hh.owner_id == user_id)],
        key=lambda h: h.display_order or 0,
    )
    pct_ideal = pct_ideal_ciclo(hoje)
    itens = []
    for hh in links:
        exp = hh.expense
        if not exp:
            continue
        entries = CardEntry.query.filter(
            CardEntry.expense_id == exp.id,
            CardEntry.billing_month == billing_month,
            CardEntry.status == "ativo",
        ).all()
        spent = sum(float(e.amount) for e in entries)
        planned = float(exp.amount)
        pct = round(spent / planned * 100, 1) if planned > 0 else 0
        itens.append({
            "expense_id": exp.id,
            "desc": exp.description,
            "planned": planned,
            "spent": spent,
            "pct": pct,
            "ok": pct <= pct_ideal + TOLERANCIA_PP,
        })
    itens.sort(key=lambda x: x["pct"], reverse=True)
    return itens, pct_ideal


# ─────────────────────────────────────────────────────────────
#  E-mail (opcional)
# ─────────────────────────────────────────────────────────────
def _brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def email_configurado() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER")
                and os.environ.get("SMTP_PASSWORD"))


def enviar_email(destino: str, assunto: str, corpo: str) -> bool:
    if not email_configurado() or not destino:
        return False
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    pwd = os.environ["SMTP_PASSWORD"]
    remetente = os.environ.get("SMTP_FROM", user)
    msg = MIMEText(corpo, "plain", "utf-8")
    msg["Subject"] = assunto
    msg["From"] = remetente
    msg["To"] = destino
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as s:
                s.login(user, pwd)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls()
                s.login(user, pwd)
                s.send_message(msg)
        return True
    except Exception as e:
        print(f"[agente-andamento] falha ao enviar e-mail para {destino}: {e}")
        return False


# ─────────────────────────────────────────────────────────────
#  Execução
# ─────────────────────────────────────────────────────────────
def _trava_do_dia(hoje: date) -> bool:
    """Garante uma execução por dia mesmo com vários workers do gunicorn."""
    from app import db
    from app.models import AgenteExecucao
    try:
        db.session.add(AgenteExecucao(job=JOB_NAME, run_date=hoje))
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        return False


def rodar_para_usuario(user, hoje: date) -> list:
    """Compara a leitura de hoje com a anterior e gera os avisos. Retorna os avisos."""
    from app import db
    from app.models import AndamentoAlertaEstado, Notificacao
    from app.utils import get_open_billing_month

    mes = get_open_billing_month(user.id, mes_fatura_do_ciclo(hoje))
    itens, pct_ideal = calcular_andamento(user.id, mes, hoje, somente_dono=True)
    limite = round(pct_ideal + TOLERANCIA_PP, 1)
    avisos = []

    for it in itens:
        novo = "dentro" if it["ok"] else "fora"
        est = AndamentoAlertaEstado.query.filter_by(
            user_id=user.id, expense_id=it["expense_id"], billing_month=mes).first()
        anterior = est.status if est else None

        if est is None:
            est = AndamentoAlertaEstado(user_id=user.id, expense_id=it["expense_id"],
                                        billing_month=mes)
            db.session.add(est)
        est.status = novo
        est.pct = it["pct"]
        est.updated_at = datetime.utcnow()

        if anterior == novo or (anterior is None and novo == "dentro"):
            continue

        if novo == "fora":
            titulo = (f"🔴 {it['desc']} saiu da faixa" if anterior == "dentro"
                      else f"🔴 {it['desc']} começou o mês fora da faixa")
        else:
            titulo = f"🟢 {it['desc']} voltou para a faixa"
        texto = (f"Gasto {it['pct']}% do planejado ({_brl(it['spent'])} de {_brl(it['planned'])}). "
                 f"Desejável hoje: até {limite}% (ciclo em {pct_ideal}%).")
        avisos.append({"titulo": titulo, "texto": texto})
        db.session.add(Notificacao(
            user_id=user.id, titulo=titulo, mensagem=texto,
            link=f"/relatorio?mes={mes}#andamento",
        ))

    db.session.commit()

    if avisos:
        destino = os.environ.get("ALERTA_EMAIL_TO") or user.email
        corpo = "\n\n".join(f"{a['titulo']}\n{a['texto']}" for a in avisos)
        corpo += f"\n\n— Nosso Dindin · Andamento de {mes} ({hoje.strftime('%d/%m/%Y')})"
        assunto = (avisos[0]["titulo"] if len(avisos) == 1
                   else f"Nosso Dindin: {len(avisos)} mudanças no Andamento do Mês")
        enviar_email(destino, assunto, corpo)
    return avisos


def rodar_agente(app, forcar: bool = False, somente_user_id: int = None) -> dict:
    """Ponto de entrada do agendador (e do botão 'rodar agora')."""
    from app.models import User
    with app.app_context():
        hoje = hoje_brasilia()
        if not forcar and not _trava_do_dia(hoje):
            print(f"[agente-andamento] já rodou em {hoje}, pulando")
            return {}
        q = User.query
        if somente_user_id:
            q = q.filter(User.id == somente_user_id)
        resultado = {}
        for u in q.all():
            try:
                resultado[u.id] = rodar_para_usuario(u, hoje)
            except Exception as e:
                from app import db
                db.session.rollback()
                print(f"[agente-andamento] erro no usuário {u.id}: {e}")
        print(f"[agente-andamento] {hoje}: "
              f"{sum(len(v) for v in resultado.values())} aviso(s)")
        return resultado
