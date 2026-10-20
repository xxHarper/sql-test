"""Application factory for SQL Assessment. No external services are used."""
import hmac
import ipaddress
import secrets
import time
from datetime import timedelta
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, session


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=str(Path(app.instance_path) / 'assessment.sqlite3'),
        SECRET_KEY=None,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Strict',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        # Up to 200 questions, including their longest allowed UTF-8 answers.
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
        EXAMS_DIR=str(Path(app.root_path).parent / 'evaluaciones'),
        LOCAL_ONLY=True,
        NOW=time.time,
    )
    if config:
        app.config.update(config)
    from .exams import load_documents
    documents = load_documents(app.config['EXAMS_DIR'])
    Path(app.config['DATABASE']).parent.mkdir(parents=True, exist_ok=True)
    from . import db
    db.init_app(app)
    with app.app_context():
        app.extensions['exam_imports'] = db.initialize(documents)
        # Persist the random key in the local database, never in source control.
        row = db.get_db().execute("SELECT value FROM settings WHERE key='secret_key'").fetchone()
        if row is None:
            db.get_db().execute("INSERT OR IGNORE INTO settings VALUES ('secret_key', ?)", (secrets.token_hex(32),))
            db.get_db().commit()
            row = db.get_db().execute("SELECT value FROM settings WHERE key='secret_key'").fetchone()
        if not app.config['SECRET_KEY']:
            app.config['SECRET_KEY'] = row['value']

    @app.before_request
    def protect_request():
        if app.config['LOCAL_ONLY']:
            try:
                address = ipaddress.ip_address(request.remote_addr or '')
                if address.version == 6 and address.ipv4_mapped:
                    address = address.ipv4_mapped
                networks = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8', '::1/128', 'fc00::/7', 'fe80::/10')
                if not any(address in ipaddress.ip_network(n) for n in networks):
                    abort(403, 'El servicio solo acepta conexiones locales o de la red privada.')
            except ValueError:
                abort(403)
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_hex(32)
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
            if not isinstance(token, str) or not token.isascii() or not hmac.compare_digest(token, session['csrf_token']):
                abort(400, 'La sesión de este formulario caducó. Recarga la página e intenta nuevamente.')
        # Deadlines are enforced on every request and also by the server worker.
        if request.endpoint != 'static':
            db.expire_attempts()

    @app.after_request
    def headers(response):
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.context_processor
    def template_helpers():
        return {'csrf_token': session.get('csrf_token', ''), 'status_labels': db.STATUS_LABELS}

    @app.template_filter('moment')
    def moment(value):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(value, timezone.utc).strftime('%d/%m/%Y %H:%M UTC') if value is not None else '—'

    @app.template_filter('fromjson')
    def fromjson(value):
        import json
        return json.loads(value)

    @app.template_filter('elapsed')
    def elapsed(value):
        seconds = int(value or 0)
        return f'{seconds // 60:02d}:{seconds % 60:02d}'

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(413)
    @app.errorhandler(429)
    def handle_error(error):
        if request.path.startswith('/api/'):
            return jsonify(error=str(error.description)), error.code
        return render_template('error.html', error=error), error.code

    from .candidate import bp as candidate_bp
    from .admin import bp as admin_bp
    app.register_blueprint(candidate_bp)
    app.register_blueprint(admin_bp)
    return app
