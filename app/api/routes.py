from flask import jsonify

from app.api import api_bp


@api_bp.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})
