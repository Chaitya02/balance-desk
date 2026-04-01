from datetime import datetime
from flask import Blueprint, render_template, session
from models import db, Expense
from utils import login_required
from routes.expenses import DEFAULT_PAYMENT_METHODS

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def landing():
    return render_template('landing.html', now=datetime.now())


@main_bp.route('/dashboard')
@login_required
def dashboard():
    user_id = session['user_id']
    now     = datetime.now()

    # Month-to-date stats
    month_expenses = (Expense.query
                      .filter_by(user_id=user_id)
                      .filter(db.extract('year',  Expense.date) == now.year)
                      .filter(db.extract('month', Expense.date) == now.month)
                      .all())

    pm_lc = {m.lower() for m in DEFAULT_PAYMENT_METHODS}

    def paid_by_me(e):
        return not e.mode or e.mode.lower() in pm_lc

    def my_spend(e):
        return e.split if e.split else e.amount

    total_month  = round(sum(my_spend(e) for e in month_expenses), 2)
    transactions = len(month_expenses)
    friend_owes  = round(sum(e.amount - e.split
                             for e in month_expenses
                             if paid_by_me(e) and e.split and e.split < e.amount), 2)
    you_owe      = round(sum(my_spend(e) for e in month_expenses if not paid_by_me(e)), 2)

    cat_totals = {}
    for e in month_expenses:
        cat_totals[e.category] = cat_totals.get(e.category, 0) + my_spend(e)
    top_category = max(cat_totals, key=cat_totals.get) if cat_totals else None

    return render_template('dashboard.html',
                           total_month=total_month,
                           transactions=transactions,
                           top_category=top_category,
                           friend_owes=friend_owes,
                           you_owe=you_owe,
                           month_expenses=month_expenses,
                           month_name=now.strftime('%B'),
                           year=now.year,
                           payment_methods_lc=[m.lower() for m in DEFAULT_PAYMENT_METHODS])


@main_bp.route('/terms')
def terms():
    return render_template('terms.html')


@main_bp.route('/privacy')
def privacy():
    return render_template('privacy.html')


