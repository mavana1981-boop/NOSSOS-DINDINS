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
        _ensure_column("card_entries", "pluggy_tx_id", "VARCHAR(64)")
        _ensure_column("card_entries", "origem", "VARCHAR(20)")
        _ensure_column("closed_months", "fechou_em", "DATE")
        _ensure_column("notificacoes", "push_status", "VARCHAR(300)")
        _ensure_column("push_inscricoes", "ultimo_ok_em", "TIMESTAMP")
        _ensure_column("push_inscricoes", "ultimo_erro", "VARCHAR(300)")
        _ensure_column("push_inscricoes", "ultimo_erro_em", "TIMESTAMP")
        _ensure_column("push_inscricoes", "expirado_em", "TIMESTAMP")
        _ensure_column("push_inscricoes", "instalado", "BOOLEAN")
        _ensure_column("dividas", "expense_id", "INTEGER REFERENCES expenses(id) ON DELETE SET NULL")
        _ensure_column("dividas", "saldo_manual", "BOOLEAN NOT NULL DEFAULT FALSE")
        try:
            with db.engine.connect() as _cc_px:
                _cc_px.execute(text("CREATE INDEX IF NOT EXISTS ix_card_entries_pluggy_tx_id ON card_entries (pluggy_tx_id)"))
                _cc_px.commit()
        except Exception as _epx:
            print(f"[migrate] indice pluggy_tx_id: {_epx}")
        _ensure_column("card_entries", "subcategoria_id", "INTEGER REFERENCES subcategorias(id) ON DELETE SET NULL")
        _ensure_column("merchant_rules", "subcategoria_id", "INTEGER REFERENCES subcategorias(id) ON DELETE SET NULL")
        _ensure_column("subcategorias", "valor", "NUMERIC(12,2)")
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
        _ensure_column("payment_plans", "valor_recebido", "NUMERIC(12,2) DEFAULT 0")
        _ensure_column("household_expenses", "show_on_dashboard", "BOOLEAN DEFAULT TRUE")
        _ensure_column("household_expenses", "display_order", "INTEGER DEFAULT 0")
        try:
            with db.engine.connect() as _cc_pd:
                _cc_pd.execute(text("""
                    CREATE TABLE IF NOT EXISTS payment_defaults (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER REFERENCES users(id),
                        expense_id INTEGER REFERENCES expenses(id),
                        created_at TIMESTAMP DEFAULT NOW(),
                        UNIQUE(user_id, expense_id)
                    )
                """))
                _cc_pd.commit()
        except Exception as _epd:
            print(f"[migrate] payment_defaults: {_epd}")
        try:
            with db.engine.connect() as _cc_del:
                # Recriar tabela com billing_month (em vez de installment_no) — SÓ se ainda
                # estiver no formato antigo. Antes ela era apagada a cada reinício e as
                # exclusões de parcelas planejadas se perdiam a cada deploy.
                _insp_del = inspect(db.engine)
                _recriar = False
                if _insp_del.has_table("planned_installment_deletions"):
                    _cols_del = {c["name"] for c in _insp_del.get_columns("planned_installment_deletions")}
                    if "billing_month" not in _cols_del:
                        _cc_del.execute(text("DROP TABLE planned_installment_deletions"))
                        _recriar = True
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
                if _recriar:
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
                # Aplica RESET_ADMIN_PASSWORD uma única vez por valor: antes a senha era
                # resetada a cada reinício enquanto a variável existisse no Railway.
                from app.models import AppConfig
                import hashlib as _hl
                _marca = _hl.sha256(f"{admin_username}|{reset_pw}".encode()).hexdigest() if reset_pw else None
                _ja = AppConfig.query.get("admin_reset_aplicado")
                if reset_pw and not (_ja and _ja.value == _marca):
                    existing.password_hash = generate_password_hash(reset_pw)
                    if _ja:
                        _ja.value = _marca
                    else:
                        db.session.add(AppConfig(key="admin_reset_aplicado", value=_marca))
                    db.session.commit()
                    print(f"[bootstrap] senha do admin '{admin_username}' resetada (uma vez).")
                else:
                    print(f"[bootstrap] admin '{admin_username}' já existe — dados preservados.")
        except Exception as e:
            db.session.rollback()
            print(f"[bootstrap] erro ao criar admin: {e}")


def _fix_parcelados_duplicados():
    pass


def _backfill_planned_installments():
    pass


def _add_current_installments():  # DESATIVADO — não chame no boot
    pass


def _sync_missing_planned_installments():  # DESATIVADO — não chame no boot
    """Sincroniza CardEntries parcelados que não têm planned_installments.
    Usa descrição NORMALIZADA em todas as comparações para evitar
    falsos negativos por espaços/capitalização."""
    with app.app_context():
        try:
            from app.models import CardEntry as _CE, PlannedInstallment as _PI
            from app.models import PlannedInstallmentDeletion as _PID
            import re as _re_s

            def _n(s):
                """Normaliza descrição: uppercase, sem espaços extras,
                sem sufixos de parcela como '04 DE 10' ou '04/10'."""
                s = (s or "").upper().strip()
                s = _re_s.sub(r"\s+[0-9]{1,2}\s+DE\s+[0-9]{1,2}", "", s)
                s = _re_s.sub(r"\s+[0-9]{1,2}/[0-9]{1,2}", "", s)
                s = _re_s.sub(r"\s+[0-9]{1,2}\s+[0-9]{1,2}(?=\s|$)", "", s)
                return s.strip()

            parcs = _CE.query.filter(
                _CE.installments > 1,
                _CE.installment_no != None,
                _CE.billing_month != None,
                _CE.status == "ativo",
            ).all()

            # Chaves normalizadas dos planned já existentes
            # (card_id, desc_norm, installment_no)
            _existing = set(
                (p.card_id, _n(p.description), p.installment_no)
                for p in _PI.query.all()
            )

            # Exclusões normalizadas: (card_id, desc_norm, billing_month)
            _deleted = set(
                (d.card_id, _n(d.description), d.billing_month)
                for d in _PID.query.all()
            )

            count = 0
            for e in parcs:
                try:
                    _byr = int(e.billing_month[:4])
                    _bmo = int(e.billing_month[5:7])
                except Exception:
                    continue

                _dn = _n(e.description)

                # Parcela atual
                if (e.card_id, _dn, e.installment_no) not in _existing:
                    if (e.card_id, _dn, e.billing_month) not in _deleted:
                        db.session.add(_PI(
                            user_id=e.user_id, card_id=e.card_id,
                            description=e.description, amount=e.amount,
                            installment_no=e.installment_no,
                            installments=e.installments,
                            billing_month=e.billing_month,
                            expense_id=e.expense_id,
                            origin_entry_id=e.id,
                        ))
                        _existing.add((e.card_id, _dn, e.installment_no))
                        count += 1

                # Parcelas futuras
                for _i in range(e.installment_no + 1, e.installments + 1):
                    if (e.card_id, _dn, _i) in _existing:
                        continue
                    _steps = _i - e.installment_no
                    _pmo = _bmo + _steps - 1
                    _pyr = _byr + _pmo // 12
                    _pmo = (_pmo % 12) + 1
                    _proj_bm = f"{_pyr}-{_pmo:02d}"
                    if (e.card_id, _dn, _proj_bm) in _deleted:
                        continue
                    db.session.add(_PI(
                        user_id=e.user_id, card_id=e.card_id,
                        description=e.description, amount=e.amount,
                        installment_no=_i, installments=e.installments,
                        billing_month=_proj_bm,
                        expense_id=e.expense_id,
                        origin_entry_id=e.id,
                    ))
                    _existing.add((e.card_id, _dn, _i))
                    count += 1

            db.session.commit()
            if count:
                print(f"[sync_planned] {count} planned(s) criado(s).")
            else:
                print("[sync_planned] Tudo sincronizado.")
        except Exception as _ex:
            db.session.rollback()
            print(f"[sync_planned] Erro: {_ex}")




def _dedup_card_entries():
    """Remove CardEntries duplicados. Roda em todo boot.
    Parcelados: mesmo (card_id, desc_norm, installment_no) em qualquer billing_month.
    Pontuais: mesmo (card_id, description, amount, entry_date)."""
    with app.app_context():
        try:
            from app.models import CardEntry as _CE
            import re as _re_dd

            def _norm_dd(s):
                s = (s or "").upper().strip()
                s = _re_dd.sub(r"[ ]+[0-9]{1,2}[ ]+DE[ ]+[0-9]{1,2}", "", s)
                s = _re_dd.sub(r"[ ]+[0-9]{1,2}/[0-9]{1,2}", "", s)
                s = _re_dd.sub(r"[ ]+[0-9]{1,2}[ ]+[0-9]{1,2}(?=[ ]|$)", "", s)
                return s[:30].strip()

            entries = _CE.query.filter(
                _CE.status != "excluido"
            ).order_by(_CE.id).all()

            grupos = {}
            for e in entries:
                if e.installments and e.installments > 1:
                    k = ("parc", e.card_id, _norm_dd(e.description), e.installment_no or 0)
                else:
                    k = ("pont", e.card_id,
                         (e.description or "")[:50].upper().strip(),
                         str(round(float(e.amount or 0), 2)),
                         str(e.entry_date or ""))
                if k not in grupos:
                    grupos[k] = []
                grupos[k].append(e)

            removidos = 0
            for k, itens in grupos.items():
                if len(itens) <= 1:
                    continue
                for dup in itens[1:]:
                    dup.status = "excluido"
                    removidos += 1

            if removidos:
                db.session.commit()
                print(f"[dedup_entries] {removidos} duplicata(s) marcada(s) como excluida.")
            else:
                print("[dedup_entries] Nenhum duplicado encontrado.")
        except Exception as _ex:
            db.session.rollback()
            print(f"[dedup_entries] Erro: {_ex}")


def _limpar_planejados_invalidos():
    """Remove planned_installments com installments <= 1 (não são parcelados reais)."""
    with app.app_context():
        try:
            from app.models import PlannedInstallment as _PI
            invalidos = _PI.query.filter(_PI.installments <= 1).all()
            n = len(invalidos)
            for p in invalidos:
                db.session.delete(p)
            if n:
                db.session.commit()
                print(f"[limpar_planejados] {n} planned(s) 1/1 removido(s).")
            else:
                print("[limpar_planejados] Nenhum 1/1 encontrado.")
        except Exception as _ex:
            db.session.rollback()
            print(f"[limpar_planejados] Erro: {_ex}")


def _fechamento_13_11_2026():
    """Correção pontual pedida pelo Marcelo: a fatura que fecharia em 16/11
    fechou em 13/11/2026 — compras de 13/11 em diante vão para a seguinte.
    Roda uma única vez (trava em agente_execucoes, segura com 2 workers)."""
    from datetime import date as _d
    from sqlalchemy import func as _f
    from sqlalchemy.exc import IntegrityError as _IE
    from app.models import AgenteExecucao, User as _U
    from app.utils import fatura_que_fechou_em, registrar_fechamento
    with app.app_context():
        try:
            db.session.add(AgenteExecucao(job="fechamento_2026_11_13", run_date=_d(2026, 11, 13)))
            db.session.commit()
        except _IE:
            db.session.rollback()
            return
        except Exception as _e:
            db.session.rollback()
            print(f"[fechamento 13/11] trava: {_e}")
            return
        try:
            data = _d(2026, 11, 13)
            emails = ["mavana1981@gmail.com", "marcelo.uvara@camara.leg.br"]
            for u in _U.query.filter(_f.lower(_U.email).in_(emails)).all():
                bm = fatura_que_fechou_em(u.id, data)
                if not bm:
                    print(f"[fechamento 13/11] {u.username}: fatura não encontrada")
                    continue
                fr, tr = registrar_fechamento(u.id, bm, data)
                db.session.commit()
                print(f"[fechamento 13/11] {u.username}: fatura {bm} fechada em 13/11; "
                      f"{fr} lançamento(s) movido(s) para a fatura seguinte")
        except Exception as _e:
            db.session.rollback()
            print(f"[fechamento 13/11] erro: {_e}")


def _fechamento_13_11_2026_denise():
    """Mesma correção (fatura fechada em 13/11/2026) para os cartões da Denise.
    Encontra a conta pelo nome; se não achar, usa quem divide gastos da casa
    com o Marcelo. Roda uma única vez (trava própria em agente_execucoes)."""
    from datetime import date as _d
    from sqlalchemy import func as _f, or_ as _or
    from sqlalchemy.exc import IntegrityError as _IE
    from app.models import AgenteExecucao, User as _U, HouseholdExpense as _HH
    from app.utils import fatura_que_fechou_em, registrar_fechamento
    with app.app_context():
        try:
            db.session.add(AgenteExecucao(job="fechamento_2026_11_13_denise", run_date=_d(2026, 11, 13)))
            db.session.commit()
        except _IE:
            db.session.rollback()
            return
        except Exception as _e:
            db.session.rollback()
            print(f"[fechamento 13/11 denise] trava: {_e}")
            return
        try:
            data = _d(2026, 11, 13)
            alvos = _U.query.filter(_or(_f.lower(_U.full_name).contains("denise"),
                                        _f.lower(_U.username).contains("denise"),
                                        _f.lower(_U.email).contains("denise"))).all()
            if not alvos:
                emails = ["mavana1981@gmail.com", "marcelo.uvara@camara.leg.br"]
                marcelo = [u.id for u in _U.query.filter(_f.lower(_U.email).in_(emails)).all()]
                ids = set()
                for hh in _HH.query.filter(_or(_HH.owner_id.in_(marcelo),
                                               _HH.shared_with_id.in_(marcelo))).all():
                    ids.update(x for x in (hh.owner_id, hh.shared_with_id) if x and x not in marcelo)
                alvos = _U.query.filter(_U.id.in_(ids)).all() if ids else []
            if not alvos:
                print("[fechamento 13/11 denise] conta da Denise não encontrada")
            for u in alvos:
                bm = fatura_que_fechou_em(u.id, data)
                if not bm:
                    print(f"[fechamento 13/11 denise] {u.username}: fatura não encontrada")
                    continue
                fr, tr = registrar_fechamento(u.id, bm, data)
                db.session.commit()
                print(f"[fechamento 13/11 denise] {u.username}: fatura {bm} fechada em 13/11; "
                      f"{fr} lançamento(s) movido(s) para a fatura seguinte")
        except Exception as _e:
            db.session.rollback()
            print(f"[fechamento 13/11 denise] erro: {_e}")


def _corrigir_lojas_carteira():
    """Lançamentos da Carteira que ficaram como 'Compra por aproximação' porque o
    MacroDroid mandou o JSON inteiro dentro do texto: relê e põe o nome da loja."""
    with app.app_context():
        try:
            from app.carteira import corrigir_lojas
            n = corrigir_lojas()
            if n:
                print(f"[carteira] {n} lançamento(s) com o nome da loja corrigido")
        except Exception as _e:
            db.session.rollback()
            print(f"[carteira] corrigir lojas: {_e}")


bootstrap()
_corrigir_lojas_carteira()
_fechamento_13_11_2026()
_fechamento_13_11_2026_denise()
_limpar_planejados_invalidos()
_dedup_card_entries()
_fix_parcelados_duplicados()
_backfill_planned_installments()


if __name__ == "__main__":
    app.run(debug=True)
