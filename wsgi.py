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
        _ensure_column("card_entries", "billing_month", "VARCHAR(7)")
        _ensure_column("payment_plans", "mes_ref", "VARCHAR(7) NOT NULL DEFAULT ''")
        _ensure_column("card_month_history", "card_id", "INTEGER REFERENCES cards(id)")
        _ensure_column("card_month_history", "snapshot", "TEXT")
        _ensure_column("card_month_history", "entry_count", "INTEGER DEFAULT 0")
        # Corrigir constraint: de UNIQUE(user_id) para UNIQUE(user_id, mes_ref)
        try:
            with db.engine.connect() as _conn_fix:
                _conn_fix.execute(text(
                    "ALTER TABLE payment_plans DROP CONSTRAINT IF EXISTS payment_plans_user_id_key"
                ))
                _conn_fix.execute(text(
                    "DO $$ BEGIN "
                    "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'payment_plans_user_id_mes_ref_key') "
                    "THEN ALTER TABLE payment_plans ADD CONSTRAINT payment_plans_user_id_mes_ref_key UNIQUE(user_id, mes_ref); "
                    "END IF; END $$"
                ))
                _conn_fix.commit()
        except Exception as _e_fix:
            print(f"[migrate] payment_plans constraint: {_e_fix}")
        _ensure_column("payment_items", "is_paid", "BOOLEAN DEFAULT FALSE")
        _ensure_column("payment_items", "due_date", "DATE")
        _ensure_column("payment_card_status", "amount_override", "NUMERIC(12,2)")
        # Tabela de regras de categorização por estabelecimento
        with db.engine.connect() as _conn2:
            _conn2.execute(text("""
                CREATE TABLE IF NOT EXISTS merchant_rules (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    keyword VARCHAR(120) NOT NULL,
                    category VARCHAR(80) NOT NULL,
                    expense_id INTEGER REFERENCES expenses(id),
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, keyword)
                )
            """))
            _conn2.execute(text("""
                CREATE TABLE IF NOT EXISTS payment_plans (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    mes_ref VARCHAR(7) NOT NULL DEFAULT '',
                    saldo_inicial NUMERIC(12,2) DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, mes_ref)
                )
            """))
            _conn2.execute(text("""
                CREATE TABLE IF NOT EXISTS payment_items (
                    id SERIAL PRIMARY KEY,
                    plan_id INTEGER REFERENCES payment_plans(id) ON DELETE CASCADE,
                    description VARCHAR(200) NOT NULL,
                    amount NUMERIC(12,2) NOT NULL,
                    expense_id INTEGER REFERENCES expenses(id),
                    is_paid BOOLEAN DEFAULT FALSE,
                    due_date DATE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))
            _conn2.execute(text("""
                CREATE TABLE IF NOT EXISTS payment_card_status (
                    id SERIAL PRIMARY KEY,
                    plan_id INTEGER REFERENCES payment_plans(id) ON DELETE CASCADE,
                    card_id INTEGER REFERENCES cards(id),
                    is_paid BOOLEAN DEFAULT FALSE,
                    due_date DATE,
                    UNIQUE(plan_id, card_id)
                )
            """))
            _conn2.commit()
        # Tabela de histórico mensal — criada via SQLAlchemy
        try:
            from app.models import CardMonthHistory as _CMH
            with app.app_context():
                db.create_all()
        except Exception as _e:
            print(f"[migrate] card_month_history: {_e}")

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

        # 3b-3c. Limpa excedentes duplicados e órfãos
        try:
            from app.models import Expense, ExpenseShare

            todos = Expense.query.filter(
                Expense.description.like("% - excedente %"),
                Expense.kind == "pontual"
            ).order_by(Expense.id).all()

            # 1. Remove duplicatas
            seen = {}
            removed = 0
            for exp in todos:
                key = (exp.payer_id, exp.description, exp.spent_at.year, exp.spent_at.month)
                if key in seen:
                    ExpenseShare.query.filter_by(expense_id=exp.id).delete()
                    db.session.delete(exp)
                    removed += 1
                else:
                    seen[key] = exp.id
            if removed:
                db.session.commit()
                print(f"[migrate] {removed} excedente(s) duplicado(s) removido(s)")

            # 2. Remove órfãos — excedentes cujo gasto original foi excluído
            todos2 = Expense.query.filter(
                Expense.description.like("% - excedente %"),
                Expense.kind == "pontual"
            ).all()
            orphans = 0
            for exp in todos2:
                parts = exp.description.split(" - excedente ")
                if len(parts) < 2:
                    continue
                nome_base = parts[0].strip()
                original = Expense.query.filter(
                    Expense.payer_id == exp.payer_id,
                    Expense.description == nome_base,
                    Expense.id != exp.id
                ).first()
                if not original:
                    ExpenseShare.query.filter_by(expense_id=exp.id).delete()
                    db.session.delete(exp)
                    orphans += 1
            if orphans:
                db.session.commit()
                print(f"[migrate] {orphans} excedente(s) órfão(s) removido(s)")

        except Exception as e:
            db.session.rollback()
            print(f"[migrate] erro limpeza excedentes: {e}")

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
