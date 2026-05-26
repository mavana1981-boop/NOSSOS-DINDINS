from datetime import datetime, date
from flask_login import UserMixin
from sqlalchemy import func
from app import db


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    photo = db.Column(db.String(255), nullable=True)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    incomes = db.relationship("Income", backref="user", lazy="dynamic", cascade="all, delete-orphan")
    expenses = db.relationship("Expense", backref="payer", lazy="dynamic",
                               foreign_keys="Expense.payer_id", cascade="all, delete-orphan")

    @property
    def photo_url(self):
        if self.photo:
            return f"/static/uploads/{self.photo}"
        # Default avatar (data URI - tiny SVG)
        return "/static/img/default-avatar.svg"


class Income(db.Model):
    __tablename__ = "incomes"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    description = db.Column(db.String(160), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    received_at = db.Column(db.Date, nullable=False, default=date.today)
    is_recurring = db.Column(db.Boolean, default=False)
    category = db.Column(db.String(60), default="Salário")
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Expense(db.Model):
    """
    Gasto principal. payer_id é quem pagou (cartão dele).
    Os 'shares' definem quem realmente deve o quê.
    """
    __tablename__ = "expenses"
    id = db.Column(db.Integer, primary_key=True)
    payer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    description = db.Column(db.String(160), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    spent_at = db.Column(db.Date, nullable=False, default=date.today)
    category = db.Column(db.String(60), default="Outros")
    notes = db.Column(db.Text)
    # 'integral' = repasse total para outro / 'split' = dividido com percentuais
    share_mode = db.Column(db.String(20), default="solo")  # solo | integral | split
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    shares = db.relationship("ExpenseShare", backref="expense", lazy="joined",
                             cascade="all, delete-orphan")


class ExpenseShare(db.Model):
    """
    Define a participação de cada usuário num gasto.
    Para um gasto solo: 1 linha com o pagador (share_amount = total).
    Para integral: 1 linha com o devedor (share_amount = total).
    Para split: N linhas, somando o total.
    """
    __tablename__ = "expense_shares"
    id = db.Column(db.Integer, primary_key=True)
    expense_id = db.Column(db.Integer, db.ForeignKey("expenses.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    share_amount = db.Column(db.Numeric(12, 2), nullable=False)
    share_percent = db.Column(db.Numeric(5, 2))  # informativo

    user = db.relationship("User", backref="expense_shares")


class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    target_amount = db.Column(db.Numeric(12, 2), nullable=False)
    deadline = db.Column(db.Date, nullable=True)
    monthly_auto = db.Column(db.Numeric(12, 2), default=0)  # aporte automático mensal total
    auto_day = db.Column(db.Integer, default=1)  # dia do mês para aporte
    is_completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    owner = db.relationship("User", foreign_keys=[owner_id], backref="owned_projects")
    members = db.relationship("ProjectMember", backref="project", lazy="joined",
                              cascade="all, delete-orphan")
    contributions = db.relationship("Contribution", backref="project", lazy="dynamic",
                                    cascade="all, delete-orphan")

    @property
    def total_raised(self):
        result = db.session.query(func.coalesce(func.sum(Contribution.amount), 0))\
            .filter_by(project_id=self.id).scalar()
        return float(result or 0)

    @property
    def progress_percent(self):
        target = float(self.target_amount or 0)
        if target <= 0:
            return 0
        pct = (self.total_raised / target) * 100
        return min(round(pct, 1), 100)

    @property
    def remaining(self):
        return max(float(self.target_amount) - self.total_raised, 0)

    def member_ids(self):
        return [m.user_id for m in self.members]


class ProjectMember(db.Model):
    __tablename__ = "project_members"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    monthly_share = db.Column(db.Numeric(12, 2), default=0)  # contribuição automática mensal
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="project_memberships")


class Contribution(db.Model):
    __tablename__ = "contributions"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    contributed_at = db.Column(db.Date, default=date.today)
    note = db.Column(db.String(200))
    is_auto = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="contributions")


class AutoTransfer(db.Model):
    """Histórico de aportes automáticos já executados (controla idempotência)."""
    __tablename__ = "auto_transfers"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    executed_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("project_id", "year", "month"),)
