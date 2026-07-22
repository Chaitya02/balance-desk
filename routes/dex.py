import os
import json
import random
import threading
import resend
from html import escape
from calendar import monthrange
from datetime import datetime, date, timezone, timedelta
from flask import Blueprint, request, jsonify, session, stream_with_context, Response, current_app
from models import db, Expense, User
from utils import login_required, CURRENCIES, DEFAULT_CURRENCY


def _max_allowed_date():
    """Latest date an expense may be logged for — last day of next month."""
    today = date.today()
    month, year = today.month + 1, today.year
    if month > 12:
        month, year = 1, year + 1
    return date(year, month, monthrange(year, month)[1])

dex_bp = Blueprint('dex', __name__)


def _currency_symbol(user_id: int) -> str:
    user = db.session.get(User, user_id)
    code = user.currency if user and user.currency in CURRENCIES else DEFAULT_CURRENCY
    return CURRENCIES[code]['symbol']

SYSTEM_PROMPT_TEMPLATE = """You are Dex, a friendly AI financial companion built into Balance Desk — a personal expense tracker.

The user's name is {name}. Use their name occasionally to feel personal, but not every message.
Today's date is {today}.
The user's currency is {currency_name}. Always write amounts with the {currency_symbol} symbol — never any other currency sign, and never convert between currencies.

You have these jobs:
1. Answer questions about the user's spending. Only the most recent expenses are listed below — for anything else, use the tools: call find_expenses to list or locate specific expenses (by title, category, mode, amount, date, etc.), and get_spending_summary for any totals, sums, averages, counts, comparisons, biggest expense, most-used payment method, or split balances. Never add up the expense list by hand.
2. Add new expenses when the user asks, using the create_expense tool.
3. Edit existing expenses when the user asks, using the update_expense tool.
4. Delete existing expenses when the user asks, using the delete_expense tool.
5. Help the user understand how to use Balance Desk (see "HOW BALANCE DESK WORKS" below).

HOW TO ADD AN EXPENSE:
- Extract what you can from the user's message (title, amount, date, category, payment mode).
- Infer category intelligently from context:
    Starbucks/restaurant/food → Eating Out
    Uber/Lyft/metro/fuel → Transport
    Amazon/mall/clothes → Shopping
    Netflix/Spotify/game → Subscriptions
    Gym/doctor/pharmacy → Health
    Supermarket/groceries → Groceries
    Electricity/water/internet → Utilities
    Flight/hotel/trip → Travel
    Rent/lease → Rent
    Otherwise → Miscellaneous
- Suggest payment mode from their most-used methods in past expenses.
- If the amount is missing, ALWAYS ask — never guess it.
- If date is not mentioned, default to today ({today_iso}).
- Expenses can only be logged up to {max_date} — if the user gives a later date, tell them it's too far in the future and don't call the tool.
- If anything else is ambiguous, ask one clear follow-up question; don't bombard with multiple questions at once.
- Once you have everything, call create_expense immediately without asking for confirmation again.

HOW TO EDIT OR DELETE AN EXPENSE:
- Each expense is identified by an ID like "#42" — you need that ID to call update_expense or delete_expense.
- Match the user's description (title, amount, date, category, "the one I just added", etc.) against the recent expense data below to find the right ID. If it's not in that recent list, call find_expenses to look it up first, then use the ID it returns.
- If exactly one expense clearly matches, act on it directly — no need to ask again.
- If multiple expenses could match, briefly list the candidates (title, amount, date) and ask which one before doing anything.
- The most recently added expense is tagged "<-- most recently added" in the data — use it for "the one I just added", "my latest entry", or "my last one".
- If nothing matches, say so — don't guess an ID.
- For edits, only change the fields the user mentions; leave everything else as-is.
- For deletes, since it can't be undone, name the expense (title, amount, date) when confirming you've deleted it.

DELETING MORE THAN ONE EXPENSE (e.g. "delete all my data", "clear my Eating Out expenses", "remove everything from May"):
- NEVER delete multiple expenses without explicit confirmation, and never fire several single deletes at once.
- Use the delete_expenses tool. First call it WITHOUT confirmed (delete_all / category / date range as appropriate) — it returns how many expenses match and their total. Relay that to the user ("This will permanently delete 34 expenses totaling {currency_symbol}4,274. Are you sure?") and wait.
- Only after the user clearly says yes, call delete_expenses again with confirmed=true. If they hesitate or say no, do nothing.

ANSWERING SPENDING QUESTIONS:
- Be concise and conversational — no essays.
- For exact figures, call get_spending_summary and report the numbers it returns; don't compute them from the list yourself (the list only shows recent expenses and may be incomplete).
- To show or list specific expenses (e.g. "show my Starbucks purchases", "which expenses were over {currency_symbol}100", "my UPI payments in March"), call find_expenses and report what it returns.
- Pick the right arguments: a date range for "this month"/"in May"/"last year", a category filter for "on Eating Out", group_by 'mode' for "which payment method do I use most", group_by 'month' for month-to-month comparisons. For split questions, use the you_owe_others / others_owe_you figures it returns.
- Use specific numbers when relevant. Format currency as {currency_symbol}X.XX.
- Keep responses under 150 words unless the question genuinely needs more.
- Never fabricate transactions.

HOW BALANCE DESK WORKS (use this to answer "how do I…" questions — explain the steps, you can't do these for the user):
- Add an expense (manually): click "+ Add expense" on the Dashboard or Expenses page and fill in the form. (Or just tell me the details here and I'll add it.)
- Edit or delete an expense: go to the Expenses page; each row has edit and delete icons. (Or ask me to do it.)
- Import from Excel: open the profile menu (top-right) > Profile settings > Import. Download the template, fill in your rows, choose "Add" (append) or "Replace" (overwrite everything), upload the .xlsx, then review and apply.
- Download the import template: it's the "Download expenses_template.xlsx" link on the Import page — it has example rows and an Instructions tab (delete the example rows before importing).
- Export to a spreadsheet: profile menu > Profile settings > Export, pick the months you want, then "Export Expenses" to download an .xlsx.
- Delete all data: there's no one-click wipe — either delete entries individually on the Expenses page, or use Import with "Replace" mode to overwrite everything with a new file.

STAYING ON TOPIC:
- You only handle personal finance and Balance Desk. If asked something unrelated (weather, trivia, poems, coding, etc.), politely decline in one line and steer back to their spending — don't attempt an answer."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_expense",
            "description": "Add a new expense to the user's Balance Desk account. Call this only once all required fields are known.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Name of the expense (e.g. 'Starbucks', 'Rent', 'Uber')"
                    },
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format"
                    },
                    "category": {
                        "type": "string",
                        "description": "Category: Eating Out, Groceries, Transport, Rent, Utilities, Shopping, Entertainment, Health, Subscriptions, Travel, Personal Care, Gifts, Miscellaneous"
                    },
                    "amount": {
                        "type": "number",
                        "description": "Total expense amount in the user's currency"
                    },
                    "mode": {
                        "type": "string",
                        "description": "Payment method (e.g. Cash, Credit Card, Debit Card, UPI)"
                    },
                    "paid_by_user": {
                        "type": "boolean",
                        "description": "True if the user paid. False if someone else paid and the user owes them their share."
                    },
                    "split": {
                        "type": "number",
                        "description": "User's share in dollars — only set when splitting and their share differs from the total amount."
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional short note about the expense."
                    }
                },
                "required": ["title", "date", "category", "amount", "mode", "paid_by_user"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_expense",
            "description": "Edit one or more fields of an existing expense, identified by its ID (shown as #ID in the expense data). Only include the fields that should change.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expense_id": {
                        "anyOf": [{"type": "integer"}, {"type": "string"}],
                        "description": "The numeric ID of the expense to edit, e.g. 42 for '#42'."
                    },
                    "title": {"type": "string", "description": "New name for the expense."},
                    "date": {"type": "string", "description": "New date in YYYY-MM-DD format."},
                    "category": {
                        "type": "string",
                        "description": "New category: Eating Out, Groceries, Transport, Rent, Utilities, Shopping, Entertainment, Health, Subscriptions, Travel, Personal Care, Gifts, Miscellaneous"
                    },
                    "amount": {"type": "number", "description": "New total amount in dollars."},
                    "mode": {"type": "string", "description": "New payment method (e.g. Cash, Credit Card, Debit Card, UPI)."},
                    "paid_by_user": {
                        "type": "boolean",
                        "description": "True if the user paid. False if someone else paid and the user owes them their share."
                    },
                    "split": {
                        "type": "number",
                        "description": "User's share in dollars — set when splitting and their share differs from the total amount."
                    },
                    "description": {"type": "string", "description": "New short note about the expense."}
                },
                "required": ["expense_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_expense",
            "description": "Permanently delete an existing expense, identified by its ID (shown as #ID in the expense data). This cannot be undone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expense_id": {
                        "anyOf": [{"type": "integer"}, {"type": "string"}],
                        "description": "The numeric ID of the expense to delete, e.g. 42 for '#42'."
                    }
                },
                "required": ["expense_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_spending_summary",
            "description": "Compute exact spending figures from the full database. Use this for ANY question involving totals, sums, averages, counts, comparisons across periods, biggest expense, most-used payment method, or how much is owed/owing on splits. Never add up the expense list by hand — call this instead so the numbers are accurate and cover all the user's data, not just the recent ones shown.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Start of the date range (inclusive), YYYY-MM-DD. Omit for all time."
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End of the date range (inclusive), YYYY-MM-DD. Omit for all time."
                    },
                    "category": {
                        "type": "string",
                        "description": "Restrict to a single category, e.g. 'Eating Out'. Omit to include all categories."
                    },
                    "group_by": {
                        "type": "string",
                        "enum": ["category", "mode", "month", "none"],
                        "description": "How to break down the total: 'category', 'mode' (payment method), 'month', or 'none' for a single overall figure."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_expenses",
            "description": "Delete MULTIPLE expenses at once — e.g. 'delete all my data', 'remove everything from May', 'clear all my Eating Out expenses'. For a single specific expense, use delete_expense instead. This is destructive and irreversible, so it is a TWO-STEP flow: first call it WITHOUT confirmed (or confirmed=false) to get the count, tell the user exactly how many expenses will be deleted and the total, and ask them to confirm; only call it again with confirmed=true after the user clearly says yes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "delete_all": {
                        "type": "boolean",
                        "description": "Set true to delete ALL of the user's expenses (a full data wipe)."
                    },
                    "expense_ids": {
                        "type": "array",
                        "items": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
                        "description": "Specific expense IDs to delete, when removing a known set."
                    },
                    "category": {
                        "type": "string",
                        "description": "Delete only expenses in this category, e.g. 'Eating Out'."
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Only delete expenses on or after this date (YYYY-MM-DD)."
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Only delete expenses on or before this date (YYYY-MM-DD)."
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "Must be true to actually delete. Only set true AFTER the user has explicitly confirmed in their latest message."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_expenses",
            "description": "Search the user's expenses with filters and get back the matching rows (each with its #ID). Use this to LIST or LOCATE specific expenses — e.g. 'show my Starbucks expenses', 'what did I buy at Walmart', 'find expenses over 100', 'my UPI payments in March' — and to look up the #ID of an expense you need to edit or delete when it isn't in the recent list shown to you. For totals, sums, counts, or comparisons, use get_spending_summary instead. For a date/month/year RANGE, use start_date and end_date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Exact category, e.g. 'Eating Out'."},
                    "mode": {"type": "string", "description": "Exact payment method, e.g. 'Cash', 'UPI'."},
                    "title": {"type": "string", "description": "Exact expense title."},
                    "title_contains": {"type": "string", "description": "Match titles containing this text (partial/substring match)."},
                    "description_contains": {"type": "string", "description": "Match descriptions containing this text."},
                    "amount": {"type": "number", "description": "Exact amount."},
                    "min_amount": {"type": "number", "description": "Amount greater than or equal to this."},
                    "max_amount": {"type": "number", "description": "Amount less than or equal to this."},
                    "start_date": {"type": "string", "description": "On or after this date (YYYY-MM-DD). Use with end_date for any range, including month or year ranges."},
                    "end_date": {"type": "string", "description": "On or before this date (YYYY-MM-DD)."},
                    "month": {"type": "integer", "description": "Single calendar month 1-12 (combine with year; defaults to the current year if year is omitted)."},
                    "year": {"type": "integer", "description": "Single year, e.g. 2026. Alone, matches the whole year; with month, that one month."},
                    "paid_by_user": {"type": "boolean", "description": "True for expenses the user paid; false for ones someone else paid."},
                    "is_split": {"type": "boolean", "description": "True to return only split expenses; false for only non-split."},
                    "sort_by": {"type": "string", "enum": ["date", "amount", "recent"], "description": "Sort by 'date', 'amount', or 'recent' (most recently added)."},
                    "order": {"type": "string", "enum": ["asc", "desc"], "description": "Sort direction. Defaults to desc."},
                    "limit": {"type": "integer", "description": "Max rows to return (default 20, max 50)."}
                },
                "required": []
            }
        }
    }
]

# Tools that change the user's data — only these should trigger a frontend refresh.
MUTATING_TOOLS = {'create_expense', 'update_expense', 'delete_expense', 'delete_expenses'}

# Human-friendly labels shown in the chat while a tool runs ("what Dex is doing").
TOOL_LABELS = {
    'create_expense': 'Adding your expense',
    'update_expense': 'Updating your expense',
    'delete_expense': 'Deleting your expense',
    'delete_expenses': 'Deleting expenses',
    'get_spending_summary': 'Crunching your spending data',
    'find_expenses': 'Searching your expenses',
}

# How many recent expenses to inline into the prompt. Everything beyond this is
# reachable via find_expenses / get_spending_summary, keeping per-message tokens low.
RECENT_CONTEXT_LIMIT = 15


# How many starters to generate per pool, and how many to show per chat open.
STARTER_POOL_SIZE = 10
STARTERS_SHOWN = 3
# Regenerate the pool only after it's this old — keeps LLM calls rare.
STARTERS_TTL = timedelta(days=7)

STARTER_PROMPT_TEMPLATE = """Based on this user's recent expense data, write exactly {pool_size} short example messages they could send to Dex (their AI financial companion) — the kind of thing that'd appear as tappable suggestion chips the moment they open the chat.

Make them feel personal and fresh by referencing things you actually see in their data below: real category names, merchants/titles, amounts, or patterns (e.g. "How much did I spend on Eating Out in May?", "Add a {currency_symbol}14 lunch at Chipotle today", "What's my biggest expense this month?"). The user's currency symbol is {currency_symbol} — use it for any amounts.

Requirements:
- Exactly {pool_size} examples, each a natural first-person message a user would type — under 12 words.
- Make them varied: mix spending questions, "log an expense" examples, and edit/delete examples. Avoid near-duplicates.
- Refer to expenses by their title/amount/date (e.g. "Delete my {currency_symbol}40 Walmart expense"), never by raw #ID numbers — users don't know those.
- No numbering, quotes, or extra commentary.
- Return ONLY a raw JSON array of {pool_size} strings — nothing else.

USER'S EXPENSE DATA:
{expense_data}"""

DEFAULT_STARTERS = [
    "How much did I spend this month?",
    "What's my biggest spending category?",
    "Add a {currency_symbol}12 coffee from today",
]

DEX_FALLBACK_MESSAGE = "Dex is taking a quick break right now — please try again in a little while."


def _render_chat_transcript(messages: list) -> str:
    if not messages:
        return "<p style=\"color:#888;\">(no conversation messages)</p>"

    rows = []
    for m in messages[-20:]:
        role = m.get('role')
        if role not in ('user', 'assistant'):
            continue
        label = 'User' if role == 'user' else 'Dex'
        color = '#1a472a' if role == 'user' else '#946200'
        text = escape(str(m.get('content') or ''))
        rows.append(
            f"<p style=\"margin:0 0 10px;\"><strong style=\"color:{color};\">{label}:</strong> "
            f"<span style=\"white-space:pre-wrap;\">{text}</span></p>"
        )
    return ''.join(rows) if rows else "<p style=\"color:#888;\">(no conversation messages)</p>"


def _notify_dex_error(user, error_text: str, messages: list | None = None) -> None:
    """Log a Dex failure and email the admin — with the chat transcript — so it doesn't go unnoticed."""
    who = f"{user.name} <{user.email}>" if user else "unknown user"
    current_app.logger.error(f"Dex chat error for {who}: {error_text}")

    admin_email = os.environ.get('ADMIN_EMAIL', '').strip()
    if not admin_email:
        return

    app = current_app._get_current_object()
    transcript_html = _render_chat_transcript(messages or [])

    def _send():
        with app.app_context():
            try:
                resend.Emails.send({
                    "from": "Balance Desk <noreply@verify.chaityadobariya.me>",
                    "to": [admin_email],
                    "subject": "Dex error alert — Balance Desk",
                    "html": (
                        f"<p><strong>Dex ran into an error.</strong></p>"
                        f"<p>User: {who}</p>"
                        f"<pre style=\"white-space:pre-wrap;font-family:monospace;\">{escape(error_text)}</pre>"
                        f"<hr style=\"border:none;border-top:1px solid #eee;margin:16px 0;\">"
                        f"<p><strong>Conversation leading up to the error:</strong></p>"
                        f"{transcript_html}"
                    ),
                })
            except Exception:
                current_app.logger.exception('Failed to send Dex error alert email')

    threading.Thread(target=_send, daemon=True).start()


def _build_expense_context(user_id: int) -> str:
    base = Expense.query.filter_by(user_id=user_id)
    total = base.count()
    if total == 0:
        return "No expenses recorded yet."

    expenses = (base.order_by(Expense.date.desc(), Expense.id.desc())
                .limit(RECENT_CONTEXT_LIMIT)
                .all())

    earliest = base.order_by(Expense.date.asc()).first().date
    latest = expenses[0].date

    # Highest id = most recently added row (ids are monotonic on insert), which can
    # differ from the newest by date if the user backdated it. Mark it so Dex can
    # resolve "the one I just added" / "my latest entry" reliably.
    newest_added_id = max(e.id for e in expenses)

    header = (
        f"The user has {total} expense(s) in total, from {earliest.strftime('%b %d, %Y')} "
        f"to {latest.strftime('%b %d, %Y')}. Only the {len(expenses)} most recent are listed below "
        f"(newest first). To find or act on any expense NOT in this list, call find_expenses to look "
        f"it up; for totals, sums, or comparisons, call get_spending_summary. #ID is needed to edit or delete one."
    )
    sym = _currency_symbol(user_id)
    lines = [header, ""]
    for e in expenses:
        split_note = f" (split: {sym}{e.split:.2f} is my share)" if e.split is not None else ""
        paid_note = "" if e.paid_by_user else " (paid by someone else)"
        added_note = "  <-- most recently added" if e.id == newest_added_id else ""
        lines.append(
            f"- #{e.id} | {e.date.strftime('%b %d, %Y')} | {e.title} | {e.category} | {sym}{e.amount:.2f}{split_note}{paid_note} | mode: {e.mode or 'N/A'}{added_note}"
        )
    return "\n".join(lines)


def _execute_create_expense(user_id: int, args: dict) -> dict:
    try:
        exp_date = datetime.strptime(args['date'], '%Y-%m-%d').date()
        max_allowed = _max_allowed_date()
        if exp_date > max_allowed:
            return {
                'success': False,
                'error': f"Can't add expenses dated later than {max_allowed.strftime('%b %d, %Y')}."
            }
        amount = float(args['amount'])
        split = float(args['split']) if args.get('split') is not None else None
        paid_by_user = bool(args.get('paid_by_user', True))
        mode = str(args.get('mode') or ('Cash' if paid_by_user else ''))

        expense = Expense(
            user_id=user_id,
            date=exp_date,
            title=str(args['title']),
            description=str(args.get('description') or ''),
            category=str(args['category']),
            mode=mode,
            amount=amount,
            split=split,
            paid_by_user=paid_by_user,
        )
        db.session.add(expense)
        db.session.commit()
        return {
            'success': True,
            'message': f"Expense '{args['title']}' for {_currency_symbol(user_id)}{amount:.2f} on {exp_date.strftime('%b %d, %Y')} added successfully."
        }
    except Exception as e:
        db.session.rollback()
        return {'success': False, 'error': str(e)}


def _execute_update_expense(user_id: int, args: dict) -> dict:
    try:
        expense_id = int(args['expense_id'])
        expense = Expense.query.filter_by(id=expense_id, user_id=user_id).first()
        if not expense:
            return {'success': False, 'error': f"No expense with ID #{expense_id} found."}

        if args.get('date'):
            exp_date = datetime.strptime(args['date'], '%Y-%m-%d').date()
            max_allowed = _max_allowed_date()
            if exp_date > max_allowed:
                return {
                    'success': False,
                    'error': f"Can't set a date later than {max_allowed.strftime('%b %d, %Y')}."
                }
            expense.date = exp_date
        if args.get('title'):
            expense.title = str(args['title'])
        if args.get('category'):
            expense.category = str(args['category'])
        if args.get('amount') is not None:
            expense.amount = float(args['amount'])
        if args.get('mode'):
            expense.mode = str(args['mode'])
        if args.get('paid_by_user') is not None:
            expense.paid_by_user = bool(args['paid_by_user'])
        if 'split' in args:
            expense.split = float(args['split']) if args['split'] is not None else None
        if 'description' in args:
            expense.description = str(args.get('description') or '')

        db.session.commit()
        return {
            'success': True,
            'message': f"Updated '{expense.title}' — now {_currency_symbol(user_id)}{expense.amount:.2f} on {expense.date.strftime('%b %d, %Y')}."
        }
    except Exception as e:
        db.session.rollback()
        return {'success': False, 'error': str(e)}


def _execute_delete_expense(user_id: int, args: dict) -> dict:
    try:
        expense_id = int(args['expense_id'])
        expense = Expense.query.filter_by(id=expense_id, user_id=user_id).first()
        if not expense:
            return {'success': False, 'error': f"No expense with ID #{expense_id} found."}

        title, amount, exp_date = expense.title, expense.amount, expense.date
        db.session.delete(expense)
        db.session.commit()
        return {
            'success': True,
            'message': f"Deleted '{title}' ({_currency_symbol(user_id)}{amount:.2f} on {exp_date.strftime('%b %d, %Y')})."
        }
    except Exception as e:
        db.session.rollback()
        return {'success': False, 'error': str(e)}


def _execute_get_spending_summary(user_id: int, args: dict) -> dict:
    """Read-only aggregation over the user's expenses — totals, breakdowns, top items, split balances."""
    try:
        q = Expense.query.filter_by(user_id=user_id)

        if args.get('start_date'):
            start = datetime.strptime(args['start_date'], '%Y-%m-%d').date()
            q = q.filter(Expense.date >= start)
        if args.get('end_date'):
            end = datetime.strptime(args['end_date'], '%Y-%m-%d').date()
            q = q.filter(Expense.date <= end)
        if args.get('category'):
            q = q.filter(db.func.lower(Expense.category) == str(args['category']).strip().lower())

        expenses = q.all()
        if not expenses:
            return {'success': True, 'count': 0, 'total': 0.0,
                    'note': 'No expenses match that filter.'}

        result = {
            'success': True,
            'count': len(expenses),
            'total': round(sum(e.amount for e in expenses), 2),
            'you_owe_others': round(sum(e.you_owe for e in expenses), 2),
            'others_owe_you': round(sum(e.friend_owes for e in expenses), 2),
        }

        group_by = (args.get('group_by') or 'none').lower()
        if group_by in ('category', 'mode', 'month'):
            buckets = {}
            for e in expenses:
                if group_by == 'category':
                    key = e.category or 'Uncategorized'
                elif group_by == 'mode':
                    key = e.mode or 'Unknown'
                else:
                    key = e.date.strftime('%Y-%m')
                slot = buckets.setdefault(key, {'total': 0.0, 'count': 0})
                slot['total'] += e.amount
                slot['count'] += 1
            result['breakdown'] = {
                k: {'total': round(v['total'], 2), 'count': v['count']}
                for k, v in sorted(buckets.items(), key=lambda kv: kv[1]['total'], reverse=True)
            }

        top = sorted(expenses, key=lambda e: e.amount, reverse=True)[:5]
        result['top_expenses'] = [
            {'id': e.id, 'title': e.title, 'amount': round(e.amount, 2),
             'date': e.date.strftime('%Y-%m-%d'), 'category': e.category}
            for e in top
        ]
        return result
    except Exception as e:
        return {'success': False, 'error': str(e)}


def _execute_delete_expenses(user_id: int, args: dict) -> dict:
    """Bulk delete with a mandatory two-step confirmation. Deletes only when confirmed=true."""
    try:
        q = Expense.query.filter_by(user_id=user_id)
        delete_all = bool(args.get('delete_all'))
        ids = args.get('expense_ids') or []

        if not delete_all:
            if ids:
                q = q.filter(Expense.id.in_([int(i) for i in ids]))
            else:
                has_filter = False
                if args.get('category'):
                    q = q.filter(db.func.lower(Expense.category) == str(args['category']).strip().lower())
                    has_filter = True
                if args.get('start_date'):
                    q = q.filter(Expense.date >= datetime.strptime(args['start_date'], '%Y-%m-%d').date())
                    has_filter = True
                if args.get('end_date'):
                    q = q.filter(Expense.date <= datetime.strptime(args['end_date'], '%Y-%m-%d').date())
                    has_filter = True
                # Refuse an unscoped wipe unless delete_all is explicitly set.
                if not has_filter:
                    return {'success': False,
                            'error': 'Nothing specified to delete. Set delete_all=true for a full wipe, or give a filter (ids, category, or date range).'}

        matches = q.all()
        count = len(matches)
        if count == 0:
            return {'success': True, 'deleted': 0, 'message': 'No matching expenses to delete.'}

        total = round(sum(e.amount for e in matches), 2)

        sym = _currency_symbol(user_id)

        # Gate: never delete more than one expense without explicit confirmation.
        if not bool(args.get('confirmed')):
            sample = [f"{e.title} ({sym}{e.amount:.2f}, {e.date.strftime('%b %d, %Y')})"
                      for e in matches[:5]]
            return {
                'success': False,
                'needs_confirmation': True,
                'count': count,
                'total': total,
                'sample': sample,
                'message': (f"This will permanently delete {count} expense(s) totaling "
                            f"{sym}{total:.2f}. Do NOT proceed until the user explicitly confirms."),
            }

        for e in matches:
            db.session.delete(e)
        db.session.commit()
        return {'success': True, 'deleted': count, 'total': total,
                'message': f"Deleted {count} expense(s) totaling {sym}{total:.2f}."}
    except Exception as e:
        db.session.rollback()
        return {'success': False, 'error': str(e)}


def _execute_find_expenses(user_id: int, args: dict) -> dict:
    """Read-only search over the user's expenses with flexible filters. Returns matching rows + #IDs."""
    try:
        def val(name):
            v = args.get(name)
            return v if v not in (None, '') else None

        q = Expense.query.filter_by(user_id=user_id)

        if val('category'):
            q = q.filter(db.func.lower(Expense.category) == str(args['category']).strip().lower())
        if val('mode'):
            q = q.filter(db.func.lower(Expense.mode) == str(args['mode']).strip().lower())
        if val('title'):
            q = q.filter(db.func.lower(Expense.title) == str(args['title']).strip().lower())
        if val('title_contains'):
            q = q.filter(Expense.title.ilike(f"%{str(args['title_contains']).strip()}%"))
        if val('description_contains'):
            q = q.filter(Expense.description.ilike(f"%{str(args['description_contains']).strip()}%"))
        if val('amount') is not None:
            q = q.filter(Expense.amount == round(float(args['amount']), 2))
        if val('min_amount') is not None:
            q = q.filter(Expense.amount >= float(args['min_amount']))
        if val('max_amount') is not None:
            q = q.filter(Expense.amount <= float(args['max_amount']))
        if val('start_date'):
            q = q.filter(Expense.date >= datetime.strptime(args['start_date'], '%Y-%m-%d').date())
        if val('end_date'):
            q = q.filter(Expense.date <= datetime.strptime(args['end_date'], '%Y-%m-%d').date())

        # Single month / year convenience (ranges should use start_date/end_date).
        year = int(args['year']) if val('year') is not None else None
        month = int(args['month']) if val('month') is not None else None
        if year is not None and month is not None:
            last = monthrange(year, month)[1]
            q = q.filter(Expense.date >= date(year, month, 1), Expense.date <= date(year, month, last))
        elif year is not None:
            q = q.filter(Expense.date >= date(year, 1, 1), Expense.date <= date(year, 12, 31))
        elif month is not None:
            yr = date.today().year
            last = monthrange(yr, month)[1]
            q = q.filter(Expense.date >= date(yr, month, 1), Expense.date <= date(yr, month, last))

        if args.get('paid_by_user') is not None:
            q = q.filter(Expense.paid_by_user == bool(args['paid_by_user']))
        if args.get('is_split') is not None:
            q = q.filter(Expense.split.isnot(None) if bool(args['is_split']) else Expense.split.is_(None))

        sort_col = {'date': Expense.date, 'amount': Expense.amount,
                    'recent': Expense.id}.get((args.get('sort_by') or 'date').lower(), Expense.date)
        descending = (args.get('order') or 'desc').lower() != 'asc'
        q = q.order_by(sort_col.desc() if descending else sort_col.asc(), Expense.id.desc())

        try:
            limit = max(1, min(int(args.get('limit') or 20), 50))
        except (ValueError, TypeError):
            limit = 20

        match_count = q.count()
        rows = q.limit(limit).all()
        return {
            'success': True,
            'match_count': match_count,
            'returned': len(rows),
            'expenses': [{
                'id': e.id,
                'date': e.date.strftime('%Y-%m-%d'),
                'title': e.title,
                'category': e.category,
                'mode': e.mode or '',
                'amount': round(e.amount, 2),
                'split': round(e.split, 2) if e.split is not None else None,
                'paid_by_user': e.paid_by_user,
                'description': e.description or '',
            } for e in rows],
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}


@dex_bp.route('/api/dex/chat', methods=['POST'])
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    messages = data.get('messages', [])
    if not messages:
        return jsonify({'error': 'No messages provided'}), 400

    api_key = os.environ.get('GROQ_API_KEY', '').strip()
    if not api_key:
        return jsonify({'error': 'Groq API key not configured. Add GROQ_API_KEY to your .env file.'}), 503

    try:
        from groq import Groq
    except ImportError:
        return jsonify({'error': 'groq package not installed. Run: pip install groq'}), 503

    user_id = session['user_id']
    user = db.session.get(User, user_id)
    user_name = user.name if user else 'there'

    currency_code = (user.currency if user and user.currency in CURRENCIES
                     else DEFAULT_CURRENCY)

    now = datetime.now()
    system_content = (
        SYSTEM_PROMPT_TEMPLATE.format(
            name=user_name,
            today=now.strftime('%B %d, %Y'),
            today_iso=now.strftime('%Y-%m-%d'),
            max_date=_max_allowed_date().strftime('%B %d, %Y'),
            currency_name=CURRENCIES[currency_code]['label'],
            currency_symbol=CURRENCIES[currency_code]['symbol'],
        )
        + f"\n\n---\nUSER'S EXPENSE DATA:\n{_build_expense_context(user_id)}"
    )

    groq_messages = [{'role': 'system', 'content': system_content}]
    for msg in messages[-20:]:
        if msg.get('role') in ('user', 'assistant') and msg.get('content'):
            groq_messages.append({'role': msg['role'], 'content': msg['content']})

    def generate():
        try:
            client = Groq(api_key=api_key)

            # Non-streaming first call so we can detect tool calls
            response = client.chat.completions.create(
                model='llama-3.3-70b-versatile',
                messages=groq_messages,
                tools=TOOLS,
                tool_choice='auto',
                max_tokens=512,
                temperature=0.6,
            )

            assistant_msg = response.choices[0].message

            if assistant_msg.tool_calls:
                tool_result_msgs = []
                data_changed = False

                executors = {
                    'create_expense': _execute_create_expense,
                    'update_expense': _execute_update_expense,
                    'delete_expense': _execute_delete_expense,
                    'delete_expenses': _execute_delete_expenses,
                    'get_spending_summary': _execute_get_spending_summary,
                    'find_expenses': _execute_find_expenses,
                }

                # Safety: if the model tries to delete several expenses via repeated
                # single-delete calls in one turn, block them and steer it to the
                # confirmed bulk-delete flow instead.
                single_deletes = sum(1 for tc in assistant_msg.tool_calls
                                     if tc.function.name == 'delete_expense')

                for tc in assistant_msg.tool_calls:
                    executor = executors.get(tc.function.name)
                    if executor:
                        if tc.function.name == 'delete_expense' and single_deletes > 1:
                            tool_result_msgs.append({
                                'role': 'tool',
                                'tool_call_id': tc.id,
                                'content': json.dumps({
                                    'success': False,
                                    'error': ('Deleting multiple expenses at once requires '
                                              'confirmation. Use the delete_expenses tool: preview '
                                              'the count, ask the user to confirm, then set confirmed=true.'),
                                }),
                            })
                            continue

                        # Tell the frontend what Dex is doing before it runs.
                        label = TOOL_LABELS.get(tc.function.name, 'Working on it')
                        yield f"data: {json.dumps({'event': 'tool_activity', 'label': label})}\n\n"
                        args = json.loads(tc.function.arguments)
                        result = executor(user_id, args)
                        if result.get('success') and tc.function.name in MUTATING_TOOLS:
                            data_changed = True
                        tool_result_msgs.append({
                            'role': 'tool',
                            'tool_call_id': tc.id,
                            'content': json.dumps(result),
                        })

                # Signal the frontend to refresh the expense list if anything changed
                if data_changed:
                    yield f"data: {json.dumps({'event': 'data_changed'})}\n\n"

                follow_up = groq_messages + [
                    {
                        'role': 'assistant',
                        'content': assistant_msg.content or '',
                        'tool_calls': [
                            {
                                'id': tc.id,
                                'type': 'function',
                                'function': {
                                    'name': tc.function.name,
                                    'arguments': tc.function.arguments,
                                }
                            }
                            for tc in assistant_msg.tool_calls
                        ],
                    }
                ] + tool_result_msgs

                # Stream the confirmation response
                stream = client.chat.completions.create(
                    model='llama-3.3-70b-versatile',
                    messages=follow_up,
                    max_tokens=256,
                    temperature=0.6,
                    stream=True,
                )
                for chunk in stream:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield f"data: {json.dumps({'content': delta})}\n\n"

            else:
                # Plain text response — send as one chunk (no tool involved)
                if assistant_msg.content:
                    yield f"data: {json.dumps({'content': assistant_msg.content})}\n\n"

            yield "data: [DONE]\n\n"

        except Exception as e:
            _notify_dex_error(user, str(e), messages)
            yield f"data: {json.dumps({'content': DEX_FALLBACK_MESSAGE})}\n\n"
            yield "data: [DONE]\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


def _read_cached_pool(user) -> list | None:
    """Return the user's cached starter pool if it exists and is still fresh, else None."""
    if not user or not user.dex_starters or not user.dex_starters_at:
        return None
    generated_at = user.dex_starters_at
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - generated_at > STARTERS_TTL:
        return None
    try:
        pool = json.loads(user.dex_starters)
    except (ValueError, TypeError):
        return None
    cleaned = [str(s).strip() for s in pool if str(s).strip()]
    return cleaned or None


def _generate_starter_pool(user_id: int) -> list:
    """Ask the LLM for a fresh pool of starter prompts. Returns [] on any failure."""
    api_key = os.environ.get('GROQ_API_KEY', '').strip()
    if not api_key:
        return []
    try:
        from groq import Groq
    except ImportError:
        return []

    context = _build_expense_context(user_id)
    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model='llama-3.3-70b-versatile',
            messages=[{'role': 'user', 'content': STARTER_PROMPT_TEMPLATE.format(
                pool_size=STARTER_POOL_SIZE, expense_data=context,
                currency_symbol=_currency_symbol(user_id))}],
            max_tokens=500,
            temperature=1.0,
        )
        raw = (response.choices[0].message.content or '').strip()
        raw = raw.strip('`').removeprefix('json').strip()
        parsed = json.loads(raw)
        # De-duplicate while preserving order.
        seen, cleaned = set(), []
        for s in parsed:
            text = str(s).strip()
            if text and text.lower() not in seen:
                seen.add(text.lower())
                cleaned.append(text)
        return cleaned
    except Exception:
        return []


def _save_starter_pool(user, pool: list) -> None:
    try:
        user.dex_starters = json.dumps(pool)
        user.dex_starters_at = datetime.now(timezone.utc)
        db.session.commit()
    except Exception:
        db.session.rollback()


@dex_bp.route('/api/dex/starters', methods=['GET'])
@login_required
def starters():
    """Serve 3 starter chips sampled from a per-user pool, regenerating the pool only when stale."""
    user_id = session['user_id']
    user = db.session.get(User, user_id)

    pool = _read_cached_pool(user)
    if pool is None:
        pool = _generate_starter_pool(user_id)
        if pool:
            _save_starter_pool(user, pool)

    if not pool:
        pool = [s.format(currency_symbol=_currency_symbol(user_id))
                for s in DEFAULT_STARTERS]

    count = min(STARTERS_SHOWN, len(pool))
    return jsonify({'starters': random.sample(pool, count)})
