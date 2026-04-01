import os
from flask import Flask, session
from models import db, User
from routes.auth import auth_bp
from routes.main import main_bp
from routes.expenses import expenses_bp


def create_app():
    app = Flask(__name__)

    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///balance_desk.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    db.init_app(app)

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
