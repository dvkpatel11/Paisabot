from flask import jsonify
from flask_login import login_required

from app.views import views_bp


@views_bp.route('/')
@login_required
def index():
    return jsonify(status='ok', message='Paisabot API is running. No frontend served.')
