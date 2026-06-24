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
    photo = db.Column(db.Text, nullable=True)  # base64 data URI — persiste no Postgres
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    incomes = db.relationship("Income", backref="user", lazy="dynamic", cascade="all, delete-orphan")
    expenses = db.relationship("Expense", backref="payer", lazy="dynamic",
                               foreign_keys="Expense.payer_id", cascade="all, delete-orphan")

    @property
    def photo_url(self):
        if self.photo:
            return self.photo  # já é data URI: "data:image/jpeg;base64,..."
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
    __tablename__ = "expenses"
    id = db.Column(db.Integer, primary_key=True)
    payer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    description = db.Column(db.String(160), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    spent_at = db.Column(db.Date, nullable=False, default=date.today)
    category = db.Column(db.String(60), default="Outros")
    notes = db.Column(db.Text)
    share_mode = db.Column(db.String(20), default="solo")
    kind = db.Column(db.String(20), default="pontual")
    recurrence_months = db.Column(db.Integer, nullable=True)
    card_id = db.Column(db.Integer, db.ForeignKey("cards.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    shares = db.relationship("ExpenseShare", backref="expense", lazy="joined",
                             cascade="all, delete-orphan")
    card = db.relationship("Card", foreign_keys=[card_id], backref="linked_expenses")

    def is_active_on(self, year, month):
        if self.kind == "pontual":
            return self.spent_at.year == year and self.spent_at.month == month
        start = self.spent_at
        target_first = date(year, month, 1)
        if target_first < date(start.year, start.month, 1):
            return False
        months_diff = (year - start.year) * 12 + (month - start.month)
        if self.recurrence_months is None:
            return True
        return 0 <= months_diff < self.recurrence_months

    def parcel_label(self, year, month):
        if self.kind != "recorrente" or self.recurrence_months is None:
            return None
        months_diff = (year - self.spent_at.year) * 12 + (month - self.spent_at.month)
        return f"{months_diff + 1}/{self.recurrence_months}"


class ExpenseShare(db.Model):
    __tablename__ = "expense_shares"
    id = db.Column(db.Integer, primary_key=True)
    expense_id = db.Column(db.Integer, db.ForeignKey("expenses.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    share_amount = db.Column(db.Numeric(12, 2), nullable=False)
    share_percent = db.Column(db.Numeric(5, 2))

    user = db.relationship("User", backref="expense_shares")


class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    target_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    deadline = db.Column(db.Date, nullable=True)
    monthly_auto = db.Column(db.Numeric(12, 2), default=0)
    auto_day = db.Column(db.Integer, default=1)
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
        if not self.project:
            return 0
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
    monthly_share = db.Column(db.Numeric(12, 2), default=0)
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
    __tablename__ = "auto_transfers"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    executed_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("project_id", "year", "month"),)


class Investment(db.Model):
    __tablename__ = "investments"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    description = db.Column(db.String(160), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    current_value = db.Column(db.Numeric(12, 2), nullable=True)
    invested_at = db.Column(db.Date, nullable=False, default=date.today)
    category = db.Column(db.String(60), default="Renda Fixa")
    objective = db.Column(db.String(120), nullable=False)
    institution = db.Column(db.String(120))
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


class Card(db.Model):
    """Cartão de crédito/débito do usuário."""
    __tablename__ = "cards"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)        # ex: Nubank Visa
    last_digits = db.Column(db.String(4))                   # 4 últimos dígitos
    limit_amount = db.Column(db.Numeric(12, 2), default=0)  # limite do cartão
    closing_day = db.Column(db.Integer)                     # dia fechamento
    due_day = db.Column(db.Integer)                         # dia vencimento
    color = db.Column(db.String(20), default="#6b8db5")     # cor do card
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="cards")
    entries = db.relationship("CardEntry", backref="card", lazy="dynamic",
                              cascade="all, delete-orphan")

    @property
    def total_entries(self):
        try:
            from sqlalchemy import func as _func
            result = db.session.query(
                _func.coalesce(_func.sum(CardEntry.amount), 0)
            ).filter(CardEntry.card_id == self.id).scalar()
            return float(result or 0)
        except Exception:
            return 0.0

    @property
    def available(self):
        return max(float(self.limit_amount or 0) - self.total_entries, 0)

    @property
    def usage_percent(self):
        lim = float(self.limit_amount or 0)
        if lim <= 0:
            return 0
        return min(round(self.total_entries / lim * 100, 1), 100)


class CardEntry(db.Model):
    """Lançamento em um cartão, podendo ser vinculado a um gasto fixo existente."""
    __tablename__ = "card_entries"
    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey("cards.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    description = db.Column(db.String(160), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    entry_date = db.Column(db.Date, nullable=False, default=date.today)
    # Vínculo com gasto fixo existente (opcional)
    expense_id = db.Column(db.Integer, db.ForeignKey("expenses.id"), nullable=True)
    category = db.Column(db.String(60), default="Outros")
    kind = db.Column(db.String(20), default="pontual")  # pontual / recorrente / parcelado
    installments = db.Column(db.Integer, default=1)   # número de parcelas (quando parcelado)
    installment_no = db.Column(db.Integer, default=1) # parcela atual
    status = db.Column(db.String(20), default="ativo")  # ativo / em_avaliacao
    batch_id = db.Column(db.String(64), nullable=True)  # ID do lote de importação
    billing_month = db.Column(db.String(7), nullable=True)  # YYYY-MM do mês de faturamento
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="card_entries")
    expense = db.relationship("Expense", backref="card_entries")


class HouseholdExpense(db.Model):
    """Marca um gasto como 'da casa' e define com quem é compartilhado."""
    __tablename__ = "household_expenses"
    id = db.Column(db.Integer, primary_key=True)
    expense_id = db.Column(db.Integer, db.ForeignKey("expenses.id"), nullable=False, unique=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    shared_with_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    expense = db.relationship("Expense", backref=db.backref("household", uselist=False))
    owner = db.relationship("User", foreign_keys=[owner_id], backref="household_owned")
    shared_with = db.relationship("User", foreign_keys=[shared_with_id], backref="household_shared")


class CashflowOverride(db.Model):
    """Ajuste manual de valores no fluxo de caixa por mês."""
    __tablename__ = "cashflow_overrides"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    net_override = db.Column(db.Numeric(12, 2), nullable=True)
    cumulative_override = db.Column(db.Numeric(12, 2), nullable=True)
    income_recurring_override = db.Column(db.Numeric(12, 2), nullable=True)
    income_eventual_override = db.Column(db.Numeric(12, 2), nullable=True)
    fixed_override = db.Column(db.Numeric(12, 2), nullable=True)
    eventual_override = db.Column(db.Numeric(12, 2), nullable=True)

    user = db.relationship("User", backref="cashflow_overrides")

    __table_args__ = (db.UniqueConstraint("user_id", "year", "month"),)


class CardMonthHistory(db.Model):
    """Histórico mensal de gastos por cartão."""
    __tablename__ = "card_month_history"
    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    card_id       = db.Column(db.Integer, db.ForeignKey("cards.id"), nullable=True)
    billing_month = db.Column(db.String(7), nullable=False)
    snapshot      = db.Column(db.Text, nullable=True)
    total_geral   = db.Column(db.Numeric(12, 2), default=0)
    entry_count   = db.Column(db.Integer, default=0)
    created_at    = db.Column(db.DateTime, default=db.func.now())

    user = db.relationship("User", backref="card_histories")
    card = db.relationship("Card", backref="month_histories")
    __table_args__ = (db.UniqueConstraint("user_id", "card_id", "billing_month"),)


class MerchantRule(db.Model):
    """Regras de categorização automática por nome de estabelecimento."""
    __tablename__ = "merchant_rules"
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    keyword    = db.Column(db.String(120), nullable=False)   # palavra-chave do nome
    category   = db.Column(db.String(80),  nullable=False)
    expense_id = db.Column(db.Integer, db.ForeignKey("expenses.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.now())

    user    = db.relationship("User",    backref="merchant_rules")
    expense = db.relationship("Expense", backref="merchant_rules")
    __table_args__ = (db.UniqueConstraint("user_id", "keyword"),)


class PaymentPlan(db.Model):
    """Plano de gerenciamento de pagamentos por mês."""
    __tablename__ = "payment_plans"
    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    mes_ref       = db.Column(db.String(7), nullable=False, default="")  # ex: "2026-06"
    saldo_inicial = db.Column(db.Numeric(12, 2), default=0)
    updated_at    = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())
    user  = db.relationship("User", backref="payment_plans")
    items = db.relationship("PaymentItem", backref="plan", cascade="all, delete-orphan")
    __table_args__ = (db.UniqueConstraint("user_id", "mes_ref"),)


class PaymentItem(db.Model):
    """Item de gasto no plano de pagamento."""
    __tablename__ = "payment_items"
    id          = db.Column(db.Integer, primary_key=True)
    plan_id     = db.Column(db.Integer, db.ForeignKey("payment_plans.id"), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    amount      = db.Column(db.Numeric(12, 2), nullable=False)
    expense_id  = db.Column(db.Integer, db.ForeignKey("expenses.id"), nullable=True)
    is_paid     = db.Column(db.Boolean, default=False)
    due_date    = db.Column(db.Date, nullable=True)
    created_at  = db.Column(db.DateTime, default=db.func.now())
    expense = db.relationship("Expense", backref="payment_items")


class PaymentCardStatus(db.Model):
    """Status de pagamento de fatura de cartão por mês."""
    __tablename__ = "payment_card_status"
    id              = db.Column(db.Integer, primary_key=True)
    plan_id         = db.Column(db.Integer, db.ForeignKey("payment_plans.id"), nullable=False)
    card_id         = db.Column(db.Integer, db.ForeignKey("cards.id"), nullable=False)
    is_paid         = db.Column(db.Boolean, default=False)
    due_date        = db.Column(db.Date, nullable=True)
    amount_override = db.Column(db.Numeric(12, 2), nullable=True)  # valor manual
    __table_args__ = (db.UniqueConstraint("plan_id", "card_id"),)
    card = db.relationship("Card", backref="payment_status")
