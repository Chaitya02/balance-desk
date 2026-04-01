import json
from datetime import date
from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, session)
from models import db, Expense
from utils import login_required

expenses_bp = Blueprint('expenses', __name__)

# ------------------------------------------------------------------ #
# Default option lists (users can type their own values too)          #
# ------------------------------------------------------------------ #
DEFAULT_CATEGORIES = [
    'Eating Out', 'Grocery', 'Travel', 'Shopping', 'Bills',
]
DEFAULT_MODES = ['Friend', 'Cash']

MONTH_NAMES = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
]


def _user_options(field):
    """Return distinct values the current user has already used for a field."""
    rows = (db.session.query(getattr(Expense, field))
            .filter_by(user_id=session['user_id'])
            .distinct()
            .all())
    return [r[0] for r in rows if r[0]]


# ------------------------------------------------------------------ #
# /expenses  — list view                                              #
# ------------------------------------------------------------------ #

@expenses_bp.route('/expenses')
@login_required
def list_expenses():
    user_id = session['user_id']
    today   = date.today()

    # Month / year filter — default to current month
    try:
        year  = int(request.args.get('year',  today.year))
        month = int(request.args.get('month', today.month))
        if not (1 <= month <= 12):
            month = today.month
    except (ValueError, TypeError):
        year, month = today.year, today.month

    cat_filter  = request.args.get('category', '')
    mode_filter = request.args.get('mode', '')

    # Base query filtered to selected month
    q = (Expense.query
         .filter_by(user_id=user_id)
         .filter(db.extract('year',  Expense.date) == year)
         .filter(db.extract('month', Expense.date) == month)
         .order_by(Expense.date.desc(), Expense.id.desc()))

    if cat_filter:
        q = q.filter_by(category=cat_filter)
    if mode_filter:
        q = q.filter_by(mode=mode_filter)

    expenses = q.all()

    # Summary stats
    total_spent = sum(e.amount for e in expenses)
    you_owe     = sum(e.split for e in expenses if e.mode.upper() == 'FRIEND' and e.split)
    friend_owes = sum(e.split for e in expenses if e.mode.upper() != 'FRIEND' and e.split)

    # Chart data
    cat_totals  = {}
    mode_totals = {}
    for e in expenses:
        cat_totals[e.category] = round(cat_totals.get(e.category, 0) + e.amount, 2)
        key = e.mode.strip().title() if e.mode else 'Other'
        mode_totals[key] = round(mode_totals.get(key, 0) + e.amount, 2)

    top_category = max(cat_totals, key=cat_totals.get) if cat_totals else '—'

    summary = dict(
        total_spent=total_spent,
        transactions=len(expenses),
        top_category=top_category,
        you_owe=you_owe,
        friend_owes=friend_owes,
    )

    # Available years for the year dropdown
    year_rows = (db.session.query(db.extract('year', Expense.date))
                 .filter_by(user_id=user_id)
                 .distinct()
                 .all())
    available_years = sorted({int(r[0]) for r in year_rows} | {today.year}, reverse=True)

    categories = sorted(set(DEFAULT_CATEGORIES) | set(_user_options('category')))
    modes      = sorted(set(DEFAULT_MODES)       | set(_user_options('mode')))

    return render_template(
        'expenses.html',
        expenses=expenses,
        summary=summary,
        categories=categories,
        modes=modes,
        cat_filter=cat_filter,
        mode_filter=mode_filter,
        year=year,
        month=month,
        month_name=MONTH_NAMES[month - 1],
        available_years=available_years,
        month_names=MONTH_NAMES,
        cat_totals_json=json.dumps(cat_totals),
        mode_totals_json=json.dumps(mode_totals),
        you_owe=you_owe,
        friend_owes=friend_owes,
    )


# ------------------------------------------------------------------ #
# /add-expense  — create view                                         #
# ------------------------------------------------------------------ #

@expenses_bp.route('/add-expense', methods=['GET', 'POST'])
@login_required
def add_expense():
    user_id    = session['user_id']
    categories = sorted(set(DEFAULT_CATEGORIES) | set(_user_options('category')))
    modes      = sorted(set(DEFAULT_MODES)       | set(_user_options('mode')))

    if request.method == 'POST':
        exp_date    = request.form.get('date', '').strip()
        title       = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        category    = request.form.get('category', '').strip()
        mode        = request.form.get('mode', '').strip()
        amount_raw  = request.form.get('amount', '').strip()
        split_raw   = request.form.get('split', '0').strip() or '0'

        error = None
        try:
            amount = float(amount_raw)
            split  = float(split_raw)
        except ValueError:
            error = 'Amount and Split must be valid numbers.'

        if not error:
            if not exp_date or not title or not category:
                error = 'Date, Title, and Category are required.'
            elif amount <= 0:
                error = 'Amount must be greater than 0.'
            elif split < 0:
                error = 'Split cannot be negative.'
            elif split > amount:
                error = 'Split cannot exceed the total Amount.'

        if error:
            return render_template('add_expense.html',
                                   error=error,
                                   categories=categories,
                                   modes=modes,
                                   form=request.form)

        expense = Expense(
            user_id=user_id,
            date=date.fromisoformat(exp_date),
            title=title,
            description=description,
            category=category,
            mode=mode,
            amount=amount,
            split=split,
        )
        db.session.add(expense)
        db.session.commit()

        flash(f'Expense "{title}" added successfully.', 'success')

        if request.form.get('next') == 'add':
            return redirect(url_for('expenses.add_expense'))
        return redirect(url_for('expenses.list_expenses'))

    return render_template('add_expense.html',
                           categories=categories,
                           modes=modes,
                           form={},
                           today=date.today().isoformat())


# ------------------------------------------------------------------ #
# /expenses/<id>/edit  — edit view                                    #
# ------------------------------------------------------------------ #

@expenses_bp.route('/expenses/<int:expense_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_expense(expense_id):
    user_id = session['user_id']
    expense = Expense.query.filter_by(id=expense_id, user_id=user_id).first_or_404()

    categories = sorted(set(DEFAULT_CATEGORIES) | set(_user_options('category')))
    modes      = sorted(set(DEFAULT_MODES)       | set(_user_options('mode')))

    if request.method == 'POST':
        exp_date    = request.form.get('date', '').strip()
        title       = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        category    = request.form.get('category', '').strip()
        mode        = request.form.get('mode', '').strip()
        amount_raw  = request.form.get('amount', '').strip()
        split_raw   = request.form.get('split', '0').strip() or '0'

        error = None
        try:
            amount = float(amount_raw)
            split  = float(split_raw)
        except ValueError:
            error = 'Amount and Split must be valid numbers.'

        if not error:
            if not exp_date or not title or not category:
                error = 'Date, Title, and Category are required.'
            elif amount <= 0:
                error = 'Amount must be greater than 0.'
            elif split < 0:
                error = 'Split cannot be negative.'
            elif split > amount:
                error = 'Split cannot exceed the total Amount.'

        if error:
            return render_template('edit_expense.html',
                                   error=error,
                                   expense=expense,
                                   categories=categories,
                                   modes=modes,
                                   form=request.form)

        expense.date        = date.fromisoformat(exp_date)
        expense.title       = title
        expense.description = description
        expense.category    = category
        expense.mode        = mode
        expense.amount      = amount
        expense.split       = split
        db.session.commit()

        flash(f'Expense "{title}" updated successfully.', 'success')
        return redirect(url_for('expenses.list_expenses',
                                year=expense.date.year,
                                month=expense.date.month))

    return render_template('edit_expense.html',
                           expense=expense,
                           categories=categories,
                           modes=modes,
                           form={},
                           error=None)


# ------------------------------------------------------------------ #
# /expenses/<id>/delete  — delete                                     #
# ------------------------------------------------------------------ #

@expenses_bp.route('/expenses/<int:expense_id>/delete', methods=['POST'])
@login_required
def delete_expense(expense_id):
    user_id = session['user_id']
    expense = Expense.query.filter_by(id=expense_id, user_id=user_id).first_or_404()
    title      = expense.title
    exp_year   = expense.date.year
    exp_month  = expense.date.month
    db.session.delete(expense)
    db.session.commit()
    flash(f'Expense "{title}" deleted.', 'success')
    return redirect(url_for('expenses.list_expenses',
                            year=exp_year, month=exp_month))
