from app import create_app, db
from app.models import User, SubProject, Investment
from werkzeug.security import generate_password_hash
from sqlalchemy import text, inspect
import os

app = create_app()


def _ensure_column(table, column, ddl):
    """Adiciona coluna se não existir — idempotente."""
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
    """Inicializa banco e admin. Seguro para rodar a cada deploy."""
    with app.app_context():
        try:
            db.create_all()
            print("[bootstrap] tabelas verificadas/criadas.")
        except Exception as e:
            print(f"[bootstrap] erro no create_all: {e}")
            return

        _ensure_column("expenses", "kind",              "VARCHAR(20) DEFAULT 'pontual'")
        _ensure_column("expenses", "recurrence_months", "INTEGER")

        try:
            insp = inspect(db.engine)
            if not insp.has_table("subprojects"):
                SubProject.__table__.create(db.engine)
                print("[migrate] tabela subprojects criada")
            if not insp.has_table("investments"):
                Investment.__table__.create(db.engine)
                print("[migrate] tabela investments criada")
        except Exception as e:
            print(f"[migrate] erro criando tabelas extras: {e}")

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
                # Se RESET_ADMIN_PASSWORD estiver definida, reseta a senha
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
