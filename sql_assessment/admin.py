"""Authenticated administration and manual grading."""
import csv
import io
import math
import secrets
from functools import wraps

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from .db import STATUS_LABELS, check_limit, classification, event, failed_attempt, get_db, now
from .exams import active_versions, admin_questions, version_info

bp = Blueprint('admin', __name__, url_prefix='/admin')


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('admin_until', 0) > now():
            session.pop('admin_until', None)
            return redirect(url_for('admin.login'))
        return view(*args, **kwargs)
    return wrapped


@bp.route('/login', methods=['GET', 'POST'])
def login():
    configured = get_db().execute("SELECT value FROM settings WHERE key='admin_password'").fetchone()
    if request.method == 'POST':
        bucket = 'admin:' + (request.remote_addr or '')
        check_limit(bucket, 5)
        password = request.form.get('password', '')
        if configured and len(password) <= 256 and check_password_hash(configured['value'], password):
            # Retain a candidate session when both portals are used on one device.
            session['admin_until'] = now() + 8 * 3600
            session['csrf_token'] = secrets.token_hex(32)
            session.permanent = True
            with get_db() as db:
                db.execute('DELETE FROM rate_limits WHERE bucket=?', (bucket,))
            return redirect(url_for('admin.dashboard'))
        failed_attempt(bucket)
        flash('Contraseña incorrecta o administrador sin configurar.', 'error')
        return render_template('admin/login.html', configured=configured), 401
    return render_template('admin/login.html', configured=configured)


@bp.post('/logout')
@login_required
def logout():
    session.pop('admin_until', None)
    return redirect(url_for('admin.login'))


def result_rows():
    return get_db().execute('''SELECT e.*, COALESCE(SUM(a.score),0) AS total,
        COUNT(CASE WHEN q.kind<>'choice' AND a.score IS NOT NULL THEN 1 END) AS reviewed,
        (SELECT COUNT(*) FROM questions WHERE version_id=e.version_id AND kind<>'choice') AS manual_count,
        v.title AS exam_title,v.exam_id,v.version,ca.candidate_id,
        COALESCE(SUM(CASE WHEN a.answer<>'' THEN 1 ELSE 0 END),0) AS answered
        FROM evaluations e JOIN exam_versions v ON v.id=e.version_id
        LEFT JOIN candidate_attempts ca ON ca.evaluation_id=e.id
        LEFT JOIN answers a ON a.evaluation_id=e.id LEFT JOIN questions q ON q.id=a.question_id
        GROUP BY e.id ORDER BY e.created_at DESC,e.id DESC''').fetchall()


def filter_results(evaluations):
    exam_id = request.args.get('examen', '')
    status = request.args.get('estado', '')
    query = request.args.get('q', '').strip()[:120]
    return [e for e in evaluations if (not exam_id or e['exam_id'] == exam_id) and
            (not status or e['status'] == status) and
            (not query or query.casefold() in ((e['name'] or '') + ' ' + e['code']).casefold())]


@bp.get('')
@bp.get('/')
@login_required
def dashboard():
    evaluations = result_rows()
    status = request.args.get('estado', '')
    query = request.args.get('q', '').strip()[:120]
    selected_exam = request.args.get('examen', '')
    scoped = [e for e in evaluations if not selected_exam or e['exam_id'] == selected_exam]
    counts = {key: sum(e['status'] == key for e in scoped) for key in STATUS_LABELS}
    exams = get_db().execute('SELECT exam_id,MAX(title) AS title FROM exam_versions GROUP BY exam_id ORDER BY exam_id').fetchall()
    return render_template('admin/dashboard.html', evaluations=filter_results(evaluations), counts=counts, total=len(scoped),
                           selected_exam=selected_exam, exam_choices=exams,
                           selected_status=status, query=query, current_time=now(), classification=classification, admin_mode=True, versions=active_versions())


@bp.post('/codigos')
@login_required
def create_codes():
    try:
        count = int(request.form.get('count', '1'))
    except ValueError:
        abort(400, 'Cantidad inválida.')
    if not 1 <= count <= 25:
        abort(400, 'Genera entre 1 y 25 códigos a la vez.')
    db = get_db()
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    versions = active_versions()
    version_id = request.form.get('version_id', str(versions[0]['id']))
    if version_id not in {str(v['id']) for v in versions}:
        abort(400, 'Selecciona una versión vigente de evaluación.')
    selected_version = next(v for v in versions if str(v['id']) == version_id)
    prefix = 'EXC-' if selected_version['exam_id'] == 'EXCEL_JUNIOR' else 'SQL-'
    with db:
        for _ in range(count):
            while True:
                code = prefix + ''.join(secrets.choice(alphabet) for _ in range(8))
                if not db.execute('SELECT 1 FROM evaluations WHERE code=?', (code,)).fetchone():
                    break
            cursor = db.execute('INSERT INTO evaluations(code,created_at,version_id) VALUES (?,?,?)', (code, now(), int(version_id)))
            event(db, cursor.lastrowid, 'Código de un solo uso creado')
    flash(f'Se generaron {count} códigos de evaluación.', 'success')
    return redirect(url_for('admin.dashboard'))


@bp.get('/candidatos/<int:candidate_id>')
@login_required
def candidate_detail(candidate_id):
    candidate = get_db().execute('SELECT id,name FROM candidates WHERE id=?', (candidate_id,)).fetchone()
    if candidate is None:
        abort(404)
    evaluations = [e for e in result_rows() if e['candidate_id'] == candidate_id]
    return render_template('admin/candidate.html', candidate=candidate, evaluations=evaluations,
                           classification=classification, current_time=now(), admin_mode=True)


@bp.route('/evaluaciones/<int:evaluation_id>', methods=['GET', 'POST'])
@login_required
def detail(evaluation_id):
    db = get_db()
    evaluation = db.execute('SELECT * FROM evaluations WHERE id=?', (evaluation_id,)).fetchone()
    if evaluation is None:
        abort(404)
    questions = admin_questions(evaluation['version_id'], evaluation_id)
    exam = version_info(evaluation['version_id'])
    if request.method == 'POST':
        if evaluation['status'] not in ('submitted', 'expired', 'graded'):
            abort(409, 'Solo puedes calificar evaluaciones entregadas o vencidas.')
        values = []
        for q in questions:
            raw_score = request.form.get(f'score_{q["id"]}', '').strip()
            comment = request.form.get(f'comment_{q["id"]}', '').strip()
            if len(comment) > 3000:
                abort(400, 'Los comentarios admiten hasta 3,000 caracteres.')
            score = q['score']
            if q['kind'] != 'choice':
                try:
                    score = float(raw_score) if raw_score else None
                except ValueError:
                    abort(400, 'Puntaje inválido.')
                if score is not None and (not math.isfinite(score) or not 0 <= score <= q['points']):
                    abort(400, f'El puntaje de la pregunta {q["id"]} debe estar entre 0 y {q["points"]}.')
            values.append((score, comment, evaluation_id, q['db_id']))
        final = request.form.get('action') == 'finalize'
        if final and any(values[i][0] is None for i, q in enumerate(questions) if q['kind'] != 'choice'):
            flash(f'Asigna un puntaje a las {exam["manual_count"]} preguntas manuales antes de finalizar.', 'error')
            return redirect(url_for('admin.detail', evaluation_id=evaluation_id))
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.executemany('UPDATE answers SET score=?,comment=? WHERE evaluation_id=? AND question_id=?', values)
            if final:
                db.execute("UPDATE evaluations SET status='graded',graded_at=? WHERE id=?", (now(), evaluation_id))
            elif evaluation['status'] == 'graded' and any(v[0] is None for v in values):
                db.execute('UPDATE evaluations SET status=?,graded_at=NULL WHERE id=?',
                           ('expired' if evaluation['delivery_reason'] == 'timeout' else 'submitted', evaluation_id))
            event(db, evaluation_id, 'Calificación finalizada' if final else 'Revisión guardada')
        flash('Calificación finalizada.' if final else 'Revisión guardada.', 'success')
        return redirect(url_for('admin.detail', evaluation_id=evaluation_id))
    total = sum(q['score'] or 0 for q in questions)
    history = db.execute('SELECT * FROM events WHERE evaluation_id=? ORDER BY at DESC,id DESC', (evaluation_id,)).fetchall()
    candidate = db.execute('SELECT candidate_id FROM candidate_attempts WHERE evaluation_id=?', (evaluation_id,)).fetchone()
    return render_template('admin/detail.html', evaluation=evaluation, questions=questions, total=total,
                           classification=classification(total), history=history, current_time=now(), admin_mode=True, exam=exam, candidate=candidate)


def csv_safe(value):
    text = str(value) if value is not None else ''
    # Excel interprets leading whitespace and control characters before formulas too.
    if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
        return "'" + text
    return text


@bp.get('/exportar.csv')
@login_required
def export():
    from datetime import datetime, timezone
    def date(value):
        return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else ''
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    fields = ['ID', 'Código', 'Nombre', 'Estado', 'Creado UTC', 'Inicio UTC', 'Entrega UTC', 'Segundos utilizados', 'Puntaje sobre 100', 'Clasificación', 'Revisión manual']
    fields.extend(['Evaluación', 'exam_id', 'Versión'])
    evaluations = filter_results(result_rows())
    max_position = max((len(admin_questions(e['version_id'])) for e in evaluations), default=0)
    for q in range(1, max_position + 1):
        fields.extend([f'P{q} id', f'P{q} respuesta', f'P{q} puntaje', f'P{q} comentario'])
    writer.writerow(fields)
    for evaluation in evaluations:
        started = evaluation['started_at']
        elapsed = min((evaluation['submitted_at'] or now()) - started, evaluation['deadline'] - started) if started is not None else 0
        final = evaluation['status'] == 'graded'
        row = [evaluation['id'], evaluation['code'], evaluation['name'], STATUS_LABELS[evaluation['status']],
               date(evaluation['created_at']), date(started), date(evaluation['submitted_at']), elapsed,
               evaluation['total'] if final else '', classification(evaluation['total']) if final else '', f'{evaluation["reviewed"]}/{evaluation["manual_count"]}',
               evaluation['exam_title'], evaluation['exam_id'], evaluation['version']]
        questions = {q['id']:q for q in admin_questions(evaluation['version_id'], evaluation['id'])}
        for position in range(1, max_position + 1):
            q = questions.get(position)
            row.extend([q['source_id'], q['answer_text'] if q['answer'] else '', q['score'], q['comment']] if q else ['', '', '', ''])
        writer.writerow([csv_safe(v) for v in row])
    return Response('\ufeff' + output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename="sql-assessment-resultados.csv"'})


@bp.get('/guia')
@login_required
def guide():
    versions = get_db().execute('SELECT id,exam_id,title,version FROM exam_versions ORDER BY exam_id,version DESC').fetchall()
    version_id = request.args.get('version_id', str(active_versions()[0]['id']))
    if version_id not in {str(v['id']) for v in versions}:
        abort(404)
    return render_template('admin/guide.html', questions=admin_questions(int(version_id)), exam=version_info(int(version_id)), versions=versions, admin_mode=True)
