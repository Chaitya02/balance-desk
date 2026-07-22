import os
import resend
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, session, g, render_template
from werkzeug.middleware.proxy_fix import ProxyFix
from authlib.integrations.flask_client import OAuth
from models import db, User
from utils import CURRENCIES, DEFAULT_CURRENCY, format_money
from routes.auth import auth_bp
from routes.main import main_bp
from routes.expenses import expenses_bp
from routes.import_export import import_export_bp
from routes.dex import dex_bp
from routes.admin import admin_bp, sync_admin_role, redirect_admin_from_user_app

oauth = OAuth()


def create_app():
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    debug = os.environ.get('DEBUG', 'false').lower() == 'true'

    secret_key = os.environ.get('SECRET_KEY')
    if not secret_key:
        if debug:
            secret_key = 'dev-secret-do-not-use-in-production'
        else:
            raise ValueError('SECRET_KEY environment variable must be set in production')

    app.config['SECRET_KEY']                  = secret_key
    app.config['DEBUG']                       = debug
    app.config['SQLALCHEMY_DATABASE_URI']     = os.environ.get('DATABASE_URL', 'sqlite:///balance_desk.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['MAX_CONTENT_LENGTH']          = 10 * 1024 * 1024   # 10 MB upload limit

    # Secure session cookies in production
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE']   = not debug   # requires HTTPS in prod

    # Google OAuth
    app.config['GOOGLE_CLIENT_ID']     = os.environ.get('GOOGLE_CLIENT_ID', '')
    app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET', '')

    # Resend
    resend.api_key = os.environ.get('RESEND_API_KEY', '')

    db.init_app(app)
    oauth.init_app(app)

    oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

    app.extensions['oauth'] = oauth

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(expenses_bp)
    app.register_blueprint(import_export_bp)
    app.register_blueprint(dex_bp)
    app.register_blueprint(admin_bp)

    app.before_request(redirect_admin_from_user_app)

    @app.context_processor
    def inject_user():
        user_id = session.get('user_id')
        user = db.session.get(User, user_id) if user_id else None
        code = (user.currency if user and user.currency in CURRENCIES
                else DEFAULT_CURRENCY)
        g.user_currency = code
        return {
            'current_user': user,
            'currency_code': code,
            'currency_symbol': CURRENCIES[code]['symbol'],
            'currencies': CURRENCIES,
        }

    app.jinja_env.filters['enumerate'] = enumerate
    app.jinja_env.filters['money'] = format_money

    @app.errorhandler(404)
    def page_not_found(e):
        return render_template('404.html'), 404

    with app.app_context():
        db.create_all()
        with db.engine.connect() as conn:
            # Lightweight, idempotent column adds for existing databases.
            migrations = [
                "ALTER TABLE expenses ADD COLUMN paid_by_user BOOLEAN NOT NULL DEFAULT 1",
                "ALTER TABLE users ADD COLUMN dex_starters TEXT",
                "ALTER TABLE users ADD COLUMN dex_starters_at DATETIME",
                "ALTER TABLE users ADD COLUMN currency VARCHAR(3) NOT NULL DEFAULT 'USD'",
                "ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'",
                "ALTER TABLE users ADD COLUMN subscribed_at DATETIME",
            ]
            for stmt in migrations:
                try:
                    conn.execute(db.text(stmt))
                    conn.commit()
                except Exception:
                    conn.rollback()

        sync_admin_role()

    return app


app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=app.config['DEBUG'])
