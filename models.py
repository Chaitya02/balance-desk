from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = 'users'

    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(100), nullable=False)
    email         = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=True)   # null for OAuth-only accounts
    google_id     = db.Column(db.String(128), unique=True, nullable=True)
    avatar_url         = db.Column(db.String(512), nullable=True)
    is_verified        = db.Column(db.Boolean, default=False, nullable=False, server_default='1')
    verification_token = db.Column(db.String(64), nullable=True)
    created_at         = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    expenses = db.relationship('Expense', backref='user', lazy='dynamic',
                               order_by='Expense.date.desc()')

    def __repr__(self):
        return f'<User {self.email}>'


class Expense(db.Model):
    __tablename__ = 'expenses'

    id          = db.Column(db.Integer, primary_key=True)            # Sr. No.
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    date        = db.Column(db.Date, nullable=False)                 # Date
    title       = db.Column(db.String(200), nullable=False)          # Title
    description = db.Column(db.String(500), default='')              # Description

    category    = db.Column(db.String(100), nullable=False)          # Category
    mode        = db.Column(db.String(100), nullable=False, default='') # Mode

    amount        = db.Column(db.Float, nullable=False)              # Amount
    split         = db.Column(db.Float, nullable=True, default=None) # Split (None = full expense is mine)
    paid_by_user  = db.Column(db.Boolean, nullable=False, default=True, server_default='1')

    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def you_owe(self):
        return self.split if not self.paid_by_user and self.split is not None else 0.0

    @property
    def friend_owes(self):
        return (self.amount - self.split) if self.paid_by_user and self.split is not None and self.split < self.amount else 0.0

    def __repr__(self):
        return f'<Expense {self.title} ${self.amount}>'
