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


def _user_options(field):
    """Return distinct values the current user has already used for a field."""
    rows = (db.session.query(getattr(Expense, field))
            .filter_by(user_id=session['user_id'])
            .distinct()
            .all())
    return [r[0] for r in rows if r[0]]


def _summary(user_id, cat_filter=None, mode_filter=None):
    """Compute summary stats for the current user (with optional filters)."""
    q = Expense.query.filter_by(user_id=user_id)
    if cat_filter:
        q = q.filter_by(category=cat_filter)
    if mode_filter:
        q = q.filter_by(mode=mode_filter)
    expenses = q.all()

    total_spent   = sum(e.amount for e in expenses)
    transactions  = len(expenses)
    you_owe       = sum(e.split for e in expenses if e.mode.upper() == 'FRIEND' and e.split)
    friend_owes   = sum(e.split for e in expenses if e.mode.upper() != 'FRIEND' and e.split)

    # Top category by total amount
    cat_totals = {}
    for e in expenses:
        cat_totals[e.category] = cat_totals.get(e.category, 0) + e.amount
    top_category = max(cat_totals, key=cat_totals.get) if cat_totals else '—'

    return dict(
        total_spent=total_spent,
        transactions=transactions,
        top_category=top_category,
        you_owe=you_owe,
        friend_owes=friend_owes,
    )


# ------------------------------------------------------------------ #
# /expenses  — list view                                              #
# ------------------------------------------------------------------ #

@expenses_bp.route('/expenses')
@login_required
def list_expenses():
    user_id     = session['user_id']
    cat_filter  = request.args.get('category', '')
    mode_filter = request.args.get('mode', '')

    q = Expense.query.filter_by(user_id=user_id).order_by(
        Expense.date.desc(), Expense.id.desc()
    )
    if cat_filter:
        q = q.filter_by(category=cat_filter)
    if mode_filter:
        q = q.filter_by(mode=mode_filter)

    expenses     = q.all()
    summary      = _summary(user_id, cat_filter or None, mode_filter or None)
    categories   = sorted(set(DEFAULT_CATEGORIES) | set(_user_options('category')))
    modes        = sorted(set(DEFAULT_MODES)       | set(_user_options('mode')))

    return render_template('expenses.html',
                           expenses=expenses,
                           summary=summary,
                           categories=categories,
                           modes=modes,
                           cat_filter=cat_filter,
                           mode_filter=mode_filter)


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

        # ---- validation ----
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

        # "Add another" button submits with next=add
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
        return redirect(url_for('expenses.list_expenses'))

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
    title = expense.title
    db.session.delete(expense)
    db.session.commit()
    flash(f'Expense "{title}" deleted.', 'success')
    return redirect(url_for('expenses.list_expenses'))
