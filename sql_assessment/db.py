"""SQLite persistence. All candidate input is stored using bound parameters."""
import sqlite3
from flask import current_app, g

STATUS_LABELS = {'not_started': 'No iniciado', 'in_progress': 'En proceso', 'submitted': 'Entregado', 'expired': 'Vencido', 'graded': 'Calificado'}


def now():
    return int(current_app.config['NOW']())


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE'], timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
        g.db.execute('PRAGMA busy_timeout=15000')
    return g.db


def init_app(app):
    @app.teardown_appcontext
    def close_db(exception=None):
        connection = g.pop('db', None)
        if connection is not None:
            connection.close()


def initialize(documents):
    db = get_db()
    backup_before_upgrade(db)
    db.execute('PRAGMA journal_mode=WAL')
    schema = '''
        CREATE TABLE IF NOT EXISTS exam_versions (
            id INTEGER PRIMARY KEY, exam_id TEXT NOT NULL, version INTEGER NOT NULL,
            content_hash TEXT NOT NULL, source_json TEXT NOT NULL,
            title TEXT NOT NULL, description TEXT NOT NULL, duration_seconds INTEGER NOT NULL,
            max_score REAL NOT NULL, created_at INTEGER NOT NULL, source_file TEXT NOT NULL,
            UNIQUE(exam_id,version), UNIQUE(exam_id,content_hash)
        );
        CREATE TABLE IF NOT EXISTS exam_catalog (
            exam_id TEXT PRIMARY KEY, active_version_id INTEGER NOT NULL REFERENCES exam_versions(id),
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY, section TEXT NOT NULL, kind TEXT NOT NULL,
            title TEXT NOT NULL, prompt TEXT NOT NULL, points INTEGER NOT NULL,
            options TEXT, correct TEXT, guide TEXT NOT NULL, rubric TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT,
            status TEXT NOT NULL DEFAULT 'not_started'
                CHECK(status IN ('not_started','in_progress','submitted','expired','graded')),
            created_at INTEGER NOT NULL, started_at INTEGER, deadline INTEGER,
            submitted_at INTEGER, graded_at INTEGER, candidate_token TEXT UNIQUE,
            revision INTEGER NOT NULL DEFAULT 0, delivery_reason TEXT,
            CHECK (name IS NULL OR length(name) BETWEEN 3 AND 120)
        );
        CREATE TABLE IF NOT EXISTS answers (
            evaluation_id INTEGER NOT NULL REFERENCES evaluations(id),
            question_id INTEGER NOT NULL REFERENCES questions(id),
            answer TEXT NOT NULL DEFAULT '', score REAL, comment TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(evaluation_id, question_id)
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, evaluation_id INTEGER REFERENCES evaluations(id),
            action TEXT NOT NULL, at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rate_limits (
            bucket TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_started INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY, token TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS candidate_attempts (
            evaluation_id INTEGER PRIMARY KEY REFERENCES evaluations(id),
            candidate_id INTEGER NOT NULL REFERENCES candidates(id)
        );
        CREATE TABLE IF NOT EXISTS exam_resources (
            version_id INTEGER NOT NULL REFERENCES exam_versions(id), resource_id TEXT NOT NULL,
            filename TEXT NOT NULL, label TEXT NOT NULL, mime_type TEXT NOT NULL,
            sha256 TEXT NOT NULL, content BLOB NOT NULL,
            PRIMARY KEY(version_id,resource_id)
        );
    '''
    from .exams import import_documents
    with db:
        db.execute('BEGIN IMMEDIATE')
        # executescript would implicitly commit; individual statements preserve atomic DDL/import.
        for statement in schema.split(';'):
            if statement.strip():
                db.execute(statement)
        additions = {
            'questions': {
                'version_id':'INTEGER REFERENCES exam_versions(id)', 'source_id':'TEXT',
                'position':'INTEGER', 'section_id':'TEXT', 'tables_json':"TEXT NOT NULL DEFAULT '[]'",
                'grading_json':"TEXT NOT NULL DEFAULT '{}'", 'source_json':"TEXT NOT NULL DEFAULT '{}'"},
            'evaluations': {'version_id':'INTEGER REFERENCES exam_versions(id)'}}
        for table, columns in additions.items():
            existing = {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
            for name, definition in columns.items():
                if name not in existing:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        from .legacy import migrate_legacy
        migrate_legacy(db, now())
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS questions_version_source ON questions(version_id,source_id)')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS questions_version_position ON questions(version_id,position)')
        results = import_documents(db, documents, now())
        for operation in ('UPDATE', 'DELETE'):
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS immutable_resources_{operation.lower()}
                BEFORE {operation} ON exam_resources
                BEGIN SELECT RAISE(ABORT, 'Los recursos publicados son inmutables'); END''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS immutable_candidate_attempt BEFORE UPDATE ON candidate_attempts
            BEGIN SELECT RAISE(ABORT, 'No se puede cambiar la identidad de un intento'); END''')
        # Content is immutable even before attempts exist. Scores/comments live in answers.
        for table in ('exam_versions', 'questions'):
            for operation in ('UPDATE', 'DELETE'):
                condition = ' WHEN OLD.version_id IS NOT NULL' if table == 'questions' else ''
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation.lower()}
                    BEFORE {operation} ON {table}{condition}
                    BEGIN SELECT RAISE(ABORT, 'Las versiones importadas son inmutables'); END''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS immutable_attempt_version BEFORE UPDATE OF version_id ON evaluations
            WHEN OLD.version_id IS NOT NEW.version_id
            BEGIN SELECT RAISE(ABORT, 'No se puede cambiar la versión de un intento'); END''')
    return results


def backup_before_upgrade(db):
    """Use SQLite's online backup API to include WAL data before additive DDL."""
    from datetime import datetime, timezone
    from pathlib import Path
    from uuid import uuid4
    from contextlib import closing
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not tables or {'candidates', 'candidate_attempts', 'exam_resources'} <= tables:
        return
    directory = Path(current_app.config['DATABASE']).resolve().parent / 'backups'
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = directory / f'pre-excel-{stamp}-{uuid4().hex[:8]}.sqlite3'
    with closing(sqlite3.connect(path)) as destination:
        db.backup(destination)
    current_app.extensions['migration_backup'] = str(path)


def event(db, evaluation_id, action, timestamp=None):
    db.execute('INSERT INTO events(evaluation_id,action,at) VALUES (?,?,?)',
               (evaluation_id, action, now() if timestamp is None else timestamp))


def finish(db, evaluation, reason, timestamp):
    """Called inside a write transaction; objective scoring remains server-only."""
    if evaluation['status'] != 'in_progress':
        return
    expired = reason == 'timeout'
    submitted_at = evaluation['deadline'] if expired else timestamp
    db.execute('''UPDATE evaluations SET status=?,submitted_at=?,delivery_reason=?
                  WHERE id=? AND status='in_progress' ''',
               ('expired' if expired else 'submitted', submitted_at, reason, evaluation['id']))
    for question in db.execute("SELECT id, correct, points FROM questions WHERE kind='choice' AND version_id=?", (evaluation['version_id'],)).fetchall():
        answer = db.execute('SELECT answer FROM answers WHERE evaluation_id=? AND question_id=?',
                            (evaluation['id'], question['id'])).fetchone()
        score = question['points'] if answer and answer['answer'] == question['correct'] else 0
        db.execute('UPDATE answers SET score=? WHERE evaluation_id=? AND question_id=?',
                   (score, evaluation['id'], question['id']))
    event(db, evaluation['id'], 'Tiempo agotado; entrega automática' if expired else 'Evaluación entregada', submitted_at)


def expire_attempts():
    db = get_db()
    timestamp = now()
    # Avoid acquiring a write lock for ordinary reads when nothing has expired.
    if not db.execute("SELECT 1 FROM evaluations WHERE status='in_progress' AND deadline<=? LIMIT 1", (timestamp,)).fetchone():
        return
    with db:
        db.execute('BEGIN IMMEDIATE')
        for evaluation in db.execute("SELECT * FROM evaluations WHERE status='in_progress' AND deadline<=?", (timestamp,)).fetchall():
            finish(db, evaluation, 'timeout', timestamp)


def classification(total):
    if total >= 85:
        return 'Muy buen candidato'
    if total >= 70:
        return 'Buen candidato'
    if total >= 55:
        return 'Con potencial'
    return 'Requiere reforzar conocimientos'


def check_limit(bucket, limit):
    from flask import abort
    row = get_db().execute('SELECT * FROM rate_limits WHERE bucket=?', (bucket,)).fetchone()
    if row and now() - row['window_started'] < 300 and row['failures'] >= limit:
        abort(429, 'Demasiados intentos. Espera cinco minutos antes de volver a intentar.')


def failed_attempt(bucket):
    db = get_db()
    with db:
        db.execute('''INSERT INTO rate_limits VALUES (?,1,?) ON CONFLICT(bucket) DO UPDATE SET
            failures=CASE WHEN ?-window_started>=300 THEN 1 ELSE failures+1 END,
            window_started=CASE WHEN ?-window_started>=300 THEN ? ELSE window_started END''',
            (bucket, now(), now(), now(), now()))
