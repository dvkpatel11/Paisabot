import os

import eventlet

eventlet.monkey_patch()

from app import create_app

config_name = os.environ.get('FLASK_CONFIG', 'production')
app = create_app(config_name)

if __name__ == "__main__":
    from app.extensions import socketio

    debug = config_name == 'development'
    socketio.run(app, debug=debug, host="0.0.0.0", port=5000)
