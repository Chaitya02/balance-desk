import re
import secrets
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app
from flask_mail import Message
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User
from utils import login_required

auth_bp = Blueprint('auth', __name__)

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _send_verification_email(user):
    token = secrets.token_urlsafe(32)
    user.verification_token = token
    db.session.commit()

    verify_url = url_for('auth.verify_email', token=token, _external=True)
    mail = current_app.extensions['mailer']
    msg = Message(
        subject='Verify your Balance Desk email',
        recipients=[user.email],
    )
    msg.html = f"""
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
                <h2 style="margin:0 0 12px;color:#1a1a1a;font-size:20px;">Verify your email address</h2>
                <p style="margin:0 0 24px;color:#555;font-size:15px;line-height:1.6;">
                  Hi {user.name},<br><br>
                  Thanks for signing up! Click the button below to verify your email address and activate your account.
                </p>
                <table cellpadding="0" cellspacing="0"><tr><td>
                  <a href="{verify_url}"
                     style="display:inline-block;background:#1a472a;color:#ffffff;text-decoration:none;
                            font-size:15px;font-weight:600;padding:14px 32px;border-radius:8px;">
                    Verify Email Address
                  </a>
                </td></tr></table>
                <p style="margin:24px 0 0;color:#888;font-size:13px;line-height:1.6;">
                  This link expires after 24 hours. If you didn't create a Balance Desk account, you can safely ignore this email.
                </p>
              </td>
            </tr>
            <tr>
              <td style="background:#f9f9f9;padding:20px 40px;text-align:center;border-top:1px solid #eee;">
                <p style="margin:0;color:#aaa;font-size:12px;">
                  &copy; 2026 Balance Desk &nbsp;|&nbsp; balancedesk.verify@gmail.com
                </p>
              </td>
            </tr>
          </table>
        </td></tr>
      </table>
    </body>
    </html>
    """
    mail.send(msg)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if session.get('user_id'):
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        name     = ' '.join(request.form.get('name', '').split()).title()
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm_password', '')

        error = None
        if not name:
            error = 'Name is required.'
        elif not _EMAIL_RE.match(email):
            error = 'Enter a valid email address.'
        elif len(password) < 6:
            error = 'Password must be at least 6 characters.'
        elif password != confirm:
            error = 'Passwords do not match.'
        elif User.query.filter_by(email=email).first():
            error = 'An account with that email already exists.'

        if error:
            return render_template('register.html', error=error,
                                   name=name, email=email)

        user = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
            is_verified=False,
        )
        db.session.add(user)
        db.session.commit()

        try:
            _send_verification_email(user)
        except Exception as e:
            current_app.logger.error(f'Verification email failed: {e}')

        return redirect(url_for('auth.verify_notice', email=email))

    return render_template('register.html')


@auth_bp.route('/verify-notice')
def verify_notice():
    email = request.args.get('email', '')
    return render_template('verify_notice.html', email=email)


@auth_bp.route('/verify-email/<token>')
def verify_email(token):
    user = User.query.filter_by(verification_token=token).first()
    if not user:
        flash('Verification link is invalid or has already been used.', 'error')
        return redirect(url_for('auth.login'))

    user.is_verified = True
    user.verification_token = None
    db.session.commit()

    session.clear()
    session['user_id'] = user.id
    flash(f'Email verified! Welcome to Balance Desk, {user.name}!', 'success')
    return redirect(url_for('main.dashboard'))


@auth_bp.route('/resend-verification', methods=['POST'])
def resend_verification():
    email = request.form.get('email', '').strip().lower()
    user = User.query.filter_by(email=email).first()

    if user and not user.is_verified:
        try:
            _send_verification_email(user)
        except Exception as e:
            current_app.logger.error(f'Resend verification email failed: {e}')

    # Always show the same message to avoid leaking account existence
    flash('If that email is registered and unverified, a new link has been sent.', 'info')
    return redirect(url_for('auth.verify_notice', email=email))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        user = User.query.filter_by(email=email).first()

        if user and not user.password_hash:
            return render_template('login.html',
                                   error='This account uses Google sign-in. Please continue with Google.',
                                   email=email)

        if not user or not check_password_hash(user.password_hash, password):
            return render_template('login.html',
                                   error='Invalid email or password.',
                                   email=email)

        if not user.is_verified:
            return redirect(url_for('auth.verify_notice', email=email))

        session.clear()
        session['user_id'] = user.id
        flash(f'Welcome back, {user.name}!', 'success')
        return redirect(url_for('main.dashboard'))

    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('You have been signed out.', 'info')
    return redirect(url_for('main.landing'))


# ------------------------------------------------------------------ #
# Google OAuth                                                        #
# ------------------------------------------------------------------ #

@auth_bp.route('/login/google')
def google_login():
    oauth = current_app.extensions['oauth']
    redirect_uri = url_for('auth.google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route('/login/google/callback')
def google_callback():
    oauth = current_app.extensions['oauth']
    token = oauth.google.authorize_access_token()
    userinfo = token.get('userinfo')

    if not userinfo or not userinfo.get('email'):
        flash('Google sign-in failed. Please try again.', 'error')
        return redirect(url_for('auth.login'))

    google_id  = userinfo['sub']
    email      = userinfo['email'].lower()
    name       = userinfo.get('name', email.split('@')[0])
    avatar_url = userinfo.get('picture', '')

    # Find existing user by google_id or email
    user = User.query.filter_by(google_id=google_id).first()
    if not user:
        user = User.query.filter_by(email=email).first()
        if user:
            # Link Google to existing email account
            user.google_id  = google_id
            user.avatar_url = avatar_url
            user.is_verified = True  # Google already verified the email
        else:
            # Create a brand-new account — Google emails are pre-verified
            user = User(
                name=name,
                email=email,
                google_id=google_id,
                avatar_url=avatar_url,
                is_verified=True,
            )
            db.session.add(user)
        db.session.commit()

    session.clear()
    session['user_id'] = user.id
    flash(f'Welcome, {user.name}!', 'success')
    return redirect(url_for('main.dashboard'))


# ------------------------------------------------------------------ #
# /profile                                                            #
# ------------------------------------------------------------------ #

@auth_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = db.session.get(User, session['user_id'])

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'update_name':
            name = ' '.join(request.form.get('name', '').split()).title()
            if not name:
                flash('Name cannot be empty.', 'error')
            else:
                user.name = name
                db.session.commit()
                flash('Name updated successfully.', 'success')

        elif action == 'change_password':
            current  = request.form.get('current_password', '')
            new_pw   = request.form.get('new_password', '')
            confirm  = request.form.get('confirm_password', '')

            # Google-only users set a password for the first time
            if user.password_hash and not check_password_hash(user.password_hash, current):
                flash('Current password is incorrect.', 'error')
            elif len(new_pw) < 6:
                flash('New password must be at least 6 characters.', 'error')
            elif new_pw != confirm:
                flash('Passwords do not match.', 'error')
            else:
                user.password_hash = generate_password_hash(new_pw)
                db.session.commit()
                flash('Password updated successfully.', 'success')

        return redirect(url_for('auth.profile'))

    return render_template('profile.html', user=user)
