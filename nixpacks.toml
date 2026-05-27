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

    Recorrência:
      - kind='pontual': aparece só em spent_at
      - kind='recorrente': aparece de spent_at por N meses (recurrence_months)
        Se recurrence_months = None: é fixo perpétuo (até excluir)
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
    # Recorrência
    kind = db.Column(db.String(20), default="pontual")  # pontual | recorrente
    recurrence_months = db.Column(db.Integer, nullable=True)  # None = sem fim definido
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    shares = db.relationship("ExpenseShare", backref="expense", lazy="joined",
                             cascade="all, delete-orphan")

    def is_active_on(self, year, month):
        """Verifica se este gasto incide num determinado mês/ano."""
        if self.kind == "pontual":
            return self.spent_at.year == year and self.spent_at.month == month
        # recorrente
        start = self.spent_at
        target_first = date(year, month, 1)
        if target_first < date(start.year, start.month, 1):
            return False
        # meses entre start e target (inclusive ambos)
        months_diff = (year - start.year) * 12 + (month - start.month)
        if self.recurrence_months is None:
            return True  # fixo perpétuo
        return 0 <= months_diff < self.recurrence_months

    def parcel_label(self, year, month):
        """Retorna 'x/N' se for recorrente com fim definido, None caso contrário."""
        if self.kind != "recorrente" or self.recurrence_months is None:
            return None
        months_diff = (year - self.spent_at.year) * 12 + (month - self.spent_at.month)
        return f"{months_diff + 1}/{self.recurrence_months}"


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
    # target_amount agora é fallback caso não haja subprojetos
    target_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
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
    subprojects = db.relationship("SubProject", backref="project", lazy="joined",
                                  cascade="all, delete-orphan",
                                  order_by="SubProject.created_at")

    @property
    def computed_target(self):
        """Meta efetiva: soma dos subprojetos OU target_amount se não houver subs."""
        if self.subprojects:
            return sum(float(s.target_amount or 0) for s in self.subprojects)
        return float(self.target_amount or 0)

    @property
    def total_raised(self):
        result = db.session.query(func.coalesce(func.sum(Contribution.amount), 0))\
            .filter_by(project_id=self.id).scalar()
        return float(result or 0)

    @property
    def progress_percent(self):
        target = self.computed_target
        if target <= 0:
            return 0
        pct = (self.total_raised / target) * 100
        return min(round(pct, 1), 100)

    @property
    def remaining(self):
        return max(self.computed_target - self.total_raised, 0)

    def member_ids(self):
        return [m.user_id for m in self.members]


class SubProject(db.Model):
    """Componente de um projeto. Soma deles = meta do projeto pai."""
    __tablename__ = "subprojects"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    target_amount = db.Column(db.Numeric(12, 2), nullable=False)
    order_index = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def allocated_from_parent(self):
        """Quanto do total arrecadado do projeto pai já cobre este subprojeto.
        Distribuição em ordem: o que arrecadou vai preenchendo os subs em sequência."""
        if not self.project:
            return 0
        # ordena os subs pelo created_at (mesma ordem que está no joined load)
        subs = sorted(self.project.subprojects, key=lambda s: (s.order_index or 0, s.id))
        raised = self.project.total_raised
        cumulative = 0
        for s in subs:
            if s.id == self.id:
                return min(raised - cumulative, float(s.target_amount or 0)) if raised > cumulative else 0
            cumulative += float(s.target_amount or 0)
        return 0

    @property
    def progress_percent(self):
        target = float(self.target_amount or 0)
        if target <= 0:
            return 0
        pct = (self.allocated_from_parent / target) * 100
        return min(round(pct, 1), 100)


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


class Investment(db.Model):
    """Investimento do usuário, categorizado por objetivo."""
    __tablename__ = "investments"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    description = db.Column(db.String(160), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)          # valor aportado
    current_value = db.Column(db.Numeric(12, 2), nullable=True)   # valor atual (atualizado manualmente)
    invested_at = db.Column(db.Date, nullable=False, default=date.today)
    category = db.Column(db.String(60), default="Renda Fixa")     # tipo do ativo
    objective = db.Column(db.String(120), nullable=False)          # objetivo do investimento
    institution = db.Column(db.String(120))                        # corretora/banco
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="investments")

    @property
    def gain_loss(self):
        if self.current_value is None:
            return 0
        return float(self.current_value) - float(self.amount)

    @property
    def gain_loss_pct(self):
        amt = float(self.amount)
        if amt == 0 or self.current_value is None:
            return 0
        return ((float(self.current_value) - amt) / amt) * 100
