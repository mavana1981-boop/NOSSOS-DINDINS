from app import create_app, db
from app.models import User
from werkzeug.security import generate_password_hash
from sqlalchemy import text, inspect
import os

app = create_app()


def _ensure_column(table, column, ddl):
    """Adiciona coluna se ela ainda não existir (Postgres ou SQLite)."""
    insp = inspect(db.engine)
    if not insp.has_table(table):
        return
    cols = [c["name"] for c in insp.get_columns(table)]
    if column not in cols:
        try:
            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            db.session.commit()
            print(f"[migrate] adicionada coluna {table}.{column}")
        except Exception as e:
            db.session.rollback()
            print(f"[migrate] erro adicionando {table}.{column}: {e}")


def _ensure_table(table_name, model):
    """Cria tabela se ela ainda não existir."""
    insp = inspect(db.engine)
    if not insp.has_table(table_name):
        try:
            model.__table__.create(db.engine)
            print(f"[migrate] tabela {table_name} criada")
        except Exception as e:
            print(f"[migrate] erro criando {table_name}: {e}")


def bootstrap_admin():
    """Cria usuário admin na primeira execução + migrações simples."""
    with app.app_context():
        db.create_all()

        # Migrações idempotentes (para upgrades de versões anteriores)
        _ensure_column("expenses", "kind", "VARCHAR(20) DEFAULT 'pontual'")
        _ensure_column("expenses", "recurrence_months", "INTEGER")

        from app.models import SubProject
        _ensure_table("subprojects", SubProject)

        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
        existing = User.query.filter_by(username=admin_username).first()
        if not existing:
            admin = User(
                username=admin_username,
                full_name="Administrador",
                email=f"{admin_username}@local",
                password_hash=generate_password_hash(admin_password),
                is_admin=True,
            )
            db.session.add(admin)
            db.session.commit()
            print(f"[bootstrap] Admin '{admin_username}' criado.")
        else:
            print(f"[bootstrap] Admin '{admin_username}' já existe.")


bootstrap_admin()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
