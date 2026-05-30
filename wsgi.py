from app import create_app, db
from app.models import User, SubProject, Investment, Card, CardEntry, HouseholdExpense
from werkzeug.security import generate_password_hash
from sqlalchemy import text, inspect
import os

app = create_app()


def _ensure_column(table, column, ddl):
    try:
        with db.engine.connect() as conn:
            insp = inspect(db.engine)
            if not insp.has_table(table):
                return
            cols = [c["name"] for c in insp.get_columns(table)]
            if column not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                conn.commit()
                print(f"[migrate] coluna adicionada: {table}.{column}")
    except Exception as e:
        print(f"[migrate] erro em {table}.{column}: {e}")


def bootstrap():
    with app.app_context():
        # 1. Cria TODAS as tabelas de uma vez (ordem resolvida pelo SQLAlchemy)
        try:
            db.create_all()
            print("[bootstrap] tabelas verificadas/criadas.")
        except Exception as e:
            print(f"[bootstrap] erro no create_all: {e}")
            return

        # 2. Colunas novas em tabelas existentes — APÓS create_all
        _ensure_column("expenses", "kind",              "VARCHAR(20) DEFAULT 'pontual'")
        _ensure_column("expenses", "recurrence_months", "INTEGER")
        _ensure_column("expenses", "card_id",           "INTEGER REFERENCES cards(id) ON DELETE SET NULL")
        _ensure_column("card_entries", "kind", "VARCHAR(20) DEFAULT 'pontual'")
        _ensure_column("card_entries", "status", "VARCHAR(20) DEFAULT 'ativo'")
        _ensure_column("card_entries", "batch_id", "VARCHAR(64)")

        # 3. Migra coluna photo para TEXT
        try:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE users ALTER COLUMN photo TYPE TEXT"))
                conn.commit()
                print("[migrate] users.photo migrada para TEXT")
        except Exception:
            pass

        # 3b1. DEBUG: mostra card_entries de Assinaturas
        try:
            from app.models import Expense, CardEntry
            assinaturas = Expense.query.filter(
                Expense.description.ilike("%assinatura%")
            ).all()
            for exp in assinaturas:
                entries = CardEntry.query.filter_by(expense_id=exp.id).all()
                print(f"[debug] Expense '{exp.description}' id={exp.id} card_id={exp.card_id} kind={exp.kind}")
                print(f"[debug]   -> {len(entries)} CardEntry(s) vinculados")
                for e in entries:
                    print(f"[debug]      entry id={e.id} status={e.status} amount={e.amount} card_id={e.card_id}")
            # Também mostra entries sem expense_id que mencionam assinatura
            orphans = CardEntry.query.filter(
                CardEntry.expense_id == None,
                CardEntry.description.ilike("%assinatura%")
            ).all()
            print(f"[debug] CardEntries órfãos com 'assinatura': {len(orphans)}")
            for e in orphans:
                print(f"[debug]   orphan id={e.id} desc='{e.description}' status={e.status} card_id={e.card_id}")
        except Exception as ex:
            print(f"[debug] erro: {ex}")

        # 3b2. Corrige card_entries com status NULL para ativo
        try:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "UPDATE card_entries SET status = 'ativo' WHERE status IS NULL"
                ))
                conn.commit()
                print("[migrate] card_entries sem status corrigidos para ativo")
        except Exception as e:
            print(f"[migrate] erro ao corrigir status: {e}")

        # 3c. Corrige excedentes de parcelados
        try:
            from app.models import CardEntry, Expense, ExpenseShare
            from decimal import Decimal as _Dec
            from datetime import date as _date
            import calendar

            MESES = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho",
                     "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]

            def _add_months(dt, n):
                month = dt.month - 1 + n
                year = dt.year + month // 12
                month = month % 12 + 1
                day = min(dt.day, calendar.monthrange(year, month)[1])
                return _date(year, month, day)

            today = _date.today()

            # IDs de expenses que têm parcelados vinculados
            expense_ids_parcelados = set(
                e.expense_id for e in CardEntry.query.filter(
                    CardEntry.expense_id != None,
                    CardEntry.kind == "parcelado",
                    CardEntry.status == "ativo"
                ).all()
            )

            # Remove APENAS excedentes cujo expense pai NÃO tem parcelados
            # (foram gerados indevidamente para gastos normais como Restaurante)
            todos_excedentes = Expense.query.filter(
                Expense.description.like("% - excedente %"),
                Expense.kind == "pontual"
            ).all()

            removed = 0
            for exp in todos_excedentes:
                # Descobre qual expense pai gerou este excedente pelo nome
                # Ex: "Restaurante - excedente Maio" -> pai é "Restaurante"
                pai_desc = exp.description.split(" - excedente ")[0]
                pai = Expense.query.filter(
                    Expense.payer_id == exp.payer_id,
                    Expense.description == pai_desc,
                ).first()
                if pai and pai.id not in expense_ids_parcelados:
                    ExpenseShare.query.filter_by(expense_id=exp.id).delete()
                    db.session.delete(exp)
                    removed += 1

            if removed:
                db.session.commit()
                print(f"[migrate] {removed} excedente(s) indevido(s) removido(s)")

            # Remove excedentes de parcelados de maio/2026 para trás
            from datetime import date as _d2
            corte = _d2(2026, 5, 31)  # exclui até maio inclusive
            excedentes_passados = Expense.query.filter(
                Expense.description.like("% - excedente %"),
                Expense.kind == "pontual",
                Expense.spent_at <= corte
            ).all()
            removed_past = 0
            for exp in excedentes_passados:
                ExpenseShare.query.filter_by(expense_id=exp.id).delete()
                db.session.delete(exp)
                removed_past += 1
            if removed_past:
                db.session.commit()
                print(f"[migrate] {removed_past} excedente(s) passado(s) removido(s)")

            # Remove duplicados: mantém só o menor id por (payer_id, description, spent_at)
            todos = Expense.query.filter(
                Expense.description.like("% - excedente %"),
                Expense.kind == "pontual"
            ).order_by(Expense.id).all()
            seen = {}
            removed_dup = 0
            for exp in todos:
                key = (exp.payer_id, exp.description, str(exp.spent_at))
                if key in seen:
                    ExpenseShare.query.filter_by(expense_id=exp.id).delete()
                    db.session.delete(exp)
                    removed_dup += 1
                else:
                    seen[key] = exp.id
            if removed_dup:
                db.session.commit()
                print(f"[migrate] {removed_dup} excedente(s) duplicado(s) removido(s)")

            # Remove excedentes de "Celular Denise" se existirem
            cel_denise = Expense.query.filter(
                Expense.description.like("Celular Denise - excedente %"),
                Expense.kind == "pontual"
            ).all()
            for exp in cel_denise:
                ExpenseShare.query.filter_by(expense_id=exp.id).delete()
                db.session.delete(exp)
            if cel_denise:
                db.session.commit()
                print(f"[migrate] {len(cel_denise)} excedente(s) Celular Denise removido(s)")

            # Projeta excedentes para parcelados
            generated = 0
            for eid in expense_ids_parcelados:
                exp = Expense.query.get(eid)
                if not exp:
                    continue
                planejado = float(exp.amount)
                payer = exp.payer_id

                parcelados = CardEntry.query.filter_by(
                    expense_id=eid, kind="parcelado", status="ativo"
                ).all()

                month_totals = {}
                for entry in parcelados:
                    if not entry.installments:
                        continue
                    first_date = _add_months(entry.entry_date, 1 - (entry.installment_no or 1))
                    for i in range(1, entry.installments + 1):
                        d = _add_months(first_date, i - 1)
                        key = (d.year, d.month)
                        month_totals[key] = month_totals.get(key, 0.0) + float(entry.amount)

                for (year, month), total in month_totals.items():
                    excedente = round(total - planejado, 2)
                    if excedente <= 0:
                        continue
                    mes_nome = MESES[month - 1]
                    desc = f"{exp.description} - excedente {mes_nome}"
                    dt = _date(year, month, min(today.day, calendar.monthrange(year, month)[1]))
                    # Só cria se não existe
                    existing = Expense.query.filter(
                        Expense.payer_id == payer,
                        Expense.description == desc,
                        Expense.kind == "pontual",
                        Expense.spent_at == dt,
                    ).first()
                    if existing:
                        continue
                    # Não projeta excedente para Celular Denise
                    if "celular denise" in desc.lower():
                        continue
                    novo = Expense(
                        payer_id=payer, description=desc, amount=excedente,
                        kind="pontual", share_mode="solo",
                        category=exp.category, spent_at=dt
                    )
                    db.session.add(novo)
                    db.session.flush()
                    db.session.add(ExpenseShare(
                        expense_id=novo.id, user_id=payer,
                        share_amount=_Dec(str(excedente)),
                        share_percent=_Dec("100")
                    ))
                    generated += 1

            if generated:
                db.session.commit()
                print(f"[migrate] {generated} excedente(s) de parcelados projetados")

        except Exception as e:
            db.session.rollback()
            print(f"[migrate] erro excedentes: {e}")

                # 4. Admin
        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
        try:
            existing = User.query.filter_by(username=admin_username).first()
            if not existing:
                db.session.add(User(
                    username=admin_username,
                    full_name="Administrador",
                    email=f"{admin_username}@local",
                    password_hash=generate_password_hash(admin_password),
                    is_admin=True,
                ))
                db.session.commit()
                print(f"[bootstrap] admin '{admin_username}' criado.")
            else:
                reset_pw = os.environ.get("RESET_ADMIN_PASSWORD", "").strip()
                if reset_pw:
                    existing.password_hash = generate_password_hash(reset_pw)
                    db.session.commit()
                    print(f"[bootstrap] senha do admin '{admin_username}' resetada.")
                else:
                    print(f"[bootstrap] admin '{admin_username}' já existe — dados preservados.")
        except Exception as e:
            print(f"[bootstrap] erro ao criar admin: {e}")


bootstrap()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
