"""Seed the demo account used for the "How It Works" screencast.

Creates (or resets) a throwaway user with realistic-looking expenses so the
recording never shows anyone's real spending. Idempotent — rerun freely.

    .venv/bin/python scripts/seed_demo.py
"""
import os
import random
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash

from app import app
from models import db, User, Expense, ROLE_USER

DEMO_EMAIL    = 'demo@balancedesk.local'
DEMO_PASSWORD = 'DemoDesk!2026'
DEMO_NAME     = 'Alex Morgan'

# (title, category, mode, low, high) — amounts sampled per occurrence so each
# run looks natural without being wildly different from the last.
TEMPLATES = [
    ('Morning coffee',      'Eating Out',     'Card',  4,   7),
    ('Team lunch',          'Eating Out',     'Card',  18,  34),
    ('Dinner with friends', 'Eating Out',     'Card',  45,  85),
    ('Weekly groceries',    'Groceries',      'Card',  60,  120),
    ('Farmers market',      'Groceries',      'Cash',  18,  40),
    ('Metro card top-up',   'Transport',      'Card',  20,  40),
    ('Rideshare home',      'Transport',      'Card',  12,  26),
    ('Electricity bill',    'Utilities',      'Card',  55,  95),
    ('Internet',            'Utilities',      'Card',  60,  60),
    ('Phone plan',          'Utilities',      'Card',  35,  35),
    ('Running shoes',       'Shopping',       'Card',  70,  130),
    ('Winter jacket',       'Shopping',       'Card',  90,  160),
    ('Cinema tickets',      'Entertainment',  'Card',  15,  32),
    ('Concert tickets',     'Entertainment',  'Card',  55,  95),
    ('Gym membership',      'Health',         'Card',  40,  40),
    ('Pharmacy',            'Health',         'Cash',  12,  28),
    ('Streaming bundle',    'Subscriptions',  'Card',  16,  16),
    ('Cloud storage',       'Subscriptions',  'Card',  3,   10),
    ('Weekend trip',        'Travel',         'Card',  120, 240),
    ('Haircut',             'Personal Care',  'Cash',  25,  45),
    ('Birthday gift',       'Gifts',          'Card',  30,  60),
    ('Online course',       'Education',      'Card',  40,  90),
]

# Shown on camera: a bill someone else covered, and one the demo user split.
SHARED = [
    # (title, category, payer/mode, amount, split, paid_by_user)
    ('Dinner with friends', 'Eating Out',    'Card',   92.40, 46.20, True),
    ('Concert tickets',     'Entertainment', 'Priya',  110.00, 55.00, False),
    ('Weekend trip',        'Travel',        'Card',   186.00, 62.00, True),
    ('Weekly groceries',    'Groceries',     'Jordan', 78.50,  39.25, False),
]


def _month_start(offset):
    """First day of the month `offset` months before today."""
    today = date.today()
    month = today.month - offset
    year  = today.year
    while month < 1:
        month += 12
        year  -= 1
    return date(year, month, 1)


def _days_in(year, month):
    from calendar import monthrange
    return monthrange(year, month)[1]


def seed():
    rng = random.Random(20260722)   # fixed seed → reproducible takes

    with app.app_context():
        user = User.query.filter_by(email=DEMO_EMAIL).first()
        if user is None:
            user = User(name=DEMO_NAME, email=DEMO_EMAIL)
            db.session.add(user)

        user.name          = DEMO_NAME
        user.password_hash = generate_password_hash(DEMO_PASSWORD)
        user.is_verified   = True
        user.role          = ROLE_USER
        user.currency      = 'USD'
        user.google_id     = None
        user.avatar_url    = None
        if user.created_at is None:
            user.created_at = datetime.now(timezone.utc)
        db.session.flush()

        # Wipe previous run so the numbers on screen stay stable
        Expense.query.filter_by(user_id=user.id).delete()

        rows = []
        today = date.today()

        # Three months of ordinary spending, current month last
        for offset in (2, 1, 0):
            start = _month_start(offset)
            last  = today.day if offset == 0 else _days_in(start.year, start.month)
            # A lighter current month reads as "in progress"
            count = 8 if offset == 0 else 13

            for _ in range(count):
                title, category, mode, low, high = rng.choice(TEMPLATES)
                amount = round(rng.uniform(low, high), 2)
                day    = rng.randint(1, max(last, 1))
                rows.append(Expense(
                    user_id=user.id,
                    date=date(start.year, start.month, day),
                    title=title,
                    description='',
                    category=category,
                    mode=mode,
                    amount=amount,
                    split=None,
                    paid_by_user=True,
                ))

        # Shared bills land in the current month so the Expenses list shows the
        # "you owe" / "owed to you" strips during the recording.
        for i, (title, category, mode, amount, split, paid_by_user) in enumerate(SHARED):
            day = max(today.day - (i * 3 + 2), 1)
            rows.append(Expense(
                user_id=user.id,
                date=date(today.year, today.month, day),
                title=title,
                description='',
                category=category,
                mode=mode,
                amount=amount,
                split=split,
                paid_by_user=paid_by_user,
            ))

        db.session.add_all(rows)
        db.session.commit()

        print(f'Demo user : {DEMO_EMAIL} (id={user.id})')
        print(f'Password  : {DEMO_PASSWORD}')
        print(f'Expenses  : {len(rows)} rows across 3 months')
        this_month = [r for r in rows if r.date.month == today.month and r.date.year == today.year]
        print(f'This month: {len(this_month)} rows, '
              f'{sum(1 for r in this_month if r.split is not None)} shared')


if __name__ == '__main__':
    seed()
