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
def ciclo(hoje: date):
    """(início, fim_exclusivo) do ciclo de fechamento que contém `hoje` (16 → 16)."""
    if hoje.day >= 16:
        cs = hoje.replace(day=16)
        ce = (hoje.replace(month=hoje.month + 1, day=16) if hoje.month < 12
              else hoje.replace(year=hoje.year + 1, month=1, day=16))
    else:
        cs = (hoje.replace(month=hoje.month - 1, day=16) if hoje.month > 1
              else hoje.replace(year=hoje.year - 1, month=12, day=16))
        ce = hoje.replace(day=16)
    return cs, ce


def partes_do_ciclo(hoje: date, n: int):
    """
    Divide o ciclo atual em `n` partes iguais (1 a 5).
    Retorna [(inicio, fim_inclusivo), ...]. Os dias que sobram na divisão
    ficam nas primeiras partes (ex.: 31 dias em 5 → 7, 6, 6, 6, 6).
    """
    from datetime import timedelta
    n = max(1, min(5, int(n)))
    cs, ce = ciclo(hoje)
    total = (ce - cs).days
    base, resto = divmod(total, n)
    out, ini = [], cs
    for i in range(n):
        tam = base + (1 if i < resto else 0)
        fim = ini + timedelta(days=tam - 1)
        out.append((ini, fim))
        ini = fim + timedelta(days=1)
    return out


def indice_parte(d, partes):
    """Índice da parte que contém a data `d`. Datas fora do ciclo vão para a
    primeira (antes) ou última parte (depois)."""
    if d is None:
        return len(partes) - 1
    for i, (ini, fim) in enumerate(partes):
        if ini <= d <= fim:
            return i
    return 0 if d < partes[0][0] else len(partes) - 1


def gasto_por_parte(lancamentos, partes):
    """Soma os lançamentos (entry_date, valor) em cada parte. Datas fora do
    ciclo vão para a primeira (antes) ou última parte (depois)."""
    somas = [0.0] * len(partes)
    for d, v in lancamentos:
        somas[indice_parte(d, partes)] += float(v)
    return [round(x, 2) for x in somas]


def redistribuir_metas(metas, gastos, partes, hoje):
    """
    Orçamento rolante com meta total fixa.
      - Parte encerrada: sobra/estouro passa para as partes seguintes,
        na proporção das metas originais delas.
      - Parte atual: gasto conta contra ela; se já estourou o disponível,
        o excesso reduz as futuras na hora; se está abaixo, a sobra só
        passa adiante quando a parte terminar.
    Retorna lista de disponíveis ajustados (pode ser negativo = total estourado).
    """
    n = len(metas)
    total = sum(metas)
    disp = list(metas)
    consumido = 0.0          # gasto efetivo das partes já encerradas
    for i in range(n):
        ini, fim = partes[i]
        restante_meta = sum(metas[i:])
        if i > 0:
            base = total - consumido
            disp[i] = base * (metas[i] / restante_meta) if restante_meta > 0 else base / (n - i)
        if fim < hoje:                       # encerrada
            consumido += gastos[i]
        elif ini <= hoje <= fim:             # atual: reserva o maior entre disponível e gasto
            reservado = max(disp[i], gastos[i])
            base_fut = total - consumido - reservado
            meta_fut = sum(metas[i + 1:])
            for j in range(i + 1, n):
                disp[j] = (base_fut * (metas[j] / meta_fut) if meta_fut > 0
                           else base_fut / (n - i - 1))
            break
        # futura antes de achar a atual (hoje antes do ciclo): mantém proporção
    return [round(x, 2) for x in disp]


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


def _smtp_configurado() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER")
                and os.environ.get("SMTP_PASSWORD"))


def email_configurado() -> bool:
    return bool(os.environ.get("RESEND_API_KEY") or os.environ.get("BREVO_API_KEY")
                or _smtp_configurado())


def _registrar_email(status: str) -> None:
    """Guarda o resultado do último envio para mostrar na página de Avisos."""
    try:
        from datetime import datetime
        from app import db
        from app.models import AppConfig
        row = AppConfig.query.get("email_ultimo_status") or AppConfig(key="email_ultimo_status", value="")
        row.value = f"{datetime.utcnow().isoformat()}|{status[:280]}"
        db.session.add(row)
        db.session.commit()
    except Exception:
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


def ultimo_status_email():
    """(datetime_utc, texto) do último envio de e-mail, ou None."""
    try:
        from datetime import datetime
        from app.models import AppConfig
        row = AppConfig.query.get("email_ultimo_status")
        if not row or "|" not in row.value:
            return None
        quando, texto = row.value.split("|", 1)
        return datetime.fromisoformat(quando), texto
    except Exception:
        return None


def _post_json(url: str, headers: dict, dados: dict) -> None:
    import json
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(dados).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}")


def enviar_email(destino: str, assunto: str, corpo: str) -> bool:
    if not email_configurado() or not destino:
        return False
    try:
        if os.environ.get("RESEND_API_KEY"):
            remetente = os.environ.get("EMAIL_FROM") or "Nosso Dindin <onboarding@resend.dev>"
            _post_json("https://api.resend.com/emails",
                       {"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
                       {"from": remetente, "to": [destino], "subject": assunto, "text": corpo})
            _registrar_email(f"✅ enviado para {destino} (Resend)")
            return True
        if os.environ.get("BREVO_API_KEY"):
            remetente = os.environ.get("EMAIL_FROM") or os.environ.get("SMTP_FROM") or destino
            _post_json("https://api.brevo.com/v3/smtp/email",
                       {"api-key": os.environ["BREVO_API_KEY"]},
                       {"sender": {"name": "Nosso Dindin", "email": remetente},
                        "to": [{"email": destino}], "subject": assunto, "textContent": corpo})
            _registrar_email(f"✅ enviado para {destino} (Brevo)")
            return True
    except Exception as e:
        print(f"[email] falha ao enviar para {destino}: {e}")
        _registrar_email(f"❌ falhou: {e}")
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
        _registrar_email(f"✅ enviado para {destino} (SMTP)")
        return True
    except Exception as e:
        print(f"[agente-andamento] falha ao enviar e-mail para {destino}: {e}")
        _registrar_email(f"❌ SMTP falhou: {e} — o Railway (plano Hobby) bloqueia SMTP; use RESEND_API_KEY")
        return False


# ─────────────────────────────────────────────────────────────
#  Execução
# ─────────────────────────────────────────────────────────────
def andamento_com_lote(user_id: int, hoje: date, batch_ids=()) -> str:
    """Uma linha por gasto do Andamento (sem gravar nada).

    Compras ainda em avaliação dos lotes indicados entram como projeção:
    "84% → 90% (+R$ 230,00 em avaliação)".
    """
    from sqlalchemy import func
    from app import db
    from app.models import CardEntry
    from app.utils import get_open_billing_month

    mes = get_open_billing_month(user_id, mes_fatura_do_ciclo(hoje))
    itens, pct_ideal = calcular_andamento(user_id, mes, hoje, somente_dono=True)
    if not itens:
        return ""
    limite = pct_ideal + TOLERANCIA_PP
    pend = {}
    sem_gasto = 0.0
    if batch_ids:
        linhas_pend = (db.session.query(CardEntry.expense_id, func.sum(CardEntry.amount))
                       .filter(CardEntry.batch_id.in_(list(batch_ids)),
                               CardEntry.status == "em_avaliacao",
                               CardEntry.billing_month == mes)
                       .group_by(CardEntry.expense_id).all())
        for exp_id, total in linhas_pend:
            if exp_id is None:
                sem_gasto += float(total or 0)
            else:
                pend[exp_id] = float(total or 0)
    ids_andamento = {it["expense_id"] for it in itens}
    sem_gasto += sum(v for k, v in pend.items() if k not in ids_andamento)

    linhas = []
    for it in itens:
        extra = pend.get(it["expense_id"], 0.0)
        if extra:
            pct2 = (it["spent"] + extra) / it["planned"] * 100 if it["planned"] else 0
            bola = "🟢" if pct2 <= limite else "🔴"
            linhas.append(f"{bola} {it['desc']}: {it['pct']:.0f}% → {pct2:.0f}% (+{_brl(extra)} em avaliação)")
        else:
            bola = "🟢" if it["ok"] else "🔴"
            linhas.append(f"{bola} {it['desc']}: {it['pct']:.0f}% · {_brl(it['spent'])}")
    linhas.append(f"Ideal hoje: {pct_ideal:.0f}% · faixa até {limite:.0f}%")
    if sem_gasto > 0.004:
        linhas.append(f"{_brl(sem_gasto)} em avaliação ainda sem gasto do Andamento")
    return "\n".join(linhas)


def registrar_dia_e_resumir(user_id: int, mes: str, hoje: date, itens: list, pct_ideal: float):
    """
    Grava a foto de hoje de cada gasto e monta o resumo diário com a variação
    desde a última foto (normalmente a de ontem). Retorna {"titulo", "texto"} ou None.
    """
    from app import db
    from app.models import AndamentoDiario
    if not itens:
        return None
    limite = round(pct_ideal + TOLERANCIA_PP, 1)
    linhas = []
    for it in itens:
        anterior = (AndamentoDiario.query
                    .filter(AndamentoDiario.user_id == user_id,
                            AndamentoDiario.expense_id == it["expense_id"],
                            AndamentoDiario.billing_month == mes,
                            AndamentoDiario.dia < hoje)
                    .order_by(AndamentoDiario.dia.desc()).first())
        foto = AndamentoDiario.query.filter_by(
            user_id=user_id, expense_id=it["expense_id"], dia=hoje).first()
        if foto is None:
            foto = AndamentoDiario(user_id=user_id, expense_id=it["expense_id"], dia=hoje)
            db.session.add(foto)
        foto.billing_month = mes
        foto.planned, foto.spent, foto.pct, foto.pct_ideal = (
            it["planned"], it["spent"], it["pct"], pct_ideal)

        if anterior is None:
            var = "novo"
        else:
            d = round(it["spent"] - (anterior.spent or 0), 2)
            var = f"+{_brl(d)}" if d > 0.004 else ("=" if abs(d) <= 0.004 else f"−{_brl(-d)}")
        bola = "🟢" if it["ok"] else "🔴"
        linhas.append(f"{bola} {it['desc']}: {it['pct']:.0f}% · {_brl(it['spent'])} ({var})")

    fora = sum(1 for it in itens if not it["ok"])
    titulo = (f"📊 Andamento {hoje.strftime('%d/%m')} · ideal {pct_ideal:.0f}%"
              + (f" · {fora} fora" if fora else " · tudo ok"))
    texto = "\n".join(linhas) + f"\nFaixa desejável: até {limite:.0f}%"
    return {"titulo": titulo, "texto": texto}


CARTAO_ALERTA_DIAS = int(os.environ.get("CARTAO_ALERTA_DIAS", "3"))


def cartoes_ultimo_lancamento(user_id: int, hoje: date) -> list:
    """
    Para cada cartão ativo do usuário: último lançamento (pela data da compra),
    quando foi importado/cadastrado e quantos dias se passaram até hoje.
    """
    from app.models import Card, CardEntry
    out = []
    for c in Card.query.filter_by(user_id=user_id, is_active=True).order_by(Card.name).all():
        ult = (CardEntry.query
               .filter(CardEntry.card_id == c.id,
                       or_(CardEntry.status == "ativo", CardEntry.status.is_(None)))
               .order_by(CardEntry.entry_date.desc(), CardEntry.id.desc()).first())
        if ult is None:
            out.append({"card": c.name, "data": None, "dias": None, "desc": None,
                        "valor": None, "atrasado": True})
            continue
        dias = (hoje - ult.entry_date).days
        out.append({
            "card": c.name,
            "data": ult.entry_date,
            "dias": max(dias, 0),
            "desc": ult.description,
            "valor": float(ult.amount),
            "atrasado": dias > CARTAO_ALERTA_DIAS,
        })
    # mais desatualizados primeiro
    out.sort(key=lambda x: -(x["dias"] if x["dias"] is not None else 10**6))
    return out


def resumo_cartoes(user_id: int, hoje: date):
    """Monta o aviso dos cartões. Retorna {"titulo", "texto"} ou None se não houver cartões."""
    cards = cartoes_ultimo_lancamento(user_id, hoje)
    if not cards:
        return None

    def _dias(n):
        return "hoje" if n == 0 else ("há 1 dia" if n == 1 else f"há {n} dias")

    linhas = []
    for c in cards:
        if c["data"] is None:
            linhas.append(f"⚠️ {c['card']}: nenhum lançamento")
        else:
            icone = "⚠️" if c["atrasado"] else "✅"
            linhas.append(f"{icone} {c['card']}: último em {c['data'].strftime('%d/%m')} ({_dias(c['dias'])})")
    atrasados = sum(1 for c in cards if c["atrasado"])
    titulo = (f"💳 Cartões · {atrasados} sem atualização há mais de {CARTAO_ALERTA_DIAS} dias"
              if atrasados else "💳 Cartões · lançamentos em dia")
    return {"titulo": titulo, "texto": "\n".join(linhas), "atrasados": atrasados}


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
        texto = (f"Gasto {it['pct']:.0f}% do planejado ({_brl(it['spent'])} de {_brl(it['planned'])}). "
                 f"Desejável hoje: até {limite:.0f}% (ciclo em {pct_ideal:.0f}%).")
        avisos.append({"titulo": titulo, "texto": texto})
        db.session.add(Notificacao(
            user_id=user.id, titulo=titulo, mensagem=texto,
            link=f"/relatorio?mes={mes}#andamento",
        ))

    # ── Foto do dia + resumo da evolução de cada gasto ──
    resumo = registrar_dia_e_resumir(user.id, mes, hoje, itens, pct_ideal)
    db.session.commit()

    # ── Último lançamento de cada cartão ──
    try:
        cartoes = resumo_cartoes(user.id, hoje)
    except Exception as e:
        cartoes = None
        print(f"[agente-andamento] cartões falhou para usuário {user.id}: {e}")

    # ── Notificações no celular (push) ──
    try:
        from app.webpush import enviar_para_usuario
        # Um push só por noite: vários avisos ignorados levam o Chrome a
        # tirar a permissão de notificação do site.
        # O corpo traz o andamento de TODOS os gastos (uma linha cada).
        partes = []
        if resumo:
            partes.append(resumo["texto"])               # 🟢/🔴 Gasto: % · R$ (variação)
            if avisos:
                partes.append("Mudou de faixa: " + ", ".join(
                    a["titulo"].replace(" saiu da faixa", " ↑").replace(" voltou para a faixa", " ↓")
                     .replace(" começou o mês fora da faixa", " ↑") for a in avisos))
        else:
            partes += [a["titulo"] for a in avisos]
        if cartoes:
            partes.append(cartoes["titulo"])
        if partes:
            if resumo:
                titulo = resumo["titulo"]                # 📊 Andamento dd/mm · ideal X% · N fora
                if avisos:
                    titulo += f" · {len(avisos)} mudou de faixa" if len(avisos) == 1 else f" · {len(avisos)} mudaram de faixa"
                url = "/notificacoes#evolucao"
            else:
                titulo = avisos[0]["titulo"] if avisos else cartoes["titulo"]
                url = f"/relatorio?mes={mes}#andamento" if avisos else "/notificacoes#cartoes"
            enviar_para_usuario(user.id, {"title": titulo, "body": "\n".join(partes)[:1800],
                                          "url": url, "tag": "resumo-noite"})
    except Exception as e:
        print(f"[agente-andamento] push falhou para usuário {user.id}: {e}")

    if avisos:
        destino = os.environ.get("ALERTA_EMAIL_TO") or user.email
        corpo = "\n\n".join(f"{a['titulo']}\n{a['texto']}" for a in avisos)
        if resumo:
            corpo += f"\n\n{resumo['titulo']}\n{resumo['texto']}"
        if cartoes:
            corpo += f"\n\n{cartoes['titulo']}\n{cartoes['texto']}"
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
