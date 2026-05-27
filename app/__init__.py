import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from dotenv import load_dotenv

load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()


def create_app():
    app = Flask(__name__)

    # Database URL — Railway injeta DATABASE_URL do plugin Postgres automaticamente.
    # Se não estiver definida, o app recusa subir (evita silenciosamente usar SQLite).
    db_url = os.environ.get("DATABASE_URL", "sqlite:///finapp.db")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB upload max
    app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads")
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Faça login para acessar esta página."
    login_manager.login_message_category = "warning"

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Blueprints
    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.admin import admin_bp
    from app.routes.income import income_bp
    from app.routes.expenses import expenses_bp
    from app.routes.projects import projects_bp
    from app.routes.cashflow import cashflow_bp
    from app.routes.investments import investments_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(income_bp, url_prefix="/rendas")
    app.register_blueprint(expenses_bp, url_prefix="/gastos")
    app.register_blueprint(projects_bp, url_prefix="/projetos")
    app.register_blueprint(cashflow_bp, url_prefix="/fluxo")
    app.register_blueprint(investments_bp, url_prefix="/investimentos")

    # Filtros e context processors
    from app.utils import register_filters, register_context

    register_filters(app)
    register_context(app)

    # Scheduler para aportes automáticos
    from app.scheduler import start_scheduler

    start_scheduler(app)

    return app
