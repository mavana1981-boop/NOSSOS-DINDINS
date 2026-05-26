from app import create_app, db
from app.models import User
from werkzeug.security import generate_password_hash
import os

app = create_app()


def bootstrap_admin():
    """Cria usuário admin na primeira execução."""
    with app.app_context():
        db.create_all()
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
