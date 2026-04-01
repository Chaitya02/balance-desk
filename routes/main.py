from datetime import datetime
from flask import Blueprint, render_template, session
from models import db, Expense
from utils import login_required

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

    total_month   = sum(e.amount for e in month_expenses)
    transactions  = len(month_expenses)

    cat_totals = {}
    for e in month_expenses:
        cat_totals[e.category] = cat_totals.get(e.category, 0) + e.amount
    top_category = max(cat_totals, key=cat_totals.get) if cat_totals else None

    return render_template('dashboard.html',
                           total_month=total_month,
                           transactions=transactions,
                           top_category=top_category)


@main_bp.route('/terms')
def terms():
    return render_template('terms.html')


@main_bp.route('/privacy')
def privacy():
    return render_template('privacy.html')


