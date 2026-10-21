"""Candidate flow. Public question projections deliberately exclude grading data."""
import json
import re
import secrets
import hashlib
import io

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from .db import check_limit, event, failed_attempt, finish, get_db, now
from .exams import active_versions, public_questions, version_info

bp = Blueprint('candidate', __name__)


def active_evaluation():
    token = session.get('candidate_token')
    row = get_db().execute('SELECT * FROM evaluations WHERE candidate_token=?', (token,)).fetchone() if token else None
    if row is None:
        abort(403, 'Accede con tu nombre y código de evaluación.')
    expected = request.headers.get('X-Evaluation-ID') or request.form.get('evaluation_id')
    if expected is not None and expected != str(row['id']):
        abort(409, 'Cambiaste de evaluación en otra pestaña. Recarga antes de continuar.')
    ensure_identity(row)
    return row


def ensure_identity(evaluation):
    """Upgrade an existing authenticated session without altering its attempt or token."""
    db = get_db()
    identity = db.execute('''SELECT c.* FROM candidates c JOIN candidate_attempts a ON a.candidate_id=c.id
        WHERE a.evaluation_id=?''', (evaluation['id'],)).fetchone()
    if identity is None:
        with db:
            db.execute('BEGIN IMMEDIATE')
            identity = db.execute('''SELECT c.* FROM candidates c JOIN candidate_attempts a ON a.candidate_id=c.id
                WHERE a.evaluation_id=?''', (evaluation['id'],)).fetchone()
            if identity is None:
                cursor = db.execute('INSERT INTO candidates(token,name,created_at) VALUES (?,?,?)',
                                    (secrets.token_urlsafe(32), evaluation['name'], now()))
                db.execute('INSERT INTO candidate_attempts VALUES (?,?)', (evaluation['id'], cursor.lastrowid))
                identity = db.execute('SELECT * FROM candidates WHERE id=?', (cursor.lastrowid,)).fetchone()
    session['candidate_identity'] = identity['token']
    return identity


def current_identity():
    token = session.get('candidate_token')
    evaluation = get_db().execute('SELECT * FROM evaluations WHERE candidate_token=?', (token,)).fetchone() if token else None
    if evaluation:
        return ensure_identity(evaluation)
    return get_db().execute('SELECT * FROM candidates WHERE token=?', (session.get('candidate_identity'),)).fetchone()


@bp.get('/')
@bp.get('/evaluaciones')
def welcome():
    identity = current_identity()
    attempts = get_db().execute('''SELECT e.id,e.status,v.exam_id,v.title,v.version FROM evaluations e
        JOIN candidate_attempts a ON a.evaluation_id=e.id JOIN exam_versions v ON v.id=e.version_id
        WHERE a.candidate_id=? ORDER BY e.id DESC''', (identity['id'],)).fetchall() if identity else []
    return render_template('welcome.html', identity=identity, attempts=attempts,
                           exams=[version_info(v['id']) for v in active_versions()])


@bp.post('/seleccionar/<int:evaluation_id>')
def select_attempt(evaluation_id):
    identity = current_identity()
    row = get_db().execute('''SELECT e.* FROM evaluations e JOIN candidate_attempts a ON a.evaluation_id=e.id
        WHERE e.id=? AND a.candidate_id=?''', (evaluation_id, identity['id'] if identity else None)).fetchone()
    if row is None:
        abort(403)
    session['candidate_token'] = row['candidate_token']
    return redirect(url_for('candidate.instructions' if row['status'] == 'not_started' else 'candidate.exam'))


@bp.route('/acceso', methods=['GET', 'POST'])
def access():
    identity = current_identity()
    selected = request.form.get('exam_id', request.args.get('exam_id', ''))
    versions = active_versions()
    selected_exam = next((version_info(v['id']) for v in versions if v['exam_id'] == selected), None)
    if selected and selected_exam is None:
        abort(404, 'Evaluación no disponible.')
    def access_page():
        return render_template('access.html', identity=identity, selected_exam=selected_exam)
    if request.method == 'POST':
        bucket = 'candidate:' + (request.remote_addr or '')
        check_limit(bucket, 20)
        name = identity['name'] if identity else ' '.join(request.form.get('name', '').split())
        code = request.form.get('code', '').strip().upper()
        if not 3 <= len(name) <= 120 or any(ord(c) < 32 for c in name):
            flash('Escribe tu nombre completo, entre 3 y 120 caracteres.', 'error')
            return access_page(), 400
        if not re.fullmatch(r'(?:SQL|EXC)-[A-Z2-9]{8}', code):
            failed_attempt(bucket)
            flash('El código no está disponible. Revisa el código con el administrador.', 'error')
            return access_page(), 400
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            evaluation = db.execute('SELECT * FROM evaluations WHERE code=?', (code,)).fetchone()
            if evaluation and selected and version_info(evaluation['version_id'])['exam_id'] != selected:
                evaluation = None
            if evaluation and evaluation['status'] == 'not_started':
                token = secrets.token_urlsafe(32)
                # Claim the one-use code now; the timer only starts after instructions.
                if evaluation['candidate_token'] is not None:
                    evaluation = None
                else:
                    db.execute('UPDATE evaluations SET name=?, candidate_token=? WHERE id=?', (name, token, evaluation['id']))
                    if identity:
                        db.execute('INSERT INTO candidate_attempts VALUES (?,?)', (evaluation['id'], identity['id']))
                    event(db, evaluation['id'], 'Acceso registrado; pendiente de inicio')
            else:
                evaluation = None
        if evaluation is None:
            failed_attempt(bucket)
            flash('El código no está disponible. Si ya accediste, continúa desde el navegador original.', 'error')
            return access_page(), 400
        session['candidate_token'] = token
        ensure_identity(db.execute('SELECT * FROM evaluations WHERE candidate_token=?', (token,)).fetchone())
        session.permanent = True
        return redirect(url_for('candidate.instructions'))
    return access_page()


@bp.get('/recursos/<int:evaluation_id>/<resource_id>')
def download_resource(evaluation_id, resource_id):
    evaluation = active_evaluation()
    if evaluation['id'] != evaluation_id:
        abort(403)
    resource = get_db().execute('SELECT * FROM exam_resources WHERE version_id=? AND resource_id=?',
                                (evaluation['version_id'], resource_id)).fetchone()
    if resource is None:
        abort(404)
    if hashlib.sha256(resource['content']).hexdigest() != resource['sha256']:
        abort(409, 'El recurso no pasó la verificación de integridad. Contacta al administrador.')
    return send_file(io.BytesIO(resource['content']), mimetype=resource['mime_type'],
                     as_attachment=True, download_name=resource['filename'], max_age=0)


@bp.get('/instrucciones')
def instructions():
    evaluation = active_evaluation()
    if evaluation['status'] != 'not_started':
        return redirect(url_for('candidate.exam'))
    return render_template('instructions.html', evaluation=evaluation, exam=version_info(evaluation['version_id']))


@bp.post('/comenzar')
def start():
    evaluation = active_evaluation()
    db = get_db()
    with db:
        db.execute('BEGIN IMMEDIATE')
        evaluation = db.execute('SELECT * FROM evaluations WHERE id=?', (evaluation['id'],)).fetchone()
        if evaluation['status'] == 'not_started':
            timestamp = now()
            db.execute("UPDATE evaluations SET status='in_progress',started_at=?,deadline=? WHERE id=?",
                       (timestamp, timestamp + version_info(evaluation['version_id'])['duration_minutes'] * 60, evaluation['id']))
            db.execute("INSERT INTO answers(evaluation_id,question_id,updated_at) SELECT ?,id,? FROM questions WHERE version_id=?", (evaluation['id'], timestamp, evaluation['version_id']))
            event(db, evaluation['id'], 'Evaluación iniciada', timestamp)
    return redirect(url_for('candidate.exam'))


@bp.get('/evaluacion')
def exam():
    evaluation = active_evaluation()
    if evaluation['status'] == 'not_started':
        return redirect(url_for('candidate.instructions'))
    if evaluation['status'] != 'in_progress':
        return redirect(url_for('candidate.thanks'))
    return render_template('exam.html', evaluation=evaluation, questions=public_questions(evaluation['version_id']), exam=version_info(evaluation['version_id']))


@bp.get('/api/evaluacion')
def state():
    evaluation = active_evaluation()
    # No score, rubric, answer key, or admin comments in this response.
    answers = get_db().execute('''SELECT q.position AS question_id,a.answer FROM answers a JOIN questions q ON q.id=a.question_id
        WHERE a.evaluation_id=? ORDER BY q.position''', (evaluation['id'],)).fetchall()
    return jsonify(status=evaluation['status'], deadline=evaluation['deadline'], server_now=now(),
                   revision=evaluation['revision'], answers={str(a['question_id']): a['answer'] for a in answers})


def parse_payload(evaluation):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or type(payload.get('revision')) is not int or payload['revision'] < 0:
        abort(400, 'Formato de guardado inválido.')
    answers = payload.get('answers')
    rules = {str(q['position']): q for q in get_db().execute('SELECT id,position,kind,options FROM questions WHERE version_id=?', (evaluation['version_id'],))}
    if not isinstance(answers, dict) or len(answers) > len(rules):
        abort(400, 'Respuestas inválidas.')
    for key, value in answers.items():
        if key not in rules or not isinstance(value, str) or len(value) > 12000 or '\x00' in value:
            abort(400, 'Cada respuesta debe tener como máximo 12,000 caracteres.')
        if rules[key]['kind'] == 'choice' and value not in ['', *[o['id'] for o in json.loads(rules[key]['options'])]]:
            abort(400, 'Opción inválida.')
    return payload, rules


def save_or_submit(submit=False):
    evaluation = active_evaluation()
    payload, rules = parse_payload(evaluation)
    db = get_db()
    with db:
        db.execute('BEGIN IMMEDIATE')
        evaluation = db.execute('SELECT * FROM evaluations WHERE id=?', (evaluation['id'],)).fetchone()
        timestamp = now()
        if evaluation['status'] == 'in_progress' and timestamp >= evaluation['deadline']:
            finish(db, evaluation, 'timeout', timestamp)
            return jsonify(status='expired', error='El tiempo terminó. Se entregaron las respuestas guardadas.'), 409
        if evaluation['status'] != 'in_progress':
            return jsonify(status=evaluation['status'], error='La evaluación ya no admite cambios.'), 409
        if payload['revision'] != evaluation['revision']:
            return jsonify(status='conflict', error='Otra pestaña guardó cambios. Recarga para recuperar la versión del servidor.'), 409
        for key, value in payload['answers'].items():
            db.execute('UPDATE answers SET answer=?,updated_at=? WHERE evaluation_id=? AND question_id=?',
                       (value, timestamp, evaluation['id'], rules[key]['id']))
        revision = evaluation['revision'] + 1
        db.execute('UPDATE evaluations SET revision=? WHERE id=?', (revision, evaluation['id']))
        if submit:
            finish(db, evaluation, 'voluntary', timestamp)
        return jsonify(status='submitted' if submit else 'in_progress', revision=revision, server_now=timestamp, deadline=evaluation['deadline'])


@bp.post('/api/respuestas')
def save():
    return save_or_submit()


@bp.post('/api/entregar')
def submit():
    return save_or_submit(submit=True)


@bp.get('/gracias')
def thanks():
    evaluation = active_evaluation()
    if evaluation['status'] in ('not_started', 'in_progress'):
        return redirect(url_for('candidate.exam'))
    return render_template('thanks.html', evaluation=evaluation)


@bp.post('/salir')
def leave():
    evaluation = active_evaluation()
    if evaluation['status'] in ('not_started', 'in_progress'):
        abort(409, 'Conserva esta sesión hasta entregar tu evaluación.')
    identity = current_identity()
    if get_db().execute('''SELECT 1 FROM evaluations e JOIN candidate_attempts a ON a.evaluation_id=e.id
        WHERE a.candidate_id=? AND e.status IN ('not_started','in_progress')''', (identity['id'],)).fetchone():
        abort(409, 'Tienes otra evaluación pendiente. Vuelve a Evaluaciones y entrégala antes de finalizar la sesión.')
    session.pop('candidate_token', None)
    session.pop('candidate_identity', None)
    return redirect(url_for('candidate.welcome'))
