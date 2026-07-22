from functools import wraps
from flask import session, redirect, url_for, flash, g


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            flash('Please sign in to access that page.', 'info')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


# ------------------------------------------------------------------ #
# Currency                                                             #
# ------------------------------------------------------------------ #

CURRENCIES = {
    'USD': {'symbol': '$', 'label': 'US Dollar ($)'},
    'INR': {'symbol': '₹', 'label': 'Indian Rupee (₹)'},
}
DEFAULT_CURRENCY = 'USD'


def detect_currency(request):
    """Guess a signup default from the browser's Accept-Language header."""
    accept = request.headers.get('Accept-Language', '').lower()
    if '-in' in accept or accept.startswith('hi'):
        return 'INR'
    return DEFAULT_CURRENCY


def _group_indian(digits):
    """Lakh/crore digit grouping: '1234567' -> '12,34,567'."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts = [tail]
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ','.join(parts)


def format_money(amount, currency=None):
    """Format an amount in the given (or current user's) currency."""
    code = currency or getattr(g, 'user_currency', DEFAULT_CURRENCY)
    symbol = CURRENCIES.get(code, CURRENCIES[DEFAULT_CURRENCY])['symbol']
    value = float(amount or 0)
    sign = '-' if value < 0 else ''
    whole, dec = f'{abs(value):.2f}'.split('.')
    grouped = _group_indian(whole) if code == 'INR' else f'{int(whole):,}'
    return f'{sign}{symbol}{grouped}.{dec}'
