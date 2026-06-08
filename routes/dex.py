import os
import json
import threading
import resend
from html import escape
from calendar import monthrange
from datetime import datetime, date
from flask import Blueprint, request, jsonify, session, stream_with_context, Response, current_app
from models import db, Expense, User
from utils import login_required


def _max_allowed_date():
    """Latest date an expense may be logged for — last day of next month."""
    today = date.today()
    month, year = today.month + 1, today.year
    if month > 12:
        month, year = 1, year + 1
    return date(year, month, monthrange(year, month)[1])

dex_bp = Blueprint('dex', __name__)

SYSTEM_PROMPT_TEMPLATE = """You are Dex, a friendly AI financial companion built into Balance Desk — a personal expense tracker.

The user's name is {name}. Use their name occasionally to feel personal, but not every message.
Today's date is {today}.

You have four jobs:
1. Answer questions about the user's spending using the expense data below.
2. Add new expenses when the user asks, using the create_expense tool.
3. Edit existing expenses when the user asks, using the update_expense tool.
4. Delete existing expenses when the user asks, using the delete_expense tool.

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
- Each expense in the data below is shown with an ID like "#42" — you need that ID to call update_expense or delete_expense.
- Match the user's description (title, amount, date, category, "the one I just added", etc.) against the expense data to find the right ID.
- If exactly one expense clearly matches, act on it directly — no need to ask again.
- If multiple expenses could match, briefly list the candidates (title, amount, date) and ask which one before doing anything.
- If nothing matches, say so — don't guess an ID.
- For edits, only change the fields the user mentions; leave everything else as-is.
- For deletes, since it can't be undone, name the expense (title, amount, date) when confirming you've deleted it.

ANSWERING SPENDING QUESTIONS:
- Be concise and conversational — no essays.
- Use specific numbers when relevant.
- Format currency as $X.XX.
- Keep responses under 150 words unless the question genuinely needs more.
- Never fabricate transactions."""

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
                        "description": "Total expense amount in dollars"
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
                        "type": "integer",
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
                        "type": "integer",
                        "description": "The numeric ID of the expense to delete, e.g. 42 for '#42'."
                    }
                },
                "required": ["expense_id"]
            }
        }
    }
]


STARTER_PROMPT_TEMPLATE = """Based on this user's recent expense data, write exactly 3 short example messages they could send to Dex (their AI financial companion) — the kind of thing that'd appear as tappable suggestion chips the moment they open the chat.

Make them feel personal and fresh by referencing things you actually see in their data below: real category names, merchants/titles, amounts, or patterns (e.g. "How much did I spend on Eating Out in May?", "Add a $14 lunch at Chipotle today", "What's my biggest expense this month?").

Requirements:
- Exactly 3 examples, each a natural first-person message a user would type — under 12 words.
- Vary the type: mix at least one spending question with one "log an expense" example.
- No numbering, quotes, or extra commentary.
- Return ONLY a raw JSON array of 3 strings — nothing else.

USER'S EXPENSE DATA:
{expense_data}"""

DEFAULT_STARTERS = [
    "How much did I spend this month?",
    "What's my biggest spending category?",
    "Add a $12 coffee from today",
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
    expenses = (Expense.query
                .filter_by(user_id=user_id)
                .order_by(Expense.date.desc())
                .limit(200)
                .all())

    if not expenses:
        return "No expenses recorded yet."

    lines = ["Recent expenses (newest first — #ID is needed to edit or delete one):"]
    for e in expenses:
        split_note = f" (split: ${e.split:.2f} is my share)" if e.split is not None else ""
        paid_note = "" if e.paid_by_user else " (paid by someone else)"
        lines.append(
            f"- #{e.id} | {e.date.strftime('%b %d, %Y')} | {e.title} | {e.category} | ${e.amount:.2f}{split_note}{paid_note} | mode: {e.mode or 'N/A'}"
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
            'message': f"Expense '{args['title']}' for ${amount:.2f} on {exp_date.strftime('%b %d, %Y')} added successfully."
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
            'message': f"Updated '{expense.title}' — now ${expense.amount:.2f} on {expense.date.strftime('%b %d, %Y')}."
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
            'message': f"Deleted '{title}' (${amount:.2f} on {exp_date.strftime('%b %d, %Y')})."
        }
    except Exception as e:
        db.session.rollback()
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

    now = datetime.now()
    system_content = (
        SYSTEM_PROMPT_TEMPLATE.format(
            name=user_name,
            today=now.strftime('%B %d, %Y'),
            today_iso=now.strftime('%Y-%m-%d'),
            max_date=_max_allowed_date().strftime('%B %d, %Y'),
        )
        + f"\n\n---\nUSER'S EXPENSE DATA:\n{_build_expense_context(user_id)}"
    )

    groq_messages = [{'role': 'system', 'content': system_content}]
    for msg in messages[-20:]:
        if msg.get('role') in ('user', 'assistant') and msg.get('content'):
            groq_messages.append({'role': msg['role'], 'content': msg['content']})

    client = Groq(api_key=api_key)

    def generate():
        try:
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
                }

                for tc in assistant_msg.tool_calls:
                    executor = executors.get(tc.function.name)
                    if executor:
                        args = json.loads(tc.function.arguments)
                        result = executor(user_id, args)
                        if result.get('success'):
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


@dex_bp.route('/api/dex/starters', methods=['GET'])
@login_required
def starters():
    """Three fresh, data-aware example prompts Dex generates for itself each time the chat opens."""
    api_key = os.environ.get('GROQ_API_KEY', '').strip()
    if not api_key:
        return jsonify({'starters': DEFAULT_STARTERS})

    try:
        from groq import Groq
    except ImportError:
        return jsonify({'starters': DEFAULT_STARTERS})

    user_id = session['user_id']
    context = _build_expense_context(user_id)

    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model='llama-3.3-70b-versatile',
            messages=[{'role': 'user', 'content': STARTER_PROMPT_TEMPLATE.format(expense_data=context)}],
            max_tokens=200,
            temperature=1.0,
        )
        raw = (response.choices[0].message.content or '').strip()
        raw = raw.strip('`').removeprefix('json').strip()
        parsed = json.loads(raw)
        cleaned = [str(s).strip() for s in parsed if str(s).strip()][:3]
        if len(cleaned) == 3:
            return jsonify({'starters': cleaned})
    except Exception:
        pass

    return jsonify({'starters': DEFAULT_STARTERS})
