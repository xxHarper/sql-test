"""Candidate flow. Public question projections deliberately exclude grading data."""
import json
import re
import secrets

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for

from .db import check_limit, event, failed_attempt, finish, get_db, now
from .exams import active_versions, public_questions, version_info

bp = Blueprint('candidate', __name__)


def active_evaluation():
    token = session.get('candidate_token')
    row = get_db().execute('SELECT * FROM evaluations WHERE candidate_token=?', (token,)).fetchone() if token else None
    if row is None:
        abort(403, 'Accede con tu nombre y código de evaluación.')
    return row


@bp.get('/')
def welcome():
    evaluation = None
    if session.get('candidate_token'):
        evaluation = get_db().execute('SELECT status FROM evaluations WHERE candidate_token=?', (session['candidate_token'],)).fetchone()
    return render_template('welcome.html', evaluation=evaluation, exam=version_info(active_versions()[0]['id']))


@bp.route('/acceso', methods=['GET', 'POST'])
def access():
    if request.method == 'POST':
        bucket = 'candidate:' + (request.remote_addr or '')
        check_limit(bucket, 20)
        name = ' '.join(request.form.get('name', '').split())
        code = request.form.get('code', '').strip().upper()
        if not 3 <= len(name) <= 120 or any(ord(c) < 32 for c in name):
            flash('Escribe tu nombre completo, entre 3 y 120 caracteres.', 'error')
            return render_template('access.html'), 400
        if not re.fullmatch(r'SQL-[A-Z2-9]{8}', code):
            failed_attempt(bucket)
            flash('El código no está disponible. Revisa el código con el administrador.', 'error')
            return render_template('access.html'), 400
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            evaluation = db.execute('SELECT * FROM evaluations WHERE code=?', (code,)).fetchone()
            if evaluation and evaluation['status'] == 'not_started':
                token = secrets.token_urlsafe(32)
                # Claim the one-use code now; the timer only starts after instructions.
                if evaluation['candidate_token'] is not None:
                    evaluation = None
                else:
                    db.execute('UPDATE evaluations SET name=?, candidate_token=? WHERE id=?', (name, token, evaluation['id']))
                    event(db, evaluation['id'], 'Acceso registrado; pendiente de inicio')
            else:
                evaluation = None
        if evaluation is None:
            failed_attempt(bucket)
            flash('El código no está disponible. Si ya accediste, continúa desde el navegador original.', 'error')
            return render_template('access.html'), 400
        session['candidate_token'] = token
        session.permanent = True
        return redirect(url_for('candidate.instructions'))
    return render_template('access.html')


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
    session.pop('candidate_token', None)
    return redirect(url_for('candidate.welcome'))
