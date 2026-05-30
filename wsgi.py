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

        # 3b. Corrige excedentes sem ExpenseShare
        try:
            from app.models import Expense, ExpenseShare
            from decimal import Decimal as _Dec
            excedentes = Expense.query.filter(
                Expense.description.like("% - excedente %"),
                Expense.kind == "pontual"
            ).all()
            fixed = 0
            for exp in excedentes:
                share = ExpenseShare.query.filter_by(expense_id=exp.id).first()
                if not share:
                    db.session.add(ExpenseShare(
                        expense_id=exp.id,
                        user_id=exp.payer_id,
                        share_amount=_Dec(str(float(exp.amount))),
                        share_percent=_Dec("100"),
                    ))
                    fixed += 1
            if fixed:
                db.session.commit()
                print(f"[migrate] {fixed} excedente(s) corrigido(s) com ExpenseShare")
        except Exception as e:
            print(f"[migrate] erro ao corrigir excedentes: {e}")

        # 3c. Projeta excedentes de parcelados existentes
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

            parcelados = CardEntry.query.filter_by(kind="parcelado", status="ativo").all()
            generated = 0
            for entry in parcelados:
                if not entry.installments or entry.installments <= 1:
                    continue
                first_date = _add_months(entry.entry_date, 1 - (entry.installment_no or 1))
                payer = entry.user_id
                for i in range(1, entry.installments + 1):
                    parcel_date = _add_months(first_date, i - 1)
                    mes_nome = MESES[parcel_date.month - 1]
                    desc = f"{entry.description} - excedente {mes_nome}"
                    existing = Expense.query.filter(
                        Expense.payer_id == payer,
                        Expense.description == desc,
                        Expense.kind == "pontual",
                        Expense.spent_at == parcel_date,
                    ).first()
                    if existing:
                        continue
                    novo = Expense(
                        payer_id=payer,
                        description=desc,
                        amount=entry.amount,
                        kind="pontual",
                        share_mode="solo",
                        category=entry.category or "Outros",
                        spent_at=parcel_date,
                    )
                    db.session.add(novo)
                    db.session.flush()
                    db.session.add(ExpenseShare(
                        expense_id=novo.id,
                        user_id=payer,
                        share_amount=_Dec(str(float(entry.amount))),
                        share_percent=_Dec("100"),
                    ))
                    generated += 1
            if generated:
                db.session.commit()
                print(f"[migrate] {generated} excedente(s) de parcelados projetados")
        except Exception as e:
            print(f"[migrate] erro ao projetar parcelados: {e}")

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
