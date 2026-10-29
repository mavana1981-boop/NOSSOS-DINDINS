from datetime import date, datetime
from apscheduler.schedulers.background import BackgroundScheduler


def _process_auto_contributions(app):
    """Executa aportes automáticos do dia para todos os projetos."""
    from app import db
    from app.models import Project, ProjectMember, Contribution, AutoTransfer

    with app.app_context():
        today = date.today()
        projects = Project.query.filter(
            Project.is_completed.is_(False),
            Project.auto_day == today.day,
            Project.monthly_auto > 0,
        ).all()

        for p in projects:
            # Idempotência: só roda uma vez por mês
            existing = AutoTransfer.query.filter_by(
                project_id=p.id, year=today.year, month=today.month
            ).first()
            if existing:
                continue

            for m in p.members:
                if m.monthly_share and float(m.monthly_share) > 0:
                    c = Contribution(
                        project_id=p.id,
                        user_id=m.user_id,
                        amount=m.monthly_share,
                        contributed_at=today,
                        note=f"Aporte automático {today.month:02d}/{today.year}",
                        is_auto=True,
                    )
                    db.session.add(c)

            db.session.add(AutoTransfer(project_id=p.id, year=today.year, month=today.month))
            try:
                db.session.commit()
                print(f"[auto] aporte projeto {p.id} mês {today.month}/{today.year}")
            except Exception as e:
                db.session.rollback()
                print(f"[auto] falha projeto {p.id}: {e}")


def start_scheduler(app):
    if app.config.get("TESTING"):
        return
    scheduler = BackgroundScheduler(daemon=True, timezone="America/Sao_Paulo")
    # Roda todos os dias às 03:00 - varre projetos do dia
    scheduler.add_job(
        func=lambda: _process_auto_contributions(app),
        trigger="cron",
        hour=3,
        minute=0,
        id="auto_contrib",
        replace_existing=True,
    )
    # Agente do Andamento do Mês — todo dia à noite (padrão 21:00, horário de Brasília)
    from app.agente_andamento import rodar_agente
    import os as _os
    scheduler.add_job(
        func=lambda: rodar_agente(app),
        trigger="cron",
        hour=int(_os.environ.get("AGENTE_ANDAMENTO_HORA", "21")),
        minute=int(_os.environ.get("AGENTE_ANDAMENTO_MINUTO", "0")),
        id="agente_andamento",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )
    # Open Finance (Pluggy) — importa compras novas dos cartões uma vez por dia
    from app.pluggy import rodar_sincronizacao_diaria
    scheduler.add_job(
        func=lambda: rodar_sincronizacao_diaria(app),
        trigger="cron",
        hour=int(_os.environ.get("PLUGGY_SYNC_HORA", "8")),
        minute=int(_os.environ.get("PLUGGY_SYNC_MINUTO", "17")),
        id="pluggy_sync",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )
    try:
        scheduler.start()
        print("[scheduler] iniciado")
    except Exception as e:
        print(f"[scheduler] erro: {e}")
