import os
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, session
from authlib.integrations.flask_client import OAuth
from models import db, User
from routes.auth import auth_bp
from routes.main import main_bp
from routes.expenses import expenses_bp

oauth = OAuth()


def create_app():
    app = Flask(__name__)

    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///balance_desk.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Google OAuth config — set these in your environment
    app.config['GOOGLE_CLIENT_ID']     = os.environ.get('GOOGLE_CLIENT_ID', '')
    app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET', '')

    db.init_app(app)
    oauth.init_app(app)

    oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

    # Make oauth accessible inside blueprints
    app.extensions['oauth'] = oauth

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(expenses_bp)

    @app.context_processor
    def inject_user():
        user_id = session.get('user_id')
        if user_id:
            user = db.session.get(User, user_id)
            return {'current_user': user}
        return {'current_user': None}

    with app.app_context():
        db.create_all()

    return app


app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5001)
