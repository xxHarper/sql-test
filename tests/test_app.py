"""Functional and security regression tests. No real candidate records are used."""
import csv
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash
from sql_assessment import create_app
from sql_assessment.db import classification, expire_attempts, get_db
from exam_fixture import DOCUMENT, QUESTIONS


class AssessmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = 'Contraseña de prueba 2026'
        cls.password_hash = generate_password_hash(cls.password)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = 1_800_000_000
        exams = Path(self.temp.name) / 'exams'
        exams.mkdir()
        (exams / 'v1.json').write_text(json.dumps(DOCUMENT), encoding='utf-8')
        self.config = dict(TESTING=True, DATABASE=str(Path(self.temp.name) / 'test.sqlite3'), EXAMS_DIR=str(exams), NOW=lambda: self.clock)
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.admin = self.app.test_client()
        with self.app.app_context():
            with get_db() as db:
                db.execute("INSERT INTO settings VALUES ('admin_password',?)", (self.password_hash,))
        self.admin.get('/admin/login')
        self.assertEqual(self.post(self.admin, '/admin/login', {'password': self.password}).status_code, 302)

    def tearDown(self):
        self.temp.cleanup()

    def post(self, client, path, data=None, payload=None):
        client.get('/')
        with client.session_transaction() as session:
            token = session['csrf_token']
        if payload is not None:
            return client.post(path, json=payload, headers={'X-CSRF-Token': token})
        return client.post(path, data={**(data or {}), 'csrf_token': token})

    def create_code(self):
        self.assertEqual(self.post(self.admin, '/admin/codigos', {'count': 1}).status_code, 302)
        with self.app.app_context():
            return dict(get_db().execute('SELECT * FROM evaluations ORDER BY id DESC LIMIT 1').fetchone())

    def claim(self, client=None, name='Ana Candidata'):
        client = client or self.client
        evaluation = self.create_code()
        response = self.post(client, '/acceso', {'name': name, 'code': evaluation['code']})
        self.assertEqual(response.status_code, 302)
        return evaluation

    def begin(self, name='Ana Candidata'):
        evaluation = self.claim(name=name)
        self.assertEqual(self.post(self.client, '/comenzar').status_code, 302)
        return evaluation

    def state(self):
        return self.client.get('/api/evaluacion').get_json()

    def submit(self, answers=None):
        return self.post(self.client, '/api/entregar', payload={'revision': self.state()['revision'], 'answers': answers or {}})

    def grade(self, evaluation_id, score=10, action='finalize'):
        values = {f'score_{q["id"]}': min(score, q['points']) for q in QUESTIONS if q['id'] >= 6}
        values['action'] = action
        return self.post(self.admin, f'/admin/evaluaciones/{evaluation_id}', values)

    def test_initial_questions_and_idempotent_initialization(self):
        create_app(self.config)
        with self.app.app_context():
            rows = get_db().execute('SELECT * FROM questions').fetchall()
            self.assertEqual(len(rows), 12)
            self.assertEqual(sum(q['points'] for q in rows), 100)
            self.assertEqual([sum(q['points'] for q in rows if q['section'] == s) for s in ['Conocimientos generales','SQL práctico','Estructuración y migración de datos']], [20,40,40])

    def test_public_pages_and_static_assets(self):
        for path in ['/', '/acceso', '/admin/login', '/static/app.css', '/static/exam.js', '/static/favicon.svg']:
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200, path)

    def test_admin_routes_require_authentication(self):
        for path in ['/admin', '/admin/guia', '/admin/exportar.csv', '/admin/evaluaciones/1']:
            self.assertEqual(self.client.get(path).status_code, 302)
        self.assertEqual(self.client.get('/api/evaluacion').status_code, 403)
        self.assertEqual(self.post(self.client, '/admin/codigos', {'count': 2}).status_code, 302)

    def test_csrf_and_headers(self):
        response = self.client.get('/')
        self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
        self.assertEqual(self.client.post('/comenzar').status_code, 400)
        self.assertEqual(self.client.post('/comenzar', headers={'X-CSRF-Token': 'á'}).status_code, 400)

    def test_local_network_boundary(self):
        for ip in ['127.0.0.1', '192.168.1.50', '10.0.2.1', '172.16.1.1', '::1', '::ffff:192.168.0.10']:
            self.assertEqual(self.client.get('/', environ_overrides={'REMOTE_ADDR': ip}).status_code, 200, ip)
        self.assertEqual(self.client.get('/', environ_overrides={'REMOTE_ADDR': '8.8.8.8'}).status_code, 403)
        self.assertEqual(self.client.get('/', environ_overrides={'REMOTE_ADDR': '8.8.8.8'}, headers={'X-Forwarded-For': '127.0.0.1'}).status_code, 403)

    def test_admin_brute_force_limit_and_expiration(self):
        for _ in range(5):
            self.assertEqual(self.post(self.client, '/admin/login', {'password': 'wrong'}).status_code, 401)
        self.assertEqual(self.post(self.client, '/admin/login', {'password': self.password}).status_code, 429)
        self.clock += 301
        self.assertEqual(self.post(self.client, '/admin/login', {'password': self.password}).status_code, 302)
        self.clock += 8 * 3600
        self.assertEqual(self.client.get('/admin').status_code, 302)

    def test_claim_does_not_start_timer_and_cannot_be_reused(self):
        evaluation = self.claim()
        with self.app.app_context():
            row = get_db().execute('SELECT * FROM evaluations WHERE id=?', (evaluation['id'],)).fetchone()
            self.assertIsNone(row['started_at'])
        other = self.app.test_client()
        response = self.post(other, '/acceso', {'name': 'Otra persona', 'code': evaluation['code']})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/instrucciones').status_code, 200)

    def test_concurrent_claims_only_consume_code_once(self):
        from concurrent.futures import ThreadPoolExecutor
        evaluation = self.create_code()
        barrier = threading.Barrier(2)

        def claim_code(number):
            client = self.app.test_client()
            client.get('/')
            barrier.wait(timeout=5)
            return self.post(client, '/acceso', {'name':f'Candidato {number}', 'code':evaluation['code']}).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim_code, [1,2]))
        self.assertEqual(sorted(results), [302,400])

    def test_large_unicode_answers_within_limit_save(self):
        self.begin()
        answers = {str(i): '😀' * 12000 for i in range(6,13)}
        response = self.post(self.client, '/api/respuestas', payload={'revision':0, 'answers':answers})
        self.assertEqual(response.status_code, 200)

    def test_start_is_idempotent_and_reload_keeps_deadline(self):
        self.begin()
        first = self.state()
        self.assertEqual(first['deadline'] - first['server_now'], 2700)
        self.clock += 120
        self.post(self.client, '/comenzar')
        self.assertEqual(first['deadline'], self.state()['deadline'])
        self.assertEqual(self.client.get('/evaluacion').status_code, 200)

    def test_autosave_revision_and_recovery(self):
        self.begin()
        payload = {'revision': 0, 'answers': {'1': 'b', '6': 'SELECT nombre, correo FROM Clientes;'}}
        result = self.post(self.client, '/api/respuestas', payload=payload)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['revision'], 1)
        self.assertEqual(self.state()['answers']['6'], payload['answers']['6'])
        self.assertEqual(self.client.get('/evaluacion').status_code, 200)
        self.assertEqual(self.post(self.client, '/api/respuestas', payload=payload).status_code, 409)

    def test_candidate_receives_no_grading_information(self):
        self.begin()
        state = self.state()
        self.assertEqual(set(state), {'status', 'deadline', 'server_now', 'revision', 'answers'})
        html = self.client.get('/evaluacion').get_data(as_text=True)
        for private in ['rubric', 'q.correct', 'SELECT nombre, correo FROM Clientes;', 'Condición correcta por id_cliente']:
            self.assertNotIn(private, html)
        self.submit({'1': 'b'})
        self.assertEqual(set(self.state()), set(state))
        thanks = self.client.get('/gracias').get_data(as_text=True)
        self.assertNotIn('Puntaje', thanks)
        self.assertNotIn('Muy buen candidato', thanks)

    def test_answer_validation(self):
        self.begin()
        invalid = [{'revision': 0, 'answers': {'99': 'x'}}, {'revision': 0, 'answers': {'1': '4'}},
                   {'revision': 0, 'answers': {'6': 'x' * 12001}}, {'revision': 0, 'answers': {'6': 3}},
                   {'revision': True, 'answers': {}}, {'revision': -1, 'answers': {}}, {'revision': 0, 'answers': []}]
        for payload in invalid:
            self.assertEqual(self.post(self.client, '/api/respuestas', payload=payload).status_code, 400)
        self.assertEqual(self.state()['revision'], 0)

    def test_voluntary_submit_scores_mc_and_locks_answers(self):
        evaluation = self.begin()
        choices = {str(q['id']): q['correct'] for q in QUESTIONS if q['kind']=='choice'}
        self.clock += 130
        self.assertEqual(self.submit(choices).status_code, 200)
        self.assertEqual(self.state()['status'], 'submitted')
        self.assertEqual(self.post(self.client, '/api/respuestas', payload={'revision': 1, 'answers': {'1':'a'}}).status_code, 409)
        self.assertEqual(self.submit().status_code, 409)
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute('SELECT SUM(score) FROM answers WHERE evaluation_id=?', (evaluation['id'],)).fetchone()[0], 20)
            row = db.execute('SELECT * FROM evaluations WHERE id=?', (evaluation['id'],)).fetchone()
            self.assertEqual(row['submitted_at']-row['started_at'], 130)

    def test_exact_deadline_rejects_late_save_and_keeps_saved_answers(self):
        evaluation = self.begin()
        self.post(self.client, '/api/respuestas', payload={'revision': 0, 'answers': {'6':'saved'}})
        self.clock = self.state()['deadline']
        result = self.post(self.client, '/api/respuestas', payload={'revision':1, 'answers': {'6':'late'}})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.state()['status'], 'expired')
        self.assertEqual(self.state()['answers']['6'], 'saved')
        with self.app.app_context():
            row = get_db().execute('SELECT * FROM evaluations WHERE id=?', (evaluation['id'],)).fetchone()
            self.assertEqual(row['submitted_at'], row['deadline'])

    def test_background_expiration_without_candidate_request(self):
        from run import deadline_worker
        evaluation = self.begin()
        self.clock += 2700
        stop = threading.Event()
        worker = threading.Thread(target=deadline_worker, args=(self.app, stop), daemon=True)
        worker.start()
        try:
            for _ in range(30):
                with self.app.app_context():
                    row = get_db().execute('SELECT status FROM evaluations WHERE id=?', (evaluation['id'],)).fetchone()
                if row['status'] == 'expired':
                    break
                time.sleep(.1)
            self.assertEqual(row['status'], 'expired')
        finally:
            stop.set()
            worker.join(2)

    def test_restart_preserves_sessions_answers_and_deadline(self):
        self.begin()
        self.post(self.client, '/api/respuestas', payload={'revision':0, 'answers':{'6':'SELECT * FROM Clientes;'}})
        old_state = self.state()
        cookie = self.client.get_cookie('session')
        new_app = create_app(self.config)
        new_client = new_app.test_client()
        new_client.set_cookie('session', cookie.value)
        self.assertEqual(new_client.get('/api/evaluacion').json, old_state)
        self.clock += 2701
        self.assertEqual(new_client.get('/api/evaluacion').json['status'], 'expired')

    def test_isolated_candidates(self):
        first = self.begin()
        self.post(self.client, '/api/respuestas', payload={'revision':0,'answers':{'6':'private response'}})
        other = self.app.test_client()
        self.claim(other)
        self.post(other, '/comenzar')
        self.assertEqual(other.get('/api/evaluacion').json['answers']['6'], '')
        self.assertEqual(other.get(f'/admin/evaluaciones/{first["id"]}').status_code, 302)

    def test_sql_is_never_executed_and_xss_is_escaped(self):
        evaluation = self.begin(name='<script>alert(1)</script>')
        text = "DROP TABLE evaluations; <script>alert('XSS')</script>"
        self.submit({'6': text})
        html = self.admin.get(f'/admin/evaluaciones/{evaluation["id"]}').get_data(as_text=True)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>alert', html)
        self.assertEqual(self.state()['answers']['6'], text)

    def test_grading_complete_partial_and_invalid_scores(self):
        evaluation = self.begin()
        self.assertEqual(self.grade(evaluation['id']).status_code, 409)
        self.submit({str(q['id']):q['correct'] for q in QUESTIONS if q['kind']=='choice'})
        # Missing manual grades prevent finalization; a partial save is allowed.
        self.post(self.admin, f'/admin/evaluaciones/{evaluation["id"]}', {'action':'finalize'})
        self.assertEqual(self.state()['status'], 'submitted')
        for score in ['NaN','inf','-1','999','x']:
            self.assertEqual(self.grade(evaluation['id'], action='save').status_code, 302)
            values = {f'score_{q["id"]}':'0' for q in QUESTIONS if q['id']>=6}
            values.update(score_6=score, action='finalize')
            self.assertEqual(self.post(self.admin, f'/admin/evaluaciones/{evaluation["id"]}', values).status_code, 400)
        self.assertEqual(self.grade(evaluation['id'], score=15).status_code, 302)
        self.assertEqual(self.state()['status'], 'graded')
        html = self.admin.get(f'/admin/evaluaciones/{evaluation["id"]}').get_data(as_text=True)
        self.assertIn('Muy buen candidato', html)
        self.assertIn('100', html)

    def test_dashboard_detail_and_guide_render(self):
        evaluation = self.begin()
        for path in ['/admin','/admin?estado=in_progress&q=Ana','/admin/guia',f'/admin/evaluaciones/{evaluation["id"]}']:
            self.assertEqual(self.admin.get(path).status_code, 200, path)
        self.assertEqual(self.admin.get('/admin/evaluaciones/9999').status_code, 404)

    def test_csv_is_excel_compatible_and_prevents_formulas(self):
        evaluation = self.begin(name='=HYPERLINK("bad")')
        self.submit({'6':'  =1+1', '7':'SELECT * FROM Pedidos;\n-- prueba'})
        self.grade(evaluation['id'], score=0)
        response = self.admin.get('/admin/exportar.csv')
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertTrue(text.startswith('\ufeff'))
        rows = list(csv.reader(io.StringIO(text.lstrip('\ufeff'))))
        row = dict(zip(rows[0], rows[1]))
        self.assertTrue(row['Nombre'].startswith("'="))
        self.assertTrue(row['P6 respuesta'].startswith("'  ="))
        self.assertEqual(row['P7 respuesta'], 'SELECT * FROM Pedidos;\n-- prueba')
        self.assertEqual(row['Clasificación'], 'Requiere reforzar conocimientos')

    def test_classification_boundaries(self):
        expected = [(0,'Requiere reforzar conocimientos'),(54.5,'Requiere reforzar conocimientos'),
                    (55,'Con potencial'),(69.5,'Con potencial'),(70,'Buen candidato'),(84.5,'Buen candidato'),
                    (85,'Muy buen candidato'),(100,'Muy buen candidato')]
        for score, label in expected:
            self.assertEqual(classification(score), label)

    def test_invalid_names_codes_and_code_counts(self):
        for name,code in [('a','SQL-ABCDEFGH'),('a'*121,'SQL-ABCDEFGH'),('Ana','bad')]:
            self.assertEqual(self.post(self.client, '/acceso', {'name':name,'code':code}).status_code, 400)
        for count in ['0','26','abc']:
            self.assertEqual(self.post(self.admin, '/admin/codigos', {'count':count}).status_code, 400)

    def test_password_setup_and_reset_are_local_and_hashed(self):
        from configurar import configure
        with patch('configurar.getpass.getpass', side_effect=['Nueva clave con ñ 2026','Nueva clave con ñ 2026']):
            configure(self.app, reset=True)
        with self.app.app_context():
            from werkzeug.security import check_password_hash
            value = get_db().execute("SELECT value FROM settings WHERE key='admin_password'").fetchone()['value']
            self.assertNotEqual(value, 'Nueva clave con ñ 2026')
            self.assertTrue(check_password_hash(value, 'Nueva clave con ñ 2026'))
        # Existing cookies were signed with the old key. A restart revokes them.
        restarted = create_app(self.config)
        client = restarted.test_client()
        client.set_cookie('session', self.admin.get_cookie('session').value)
        self.assertEqual(client.get('/admin').status_code, 302)


if __name__ == '__main__':
    unittest.main()
