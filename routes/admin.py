import os
import secrets
import threading
import resend
from datetime import datetime, timezone, timedelta
from flask import (Blueprint, render_template, session, request,
                   redirect, url_for, flash, abort, current_app)
from werkzeug.security import generate_password_hash, check_password_hash
from models import (db, User, Expense,
                    ROLE_ADMIN, ROLE_SUBSCRIBER, ROLE_USER, ROLES)
from utils import login_required

admin_bp = Blueprint('admin', __name__)

OTP_TTL       = timedelta(minutes=10)
OTP_MAX_TRIES = 5


def admin_email():
    """The single email allowed to hold the admin role (lower-cased)."""
    return (os.environ.get('ADMIN_PANEL_EMAIL')
            or os.environ.get('ADMIN_EMAIL', '')).strip().lower()


def sync_admin_role():
    """Make the configured owner email the admin, and the only one.

    Runs at startup so the role survives a fresh database, an email change in
    .env, or a row that predates the column.
    """
    target = admin_email()
    changed = False

    for user in User.query.filter_by(role=ROLE_ADMIN).all():
        if user.email.strip().lower() != target:
            user.role = ROLE_USER
            changed = True

    if target:
        owner = User.query.filter(db.func.lower(User.email) == target).first()
        if owner and owner.role != ROLE_ADMIN:
            owner.role = ROLE_ADMIN
            changed = True

    if changed:
        db.session.commit()


def is_admin_user(user):
    """Admin is the role on the row, anchored to the configured owner email."""
    if not user or user.role != ROLE_ADMIN:
        return False
    target = admin_email()
    return bool(target and user.email.strip().lower() == target)


# Blueprints and endpoints that make up the regular user app. The admin account
# has no expense data of its own, so it is bounced off all of them to /admin.
USER_APP_BLUEPRINTS = {'expenses', 'dex', 'import_export'}
USER_APP_ENDPOINTS  = {'main.dashboard', 'main.chart_data_api', 'main.analysis',
                       'main.messages', 'main.calculate', 'auth.profile'}


def home_redirect():
    """Where an authenticated session belongs: the panel for the admin, the
    dashboard for everyone else."""
    user = db.session.get(User, session.get('user_id'))
    endpoint = 'admin.admin' if is_admin_user(user) else 'main.dashboard'
    return redirect(url_for(endpoint))


def redirect_admin_from_user_app():
    """before_request guard: the admin account never sees the user app."""
    endpoint = request.endpoint or ''
    blueprint = endpoint.split('.')[0] if '.' in endpoint else ''

    if blueprint not in USER_APP_BLUEPRINTS and endpoint not in USER_APP_ENDPOINTS:
        return None
    if not session.get('user_id'):
        return None

    user = db.session.get(User, session.get('user_id'))
    if is_admin_user(user):
        return redirect(url_for('admin.admin'))
    return None


def _require_admin_user():
    """Load the session user and 404 unless they are the designated admin.

    A 404 (rather than 403) keeps the panel's existence hidden from everyone else.
    """
    user = db.session.get(User, session.get('user_id'))
    if not is_admin_user(user):
        abort(404)
    return user


# ------------------------------------------------------------------ #
# Email one-time code                                                  #
# ------------------------------------------------------------------ #

def _send_admin_code(user, code):
    app = current_app._get_current_object()
    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="margin:0;padding:0;background:#f4f6f9;font-family:Arial,sans-serif;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f9;padding:40px 0;">
        <tr><td align="center">
          <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
            <tr>
              <td style="background:#1a472a;padding:32px 40px;text-align:center;">
                <h1 style="margin:0;color:#ffffff;font-size:24px;letter-spacing:-0.5px;">Balance Desk</h1>
              </td>
            </tr>
            <tr>
              <td style="padding:40px;">
                <h2 style="margin:0 0 12px;color:#1a1a1a;font-size:20px;">Your admin access code</h2>
                <p style="margin:0 0 24px;color:#555;font-size:15px;line-height:1.6;">
                  Hi {user.name},<br><br>
                  Use the code below to unlock the Balance Desk admin panel.
                  It expires in 10 minutes.
                </p>
                <div style="text-align:center;margin:0 0 24px;">
                  <span style="display:inline-block;background:#f0f4f1;color:#1a472a;font-size:34px;
                               font-weight:700;letter-spacing:10px;padding:16px 28px;border-radius:10px;">
                    {code}
                  </span>
                </div>
                <p style="margin:0;color:#888;font-size:13px;line-height:1.6;">
                  If you didn't request this, you can safely ignore this email &mdash; no one can access
                  the panel without this code.
                </p>
              </td>
            </tr>
            <tr>
              <td style="background:#f9f9f9;padding:20px 40px;text-align:center;border-top:1px solid #eee;">
                <p style="margin:0;color:#aaa;font-size:12px;">
                  &copy; 2026 Balance Desk &nbsp;|&nbsp; Admin security
                </p>
              </td>
            </tr>
          </table>
        </td></tr>
      </table>
    </body>
    </html>
    """

    def _send():
        with app.app_context():
            try:
                resend.Emails.send({
                    "from": "Balance Desk <noreply@verify.chaityadobariya.me>",
                    "to": [user.email],
                    "subject": "Your Balance Desk admin code",
                    "html": html,
                })
            except Exception as exc:   # pragma: no cover - email best-effort
                current_app.logger.warning("Admin code email failed: %s", exc)

    threading.Thread(target=_send, daemon=True).start()


def _clear_admin_session():
    for key in ('is_admin', 'admin_otp_hash', 'admin_otp_exp',
                'admin_otp_tries', 'admin_code_sent'):
        session.pop(key, None)


# ------------------------------------------------------------------ #
# Stats for the panel                                                  #
# ------------------------------------------------------------------ #

def _admin_context():
    now = datetime.now()
    total_users     = User.query.count()
    verified_users  = User.query.filter_by(is_verified=True).count()
    google_users    = User.query.filter(User.google_id.isnot(None)).count()
    subscribers     = User.query.filter_by(role=ROLE_SUBSCRIBER).count()
    total_expenses  = Expense.query.count()
    total_amount    = db.session.query(
        db.func.coalesce(db.func.sum(Expense.amount), 0)).scalar() or 0
    new_users_month = User.query.filter(
        db.extract('year',  User.created_at) == now.year,
        db.extract('month', User.created_at) == now.month).count()

    # Admin first, then subscribers, then everyone else — newest within each group.
    role_rank = db.case({ROLE_ADMIN: 0, ROLE_SUBSCRIBER: 1}, value=User.role, else_=2)
    users = User.query.order_by(role_rank, User.created_at.desc()).all()
    user_rows = [{
        'id':         u.id,
        'name':       u.name,
        'email':      u.email,
        'method':     'Google' if u.google_id else 'Email',
        'verified':   u.is_verified,
        'currency':   u.currency,
        'expenses':   u.expenses.count(),
        'joined':     u.created_at,
        'role':       u.role,
        'role_label': u.role_label,
        'subscribed': u.subscribed_at,
    } for u in users]

    return {
        'total_users':     total_users,
        'verified_users':  verified_users,
        'google_users':    google_users,
        'subscribers':     subscribers,
        'total_expenses':  total_expenses,
        'total_amount':    total_amount,
        'new_users_month': new_users_month,
        'user_rows':       user_rows,
    }


# ------------------------------------------------------------------ #
# Routes                                                               #
# ------------------------------------------------------------------ #

@admin_bp.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    user = _require_admin_user()

    # Signing in with Google is already a strong identity check — trust it.
    if session.get('auth_method') == 'google':
        session['is_admin'] = True

    # Verify a submitted email code (only relevant for non-Google logins)
    if request.method == 'POST' and not session.get('is_admin'):
        entered = (request.form.get('code') or '').strip()
        code_hash = session.get('admin_otp_hash')
        expires   = session.get('admin_otp_exp', 0)
        tries     = session.get('admin_otp_tries', 0)

        if not code_hash or datetime.now(timezone.utc).timestamp() > expires:
            flash('That code has expired. Send a new one.', 'error')
            session.pop('admin_otp_hash', None)
        elif tries >= OTP_MAX_TRIES:
            flash('Too many attempts. Send a new code.', 'error')
            session.pop('admin_otp_hash', None)
        elif entered and check_password_hash(code_hash, entered):
            _clear_admin_session()
            session['is_admin'] = True
        else:
            session['admin_otp_tries'] = tries + 1
            flash('Incorrect code. Please try again.', 'error')

    if session.get('is_admin'):
        return render_template('admin.html', **_admin_context())

    return render_template('admin_gate.html',
                           admin_email=user.email,
                           code_sent=session.get('admin_code_sent', False))


@admin_bp.route('/admin/send-code', methods=['POST'])
@login_required
def admin_send_code():
    user = _require_admin_user()

    code = f"{secrets.randbelow(1_000_000):06d}"
    session['admin_otp_hash']  = generate_password_hash(code)
    session['admin_otp_exp']   = (datetime.now(timezone.utc) + OTP_TTL).timestamp()
    session['admin_otp_tries'] = 0
    session['admin_code_sent'] = True

    _send_admin_code(user, code)
    flash(f'We sent a 6-digit code to {user.email}.', 'info')
    return redirect(url_for('admin.admin'))


@admin_bp.route('/admin/role/<int:user_id>', methods=['POST'])
@login_required
def admin_set_role(user_id):
    """Move a user between the 'user' and 'subscriber' plans."""
    admin = _require_admin_user()
    if not session.get('is_admin'):
        abort(404)

    target = db.session.get(User, user_id)
    role   = (request.form.get('role') or '').strip().lower()

    if not target or role not in ROLES:
        flash('Could not update that account.', 'error')
    elif target.id == admin.id or role == ROLE_ADMIN:
        # The admin row is owned by ADMIN_PANEL_EMAIL, not by this form.
        flash('The admin role is set by configuration, not here.', 'error')
    else:
        target.role = role
        target.subscribed_at = (datetime.now(timezone.utc)
                                if role == ROLE_SUBSCRIBER else None)
        db.session.commit()
        flash(f'{target.email} is now a {role}.', 'success')

    return redirect(url_for('admin.admin'))


@admin_bp.route('/admin/exit')
@login_required
def admin_exit():
    _clear_admin_session()
    return redirect(url_for('main.landing'))
