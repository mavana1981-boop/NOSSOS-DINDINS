from app import create_app, db
from app.models import User, SubProject, Investment
from werkzeug.security import generate_password_hash
from sqlalchemy import text, inspect
import os

app = create_app()


def _ensure_column(table, column, ddl):
    """Adiciona coluna se não existir — idempotente."""
    with db.engine.connect() as conn:
        insp = inspect(db.engine)
        if not insp.has_table(table):
            return
        cols = [c["name"] for c in insp.get_columns(table)]
        if column not in cols:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                conn.commit()
                print(f"[migrate] coluna adicionada: {table}.{column}")
            except Exception as e:
                print(f"[migrate] erro em {table}.{column}: {e}")


def bootstrap():
    """Inicializa banco e admin. Seguro para rodar a cada deploy."""
    with app.app_context():
        # Cria apenas tabelas que não existem (checkfirst=True é o padrão do create_all)
        # Nunca recria nem apaga dados existentes.
        db.create_all()

        # Migrações de colunas novas em tabelas já existentes
        _ensure_column("expenses", "kind",              "VARCHAR(20) DEFAULT 'pontual'")
        _ensure_column("expenses", "recurrence_months", "INTEGER")

        # Tabela de subprojetos (criada pelo create_all acima se não existir)
        # _ensure_table redundante, mas deixamos como segurança
        insp = inspect(db.engine)
        if not insp.has_table("subprojects"):
            SubProject.__table__.create(db.engine)
            print("[migrate] tabela subprojects criada")

        # Tabela de investimentos
        if not insp.has_table("investments"):
            Investment.__table__.create(db.engine)
            print("[migrate] tabela investments criada")

        # Cria admin apenas se não existir
        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
        if not User.query.filter_by(username=admin_username).first():
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
            print(f"[bootstrap] admin '{admin_username}' já existe — dados preservados.")


bootstrap()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
