import sys, traceback
try:
    from app import create_app, db
    from app.models import User, SubProject, Investment, Card, CardEntry, HouseholdExpense
except Exception as _boot_err:
    print(f"[BOOT ERROR] Import falhou: {_boot_err}", file=sys.stderr)
    traceback.print_exc()
    raise
from werkzeug.security import generate_password_hash
from sqlalchemy import text, inspect
import os

try:
    app = create_app()
except Exception as _app_err:
    import sys, traceback
    print(f"[BOOT ERROR] create_app() falhou: {_app_err}", file=sys.stderr)
    traceback.print_exc()
    raise


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
        try:
            with db.engine.connect() as _cc_del:
                # Recriar tabela com billing_month (em vez de installment_no)
                _cc_del.execute(text(
                    "DROP TABLE IF EXISTS planned_installment_deletions"
                ))
                _cc_del.execute(text("""
                    CREATE TABLE IF NOT EXISTS planned_installment_deletions (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER REFERENCES users(id),
                        card_id INTEGER,
                        description VARCHAR(200) NOT NULL,
                        billing_month VARCHAR(7) NOT NULL,
                        deleted_at TIMESTAMP DEFAULT NOW(),
                        UNIQUE(user_id, card_id, description, billing_month)
                    )
                """))
                _cc_del.commit()
                print("[migrate] planned_installment_deletions recriada com billing_month")
        except Exception as _edel:
            print(f"[migrate] planned_installment_deletions: {_edel}")
        # Corrigir FK de origin_entry_id para ON DELETE SET NULL
        try:
            with db.engine.connect() as _cc_fk:
                _cc_fk.execute(text(
                    "ALTER TABLE planned_installments "
                    "DROP CONSTRAINT IF EXISTS planned_installments_origin_entry_id_fkey"
                ))
                _cc_fk.execute(text(
                    "ALTER TABLE planned_installments "
                    "ADD CONSTRAINT planned_installments_origin_entry_id_fkey "
                    "FOREIGN KEY (origin_entry_id) REFERENCES card_entries(id) ON DELETE SET NULL"
                ))
                _cc_fk.commit()
                print("[migrate] planned_installments FK corrigida para ON DELETE SET NULL")
        except Exception as _efk:
            print(f"[migrate] FK planned_installments: {_efk}")
        # Tabela de parcelas planejadas (projeção persistente)
        try:
            with db.engine.connect() as _ccpi:
                _ccpi.execute(text("""
                    CREATE TABLE IF NOT EXISTS planned_installments (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER REFERENCES users(id),
                        card_id INTEGER REFERENCES cards(id),
                        description VARCHAR(200) NOT NULL,
                        amount NUMERIC(12,2) NOT NULL,
                        installment_no INTEGER NOT NULL,
                        installments INTEGER NOT NULL,
                        billing_month VARCHAR(7) NOT NULL,
                        expense_id INTEGER REFERENCES expenses(id),
                        origin_entry_id INTEGER REFERENCES card_entries(id),
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """))
                _ccpi.commit()
        except Exception as _epi:
            print(f"[migrate] planned_installments: {_epi}")
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
            _conn2.execute(text("""
                CREATE TABLE IF NOT EXISTS closed_months (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    billing_month VARCHAR(7) NOT NULL,
                    closed_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, billing_month)
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


def _fix_parcelados_duplicados():
    pass


def _backfill_planned_installments():
    pass


def _add_current_installments():
    """Adiciona ao planned_installments os lançamentos ATUAIS.
    Roda apenas uma vez — se já há dados, sai imediatamente para não
    recriar itens que o usuário excluiu manualmente."""
    with app.app_context():
        try:
            from app.models import CardEntry as _CE, PlannedInstallment as _PI

            # Sair se já há planejados — evitar recriar exclusões manuais
            if _PI.query.count() > 0:
                print("[add_current] Planejados já existem — pulando.")
                return
            # Carregar exclusões permanentes
            from app.models import PlannedInstallmentDeletion as _PID
            _deleted = set(
                (d.user_id, d.card_id, d.description, d.billing_month)
                for d in _PID.query.all()
            )

            # Buscar todos os parcelados ativos com billing_month definido
            parcs = _CE.query.filter(
                _CE.installments > 1,
                _CE.installment_no != None,
                _CE.billing_month != None,
                _CE.status != "excluido",
            ).all()

            count = 0
            for e in parcs:
                # Pular se esta série foi excluída para este billing_month
                if (e.user_id, e.card_id, e.description, e.billing_month) in _deleted:
                    continue
                exists = _PI.query.filter_by(
                    user_id=e.user_id,
                    card_id=e.card_id,
                    description=e.description,
                    installment_no=e.installment_no,
                ).first()
                if exists:
                    continue
                pi = _PI(
                    user_id=e.user_id,
                    card_id=e.card_id,
                    description=e.description,
                    amount=e.amount,
                    installment_no=e.installment_no,
                    installments=e.installments,
                    billing_month=e.billing_month,
                    expense_id=e.expense_id,
                    origin_entry_id=e.id,
                )
                db.session.add(pi)
                count += 1

            db.session.commit()
            print(f"[add_current] {count} parcela(s) atual(is) adicionada(s).")
        except Exception as _ex:
            db.session.rollback()
            print(f"[add_current] Erro: {_ex}")


    """Popula planned_installments a partir dos CardEntries parcelados já existentes."""
    with app.app_context():
        try:
            from app.models import CardEntry as _CE, PlannedInstallment as _PI

            # Verificar se já tem dados (evitar reprocessar)
            if _PI.query.count() > 0:
                print(f"[backfill_planned] {_PI.query.count()} item(s) já existem — pulando.")
                return

            # Buscar todos os parcelados ativos com billing_month definido
            parcs = _CE.query.filter(
                _CE.installments > 1,
                _CE.installment_no != None,
                _CE.billing_month != None,
                _CE.status != "excluido",
            ).all()

            # Para cada série (card + desc_norm), pegar a parcela mais recente
            import re as _re_bf
            def _norm_bf(s):
                s = (s or "").upper().strip()
                s = _re_bf.sub(r"[ ]+[0-9]{1,2}[ ]+DE[ ]+[0-9]{1,2}", "", s)
                s = _re_bf.sub(r"[ ]+[0-9]{1,2}/[0-9]{1,2}", "", s)
                s = _re_bf.sub(r"[ ]+[0-9]{1,2}[ ]+[0-9]{1,2}(?=[ ]|$)", "", s)
                return s[:30].strip()

            latest = {}
            for e in parcs:
                k = (e.card_id, _norm_bf(e.description))
                prev = latest.get(k)
                if prev is None or e.installment_no > prev.installment_no:
                    latest[k] = e

            count = 0
            for (cid, dnorm), entry in latest.items():
                try:
                    byr = int(entry.billing_month[:4])
                    bmo = int(entry.billing_month[5:7])
                except Exception:
                    continue

                for i in range(entry.installment_no + 1, entry.installments + 1):
                    steps = i - entry.installment_no
                    pmo = bmo + steps - 1
                    pyr = byr + pmo // 12
                    pmo = (pmo % 12) + 1
                    proj_bm = f"{pyr}-{pmo:02d}"

                    # Não duplicar
                    exists = _PI.query.filter_by(
                        user_id=entry.user_id,
                        card_id=entry.card_id,
                        description=entry.description,
                        installment_no=i,
                    ).first()
                    if exists:
                        continue

                    pi = _PI(
                        user_id=entry.user_id,
                        card_id=entry.card_id,
                        description=entry.description,
                        amount=entry.amount,
                        installment_no=i,
                        installments=entry.installments,
                        billing_month=proj_bm,
                        expense_id=entry.expense_id,
                        origin_entry_id=entry.id,
                    )
                    db.session.add(pi)
                    count += 1

            db.session.commit()
            print(f"[backfill_planned] {count} parcela(s) planejada(s) criada(s).")
        except Exception as _ex:
            db.session.rollback()
            print(f"[backfill_planned] Erro: {_ex}")


    """Remove entradas duplicadas cross-month: mesmo cartão, mesma desc, mesmo valor.
    Mantém a do billing_month mais recente."""
    import re as _re_fix
    def _norm_fix(s):
        s = (s or "").upper().strip()
        s = _re_fix.sub(r"[ ]+[0-9]{1,2}[ ]+DE[ ]+[0-9]{1,2}", "", s)
        s = _re_fix.sub(r"[ ]+[0-9]{1,2}/[0-9]{1,2}", "", s)
        s = _re_fix.sub(r"[ ]+[0-9]{1,2}[ ]+[0-9]{1,2}(?=[ ]|$)", "", s)
        return s[:30].strip()

    with app.app_context():
        try:
            from app.models import CardEntry as _CE
            # Buscar TODOS os entries ativos (independente de installments)
            todos = _CE.query.filter(
                _CE.status != "excluido",
                _CE.billing_month != None,
            ).all()

            # Agrupar por (card_id, desc_norm, amount, installment_no)
            # installment_no=None → tratado como 0
            grupos = {}
            for e in todos:
                k = (
                    e.card_id,
                    _norm_fix(e.description),
                    str(round(float(e.amount or 0), 2)),
                    e.installment_no or 0,
                )
                if k not in grupos:
                    grupos[k] = []
                grupos[k].append(e)

            removidos = 0
            detalhes = []
            for k, entries in grupos.items():
                if len(entries) <= 1:
                    continue
                # Mais de 1 entry com mesma (cartão, desc, valor, parcela) → duplicata
                entries_sorted = sorted(
                    entries,
                    key=lambda e: (e.billing_month or "0000-00"),
                    reverse=True  # mais recente primeiro
                )
                manter = entries_sorted[0]
                for e in entries_sorted[1:]:
                    detalhes.append(
                        f"  del id={e.id} '{e.description}' "
                        f"R${e.amount} bm={e.billing_month} "
                        f"(mantém id={manter.id} bm={manter.billing_month})"
                    )
                    db.session.delete(e)
                    removidos += 1

            if removidos:
                db.session.commit()
                print(f"[fix_parcelados] {removidos} duplicata(s) removida(s):")
                for d in detalhes:
                    print(d)
            else:
                # Log das chaves com 2+ entries para diagnóstico
                multi = [(k, v) for k, v in grupos.items() if len(v) >= 2]
                if not multi:
                    print("[fix_parcelados] Nenhuma duplicata cross-month encontrada.")
                else:
                    print(f"[fix_parcelados] {len(multi)} grupos com 2+ entries (não removidos por segurança):")
                    for k, v in multi[:5]:
                        bms = [e.billing_month for e in v]
                        print(f"  {k} → billing_months={bms}")
        except Exception as _ex:
            db.session.rollback()
            print(f"[fix_parcelados] Erro: {_ex}")


bootstrap()
_fix_parcelados_duplicados()


_backfill_planned_installments()


_add_current_installments()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
