import json
import os
from collections import defaultdict
from datetime import datetime
from flask import Blueprint, render_template, session, jsonify, abort
from models import db, Expense
from utils import login_required
from routes.expenses import DEFAULT_PAYMENT_METHODS, MONTH_NAMES
from insights import (my_spend, category_breakdown, spending_insight,
                      detect_recurring, quote_of_the_day)

main_bp = Blueprint('main', __name__)

BLOG_POSTS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'blog_posts.json')


def _load_blog_posts():
    with open(BLOG_POSTS_PATH, encoding='utf-8') as f:
        posts = json.load(f)
    return sorted(posts, key=lambda p: p['date'], reverse=True)


@main_bp.route('/')
def landing():
    return render_template('landing.html', now=datetime.now())


def _month_rows(user_id, year, month):
    return (Expense.query
            .filter_by(user_id=user_id)
            .filter(db.extract('year',  Expense.date) == year)
            .filter(db.extract('month', Expense.date) == month)
            .order_by(Expense.date.desc(), Expense.id.desc())
            .all())


def _month_change(user_id, now, total_month):
    """(% change vs the previous month, short previous-month name)."""
    prev_m = now.month - 1 if now.month > 1 else 12
    prev_y = now.year      if now.month > 1 else now.year - 1
    prev_total = round(sum(my_spend(e) for e in _month_rows(user_id, prev_y, prev_m)), 2)
    pct = round((total_month - prev_total) / prev_total * 100, 1) if prev_total > 0 else None
    return pct, MONTH_NAMES[prev_m - 1][:3]


def _greeting(hour):
    """Server-side default; dashboard.html re-derives it from the browser clock
    because the server may well be running in UTC."""
    if hour < 12:
        return 'Good morning'
    if hour < 17:
        return 'Good afternoon'
    return 'Good evening'


def _year_average(user_id, year):
    """Average spend across the months of `year` that had any activity."""
    rows = (Expense.query
            .filter_by(user_id=user_id)
            .filter(db.extract('year', Expense.date) == year)
            .all())
    monthly = defaultdict(float)
    for e in rows:
        monthly[e.date.month] += my_spend(e)
    active = [v for v in monthly.values() if v > 0]
    total  = round(sum(active), 2)
    return (round(total / len(active), 2) if active else 0), total


@main_bp.route('/dashboard')
@login_required
def dashboard():
    """Command centre: standing balances, this month's spend, recent + upcoming."""
    user_id = session['user_id']
    now     = datetime.now()

    month_expenses = _month_rows(user_id, now.year, now.month)

    total_month  = round(sum(my_spend(e) for e in month_expenses), 2)
    transactions = len(month_expenses)
    friend_owes  = round(sum(
        e.amount - e.split
        for e in month_expenses
        if e.paid_by_user and e.split is not None and e.split < e.amount
    ), 2)
    you_owe = round(sum(my_spend(e) for e in month_expenses
                        if not e.paid_by_user and e.split is not None), 2)

    cat_breakdown = category_breakdown(month_expenses)
    top_category  = cat_breakdown[0] if cat_breakdown else None

    avg_month, _ = _year_average(user_id, now.year)
    month_change_pct, prev_month_name = _month_change(user_id, now, total_month)

    recent = (Expense.query
              .filter_by(user_id=user_id)
              .order_by(Expense.date.desc(), Expense.id.desc())
              .limit(5).all())

    quote_text, quote_author = quote_of_the_day(now)

    return render_template('dashboard.html',
                           total_month=total_month,
                           transactions=transactions,
                           top_category=top_category,
                           cat_breakdown=cat_breakdown,
                           friend_owes=friend_owes,
                           you_owe=you_owe,
                           month_name=now.strftime('%B'),
                           year=now.year,
                           avg_month=avg_month,
                           month_change_pct=month_change_pct,
                           prev_month_name=prev_month_name,
                           recent=recent,
                           upcoming=detect_recurring(user_id, now),
                           insight=spending_insight(user_id, now),
                           greeting=_greeting(now.hour),
                           quote_text=quote_text,
                           quote_author=quote_author)


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


# ------------------------------------------------------------------ #
# App tabs (Overview / Expenses live above; these three are stubs)      #
# ------------------------------------------------------------------ #

@main_bp.route('/analysis')
@login_required
def analysis():
    """Yearly trend chart and the month's full expense sheet.

    Both moved here off the dashboard, which is now a scannable command centre.
    """
    user_id = session['user_id']
    now     = datetime.now()

    month_expenses = _month_rows(user_id, now.year, now.month)
    total_month    = round(sum(my_spend(e) for e in month_expenses), 2)

    year_expenses = (Expense.query
                     .filter_by(user_id=user_id)
                     .filter(db.extract('year', Expense.date) == now.year)
                     .all())

    monthly_cat = defaultdict(lambda: defaultdict(float))
    chart_cats  = set()
    for e in year_expenses:
        monthly_cat[e.date.month][e.category] += my_spend(e)
        chart_cats.add(e.category)

    chart_cats     = sorted(chart_cats)
    monthly_totals = [round(sum(monthly_cat[m].values()), 2) for m in range(1, 13)]
    total_year     = round(sum(monthly_totals), 2)
    active_months  = sum(1 for t in monthly_totals if t > 0)
    avg_month      = round(total_year / active_months, 2) if active_months else 0

    month_change_pct, prev_month_name = _month_change(user_id, now, total_month)

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

    return render_template('analysis.html',
                           month_expenses=month_expenses,
                           month_name=now.strftime('%B'),
                           year=now.year,
                           total_year=total_year,
                           avg_month=avg_month,
                           month_change_pct=month_change_pct,
                           prev_month_name=prev_month_name,
                           min_year=first.year if first else now.year,
                           chart_data=json.dumps(chart_data))


@main_bp.route('/messages')
@login_required
def messages():
    """Dex as a full-page conversation — shell only for now."""
    return render_template('messages.html')


@main_bp.route('/calculate')
@login_required
def calculate():
    """Split calculator — shell only for now."""
    return render_template('calculate.html')


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
    return render_template('blog.html', posts=_load_blog_posts())


@main_bp.route('/blog/<slug>')
def blog_post(slug):
    post = next((p for p in _load_blog_posts() if p['slug'] == slug), None)
    if post is None:
        abort(404)
    return render_template('blog_post.html', post=post)


@main_bp.route('/contact')
def contact():
    return render_template('contact.html')


