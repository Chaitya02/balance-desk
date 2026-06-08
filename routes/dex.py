import os
import json
from calendar import monthrange
from datetime import datetime, date
from flask import Blueprint, request, jsonify, session, stream_with_context, Response
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

You have two jobs:
1. Answer questions about the user's spending using the expense data below.
2. Add new expenses when the user asks, using the create_expense tool.

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
    }
]


def _build_expense_context(user_id: int) -> str:
    expenses = (Expense.query
                .filter_by(user_id=user_id)
                .order_by(Expense.date.desc())
                .limit(200)
                .all())

    if not expenses:
        return "No expenses recorded yet."

    lines = ["Recent expenses (newest first):"]
    for e in expenses:
        split_note = f" (split: ${e.split:.2f} is my share)" if e.split is not None else ""
        paid_note = "" if e.paid_by_user else " (paid by someone else)"
        lines.append(
            f"- {e.date.strftime('%b %d, %Y')} | {e.title} | {e.category} | ${e.amount:.2f}{split_note}{paid_note} | mode: {e.mode or 'N/A'}"
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
                expense_added = False

                for tc in assistant_msg.tool_calls:
                    if tc.function.name == 'create_expense':
                        args = json.loads(tc.function.arguments)
                        result = _execute_create_expense(user_id, args)
                        if result.get('success'):
                            expense_added = True
                        tool_result_msgs.append({
                            'role': 'tool',
                            'tool_call_id': tc.id,
                            'content': json.dumps(result),
                        })

                # Signal the frontend to refresh the expense list if one was added
                if expense_added:
                    yield f"data: {json.dumps({'event': 'expense_added'})}\n\n"

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
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
