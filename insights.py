"""Derived read-only views over a user's expenses, for the Overview dashboard.

Pure-ish helpers (queries in, plain dicts out) so the route stays thin and the
maths is testable on its own. Nothing here calls an LLM — the insight sentence
is computed from the numbers, which keeps the dashboard instant and free.
"""
from calendar import monthrange
from collections import defaultdict
from datetime import date

from models import db, Expense


def my_spend(e):
    """My actual share of an expense: the split if one is set, else the full amount.

    The single definition of the rule — routes import this rather than
    redefining it per view.
    """
    return e.amount if e.split is None else e.split


# ------------------------------------------------------------------ #
# Category breakdown                                                   #
# ------------------------------------------------------------------ #

def category_breakdown(expenses, limit=None):
    """[{category, total, pct}] by my share, largest first.

    pct is share-of-total, so the bars and the donut agree by construction.
    """
    totals = defaultdict(float)
    for e in expenses:
        totals[e.category] += my_spend(e)

    grand = sum(totals.values())
    rows = [
        {
            'category': cat,
            'total':    round(amount, 2),
            'pct':      round(amount / grand * 100, 1) if grand else 0.0,
        }
        for cat, amount in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return rows[:limit] if limit else rows


# ------------------------------------------------------------------ #
# "You're spending X% more on Y than usual"                            #
# ------------------------------------------------------------------ #

# Guardrails so noise doesn't get promoted to a headline.
INSIGHT_MIN_PCT      = 15.0   # ignore swings smaller than this
INSIGHT_MIN_AMOUNT   = 25.0   # ...and ones that are small in absolute terms
INSIGHT_MIN_BASELINE = 25.0   # a "usual" this small makes the % meaningless
INSIGHT_MAX_PCT      = 300    # past this, say "barely spent on X before" instead
INSIGHT_LOOKBACK     = 3      # months of history to average against
INSIGHT_MIN_DAY      = 5      # too early in the month to judge before this


def _month_window(now, back):
    """(year, month) `back` months before now."""
    month = now.month - back
    year  = now.year
    while month < 1:
        month += 12
        year  -= 1
    return year, month


def spending_insight(user_id, now):
    """The single most notable category deviation this month, or None.

    Returns a structured dict rather than a finished string so a Dex-written
    sentence can be swapped in later without touching the template.
    """
    if now.day < INSIGHT_MIN_DAY:
        return None

    # This month so far, by category
    current = defaultdict(float)
    for e in _month_expenses(user_id, now.year, now.month):
        current[e.category] += my_spend(e)
    if not current:
        return None

    # Same slice of the previous months, so a partial month is compared
    # against partial months rather than full ones.
    history = []            # one {category: amount} per month that had activity
    for back in range(1, INSIGHT_LOOKBACK + 1):
        year, month = _month_window(now, back)
        rows = _month_expenses(user_id, year, month, max_day=now.day)
        if not rows:
            continue
        per_cat = defaultdict(float)
        for e in rows:
            per_cat[e.category] += my_spend(e)
        history.append(per_cat)

    if len(history) < 2:
        return None   # not enough history to call anything "usual"

    best = None
    for cat, amount in current.items():
        # Months where the category is absent count as 0 — skipping them would
        # quietly inflate the baseline for occasional spending.
        usual = sum(month.get(cat, 0.0) for month in history) / len(history)
        if usual < INSIGHT_MIN_BASELINE:
            continue   # dividing by near-zero yields nonsense like "2978% more"
        delta = amount - usual
        pct   = delta / usual * 100
        if abs(pct) < INSIGHT_MIN_PCT or abs(delta) < INSIGHT_MIN_AMOUNT:
            continue
        if best is None or abs(pct) > abs(best['pct']):
            best = {
                'category':  cat,
                'pct':       round(abs(pct)),
                'direction': 'more' if delta > 0 else 'less',
                'amount':    round(amount, 2),
                'usual':     round(usual, 2),
            }

    if best is None:
        return None

    if best['pct'] > INSIGHT_MAX_PCT:
        # A multiple, not a percentage — "400% more" reads as a typo.
        times = round(best['amount'] / best['usual'], 1)
        best['text'] = (f"You've spent {times}× your usual on "
                        f"{best['category']} this month.")
    else:
        best['text'] = (f"You're spending {best['pct']}% {best['direction']} on "
                        f"{best['category']} than usual this month.")
    return best


def _month_expenses(user_id, year, month, max_day=None):
    q = (Expense.query
         .filter_by(user_id=user_id)
         .filter(db.extract('year',  Expense.date) == year)
         .filter(db.extract('month', Expense.date) == month))
    if max_day is not None:
        q = q.filter(db.extract('day', Expense.date) <= max_day)
    return q.all()


# ------------------------------------------------------------------ #
# Likely upcoming bills                                                #
# ------------------------------------------------------------------ #

RECURRING_LOOKBACK   = 6      # months of history to scan
RECURRING_MIN_HITS   = 2      # months an expense must appear in to count
RECURRING_AMOUNT_TOL = 0.15   # ±15% counts as "the same bill"
RECURRING_MIN_AMOUNT = 15.0   # below this it's a habit, not a bill worth flagging
RECURRING_LIMIT      = 4


def detect_recurring(user_id, now, limit=RECURRING_LIMIT):
    """Guess which bills are due next, from repeated same-title expenses.

    A heuristic over history — the app has no recurring-bill model — so the UI
    labels this "Likely upcoming" rather than presenting it as scheduled.
    """
    start_year, start_month = _month_window(now, RECURRING_LOOKBACK)
    start = date(start_year, start_month, 1)

    rows = (Expense.query
            .filter_by(user_id=user_id)
            .filter(Expense.date >= start)
            .order_by(Expense.date.asc())
            .all())

    groups = defaultdict(list)
    for e in rows:
        key = ' '.join(e.title.split()).lower()
        if key:
            groups[key].append(e)

    candidates = []
    for entries in groups.values():
        months = {(e.date.year, e.date.month) for e in entries}
        if len(months) < RECURRING_MIN_HITS:
            continue

        amounts = [e.amount for e in entries]
        typical = sum(amounts) / len(amounts)
        if typical < RECURRING_MIN_AMOUNT:
            continue   # a recurring coffee is not an upcoming bill
        # Steady amount? A wildly varying "Groceries" is not a bill.
        if any(abs(a - typical) / typical > RECURRING_AMOUNT_TOL for a in amounts):
            continue

        latest = max(entries, key=lambda e: e.date)
        nxt = _next_occurrence(latest.date, now)
        if nxt is None:
            continue

        candidates.append({
            'title':     latest.title,
            'category':  latest.category,
            'amount':    round(typical, 2),
            'next_date': nxt,
            'seen':      len(months),
        })

    candidates.sort(key=lambda c: c['next_date'])
    return candidates[:limit]


def _next_occurrence(last_date, now):
    """Project the next due date from the day-of-month it usually lands on."""
    today = now.date() if hasattr(now, 'date') else now
    year, month, day = last_date.year, last_date.month, last_date.day

    # Step forward a month at a time until we pass today, clamping the day so
    # a 31st bill still resolves in a 30-day month.
    for _ in range(RECURRING_LOOKBACK + 2):
        month += 1
        if month > 12:
            month = 1
            year += 1
        candidate = date(year, month, min(day, monthrange(year, month)[1]))
        if candidate >= today:
            return candidate
    return None


# ------------------------------------------------------------------ #
# Footer quote                                                         #
# ------------------------------------------------------------------ #

QUOTES = [
    ('A budget is telling your money where to go instead of wondering where it went.', 'Dave Ramsey'),
    ('Do not save what is left after spending; spend what is left after saving.', 'Warren Buffett'),
    ('Beware of little expenses; a small leak will sink a great ship.', 'Benjamin Franklin'),
    ('The art is not in making money, but in keeping it.', 'Proverb'),
    ('It is not your salary that makes you rich, it is your spending habits.', 'Charles Jaffe'),
    ('Wealth consists not in having great possessions, but in having few wants.', 'Epictetus'),
    ('Every time you borrow money, you are robbing your future self.', 'Nathan Morris'),
]


def quote_of_the_day(now):
    """Stable for the whole day, rotates without any storage."""
    return QUOTES[now.timetuple().tm_yday % len(QUOTES)]
