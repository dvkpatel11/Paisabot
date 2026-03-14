from flask import Blueprint

views_bp = Blueprint(
    'views', __name__,
    template_folder='../../templates',
    static_folder='../../static',
)

from app.views import routes  # noqa: E402, F401
