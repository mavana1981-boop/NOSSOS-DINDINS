class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    photo = db.Column(db.Text, nullable=True)  # base64 data URI
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
