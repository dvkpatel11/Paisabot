from flask import Blueprint

api_bp = Blueprint('api', __name__)

from app.api import routes  # noqa: E402, F401
from app.api import research_routes  # noqa: E402, F401
from app.api import simulation_routes  # noqa: E402, F401
from app.api import service_routes  # noqa: E402, F401
