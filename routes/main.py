import json
from collections import defaultdict
from datetime import datetime
from flask import Blueprint, render_template, session, jsonify
from models import db, Expense
from utils import login_required
from routes.expenses import DEFAULT_PAYMENT_METHODS, MONTH_NAMES

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
        return e.amount if e.split is None else e.split

    total_month  = round(sum(my_spend(e) for e in month_expenses), 2)
    transactions = len(month_expenses)
    friend_owes  = round(sum(
        e.amount - e.split
        for e in month_expenses
        if e.paid_by_user and e.split is not None and e.split < e.amount
    ), 2)
    you_owe      = round(sum(my_spend(e) for e in month_expenses if not e.paid_by_user and e.split is not None), 2)

    cat_totals = {}
    for e in month_expenses:
        cat_totals[e.category] = cat_totals.get(e.category, 0) + my_spend(e)
    top_category = max(cat_totals, key=cat_totals.get) if cat_totals else None

    # Yearly data for stacked bar chart
    year_expenses = (Expense.query
                     .filter_by(user_id=user_id)
                     .filter(db.extract('year', Expense.date) == now.year)
                     .all())

    monthly_cat = defaultdict(lambda: defaultdict(float))
    chart_cats = set()
    for e in year_expenses:
        m = e.date.month
        cat = e.category
        amt = e.amount if e.split is None else e.split
        monthly_cat[m][cat] += amt
        chart_cats.add(cat)

    chart_cats = sorted(chart_cats)
    monthly_totals = [round(sum(monthly_cat[m].values()), 2) for m in range(1, 13)]
    total_year     = round(sum(monthly_totals), 2)
    active_months  = sum(1 for t in monthly_totals if t > 0)
    avg_month      = round(total_year / active_months, 2) if active_months else 0

    # Previous month for % change
    prev_m = now.month - 1 if now.month > 1 else 12
    prev_y = now.year    if now.month > 1 else now.year - 1
    prev_expenses = (Expense.query
                     .filter_by(user_id=user_id)
                     .filter(db.extract('year',  Expense.date) == prev_y)
                     .filter(db.extract('month', Expense.date) == prev_m)
                     .all())
    prev_total = round(sum(e.amount if e.split is None else e.split
                           for e in prev_expenses), 2)
    if prev_total > 0:
        month_change_pct = round((total_month - prev_total) / prev_total * 100, 1)
    else:
        month_change_pct = None
    prev_month_name = MONTH_NAMES[prev_m - 1][:3]

    chart_data = {
        'labels':         [m[:3] for m in MONTH_NAMES],
        'categories':     chart_cats,
        'datasets':       [
            [round(monthly_cat[m].get(cat, 0), 2) for m in range(1, 13)]
            for cat in chart_cats
        ],
        'monthly_totals': monthly_totals,
    }

    first = (db.session.query(db.func.min(Expense.date))
             .filter_by(user_id=user_id).scalar())
    min_year = first.year if first else now.year

    return render_template('dashboard.html',
                           total_month=total_month,
                           transactions=transactions,
                           top_category=top_category,
                           friend_owes=friend_owes,
                           you_owe=you_owe,
                           month_expenses=month_expenses,
                           month_name=now.strftime('%B'),
                           year=now.year,
                           total_year=total_year,
                           avg_month=avg_month,
                           month_change_pct=month_change_pct,
                           prev_month_name=prev_month_name,
                           payment_methods_lc=[m.lower() for m in DEFAULT_PAYMENT_METHODS],
                           min_year=min_year,
                           chart_data=json.dumps(chart_data))


@main_bp.route('/api/chart-data/<int:year>')
@login_required
def chart_data_api(year):
    user_id = session['user_id']
    year_expenses = (Expense.query
                     .filter_by(user_id=user_id)
                     .filter(db.extract('year', Expense.date) == year)
                     .all())

    monthly_cat = defaultdict(lambda: defaultdict(float))
    chart_cats = set()
    for e in year_expenses:
        monthly_cat[e.date.month][e.category] += e.amount if e.split is None else e.split
        chart_cats.add(e.category)

    chart_cats     = sorted(chart_cats)
    monthly_totals = [round(sum(monthly_cat[m].values()), 2) for m in range(1, 13)]
    total_year     = round(sum(monthly_totals), 2)
    active_months  = sum(1 for t in monthly_totals if t > 0)
    avg_month      = round(total_year / active_months, 2) if active_months else 0

    return jsonify({
        'year':           year,
        'labels':         [m[:3] for m in MONTH_NAMES],
        'categories':     chart_cats,
        'datasets':       [
            [round(monthly_cat[m].get(cat, 0), 2) for m in range(1, 13)]
            for cat in chart_cats
        ],
        'monthly_totals': monthly_totals,
        'total_year':     total_year,
        'avg_month':      avg_month,
    })


@main_bp.route('/terms')
def terms():
    return render_template('terms.html')


@main_bp.route('/privacy')
def privacy():
    return render_template('privacy.html')


@main_bp.route('/dex-story')
def dex_story():
    return render_template('dex_story.html')


@main_bp.route('/about')
def about():
    return render_template('about.html')


@main_bp.route('/blog')
def blog():
    return render_template('blog.html')


@main_bp.route('/contact')
def contact():
    return render_template('contact.html')


