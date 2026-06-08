import os
import resend
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, session
from werkzeug.middleware.proxy_fix import ProxyFix
from authlib.integrations.flask_client import OAuth
from models import db, User
from routes.auth import auth_bp
from routes.main import main_bp
from routes.expenses import expenses_bp
from routes.import_export import import_export_bp
from routes.dex import dex_bp

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

    @app.context_processor
    def inject_user():
        user_id = session.get('user_id')
        if user_id:
            user = db.session.get(User, user_id)
            return {'current_user': user}
        return {'current_user': None}

    app.jinja_env.filters['enumerate'] = enumerate

    with app.app_context():
        db.create_all()
        with db.engine.connect() as conn:
            try:
                conn.execute(db.text(
                    "ALTER TABLE expenses ADD COLUMN paid_by_user BOOLEAN NOT NULL DEFAULT 1"
                ))
                conn.commit()
            except Exception:
                pass

    return app


app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=app.config['DEBUG'])
