"""Official v2 release: active bank, real submission/grading and v1 preservation."""
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from markupsafe import escape
from werkzeug.security import generate_password_hash

import test_app
from exam_fixture import DOCUMENT as V1
from sql_assessment import create_app
from sql_assessment.db import get_db
from sql_assessment.exams import active_versions, admin_questions, load_documents, public_questions, version_info

ROOT = Path(__file__).resolve().parents[1]
V2 = json.loads((ROOT/'evaluaciones/reactivos_sql_junior_v2.json').read_text(encoding='utf-8'))


class V2Tests(unittest.TestCase):
    post = test_app.AssessmentTests.post
    create_code = test_app.AssessmentTests.create_code
    claim = test_app.AssessmentTests.claim
    begin = test_app.AssessmentTests.begin
    state = test_app.AssessmentTests.state
    submit = test_app.AssessmentTests.submit

    @classmethod
    def setUpClass(cls):
        cls.password = 'Pruebas v2 contraseña local'
        cls.password_hash = generate_password_hash(cls.password)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = dict(TESTING=True, DATABASE=str(Path(self.temp.name)/'v2.sqlite3'), NOW=lambda:1800000000)
        self.app = create_app(self.config)
        self.client, self.admin = self.app.test_client(), self.app.test_client()
        with self.app.app_context():
            with get_db() as db:
                db.execute("INSERT INTO settings VALUES ('admin_password',?)",(self.password_hash,))
        self.post(self.admin,'/admin/login',{'password':self.password})

    def tearDown(self):
        self.temp.cleanup()

    def test_default_active_bank_is_v2_with_exact_source(self):
        documents = load_documents(ROOT/'evaluaciones')
        self.assertEqual([doc['exam_id'] for _,doc in documents],['sql-migracion-junior-v2'])
        self.assertEqual(len(V2['questions']),13)
        self.assertEqual(len({q['id'] for q in V2['questions']}),13)
        self.assertEqual(sum(q['points'] for q in V2['questions']),100)
        with self.app.app_context():
            active = active_versions()
            self.assertEqual(len(active),1)
            self.assertEqual(json.loads(active[0]['source_json']),V2)
            meta = version_info(active[0]['id'])
            self.assertEqual((meta['question_count'],meta['manual_count'],meta['duration_minutes']),(13,8,50))
            stored = get_db().execute('SELECT * FROM questions WHERE version_id=? ORDER BY position',(active[0]['id'],)).fetchall()
            self.assertEqual([json.loads(q['source_json']) for q in stored],V2['questions'])
        self.assertEqual([s['points'] for s in V2['sections']],[20,40,30,10])
        self.assertEqual([q['points'] for q in V2['questions'][9:]],[10,10,10,10])

    def test_timer_and_q13_use_existing_sql_editor(self):
        self.begin()
        state = self.state()
        self.assertEqual(state['deadline']-state['server_now'],3000)
        html = self.client.get('/evaluacion').get_data(as_text=True)
        self.assertEqual(html.count('class="question-card card"'),13)
        self.assertEqual(html.count('class="answer-editor sql-editor"'),5)
        self.assertIn('data-question="13" data-source-id="Q13" data-kind="sql"',html)
        self.assertIn(str(escape(V2['questions'][12]['prompt'])),html)
        self.assertIn('id="answer-13"',html)
        self.assertIn('50:00',html)
        self.assertIn('de 13 preguntas',html)

    def test_complete_exam_delivery_and_manual_grade_including_q13(self):
        evaluation = self.begin()
        answers = {str(i):q['grading']['correct_option'] if q['type']=='multiple_choice' else q['grading']['reference_answer'] for i,q in enumerate(V2['questions'],1)}
        self.assertEqual(self.submit(answers).status_code,200)
        self.assertEqual(self.state()['status'],'submitted')
        with self.app.app_context():
            questions = admin_questions(evaluation['version_id'],evaluation['id'])
            self.assertEqual([q['score'] for q in questions],[4]*5+[None]*8)
            self.assertEqual(questions[12]['answer'],V2['questions'][12]['grading']['reference_answer'])
            self.assertEqual(questions[12]['grading'],V2['questions'][12]['grading'])
            self.assertIsNone(get_db().execute("SELECT name FROM sqlite_master WHERE type='table' AND name='Direcciones'").fetchone())
        url = f'/admin/evaluaciones/{evaluation["id"]}'
        grades = {f'score_{i}':q['points'] for i,q in enumerate(V2['questions'],1) if q['type']!='multiple_choice'}
        grades.update(action='finalize',comment_13='Claves y tipos revisados manualmente.')
        self.assertEqual(self.post(self.admin,url,{**grades,'score_13':11}).status_code,400)
        self.assertEqual(self.state()['status'],'submitted')
        missing = dict(grades)
        del missing['score_13']
        self.post(self.admin,url,missing)
        self.assertEqual(self.state()['status'],'submitted')
        self.assertEqual(self.post(self.admin,url,grades).status_code,302)
        self.assertEqual(self.state()['status'],'graded')
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT SUM(score) FROM answers WHERE evaluation_id=?',(evaluation['id'],)).fetchone()[0],100)
            self.assertEqual(admin_questions(evaluation['version_id'],evaluation['id'])[12]['score'],10)
        csv_text = self.admin.get('/admin/exportar.csv').get_data(as_text=True).lstrip('\ufeff')
        result = next(csv.DictReader(io.StringIO(csv_text)))
        self.assertEqual(result['exam_id'],V2['exam_id'])
        self.assertEqual(result['P13 id'],'Q13')
        self.assertEqual(result['P13 respuesta'],answers['13'])
        self.assertEqual(float(result['P13 puntaje']),10)
        self.assertEqual(float(result['Puntaje sobre 100']),100)

    def test_rubrics_and_reference_are_only_available_to_admin(self):
        evaluation = self.begin()
        html = self.client.get('/evaluacion').get_data(as_text=True)
        api = self.client.get('/api/evaluacion').get_data(as_text=True)
        with self.app.app_context():
            public = json.dumps(public_questions(evaluation['version_id']))
        for private in ['grading','reference_answer','correct_option','rubric',V2['internal_notes'],
                        V2['questions'][12]['grading']['reference_answer'],V2['questions'][12]['grading']['notes']]:
            self.assertNotIn(private,html+api+public)
        for item in V2['questions'][12]['grading']['rubric']:
            self.assertNotIn(item['criterion'],html+api+public)
        for path in ['/admin/guia','/admin/exportar.csv',f'/admin/evaluaciones/{evaluation["id"]}']:
            self.assertEqual(self.client.get(path).status_code,302)
        for path in ['/evaluaciones/reactivos_sql_junior_v2.json','/evaluaciones/historico/reactivos_sql_junior.json']:
            self.assertEqual(self.client.get(path).status_code,404)
        admin_html = self.admin.get(f'/admin/evaluaciones/{evaluation["id"]}').get_data(as_text=True)
        self.assertEqual(admin_html.count('class="card grading-question"'),13)
        self.assertIn(str(escape(V2['questions'][12]['grading']['reference_answer'])),admin_html)
        for item in V2['questions'][12]['grading']['rubric']:
            self.assertIn(str(escape(item['criterion'])),admin_html)

    def test_q13_sql_is_stored_as_text_never_executed(self):
        evaluation = self.begin()
        malicious = "CREATE TABLE candidate_execution_probe(id INTEGER); DROP TABLE evaluations; <script>alert(1)</script>"
        self.assertEqual(self.submit({'13':malicious}).status_code,200)
        with self.app.app_context():
            self.assertIsNone(get_db().execute("SELECT name FROM sqlite_master WHERE name='candidate_execution_probe'").fetchone())
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM evaluations').fetchone()[0],1)
            self.assertEqual(admin_questions(evaluation['version_id'],evaluation['id'])[12]['answer'],malicious)
        admin_html = self.admin.get(f'/admin/evaluaciones/{evaluation["id"]}').get_data(as_text=True)
        self.assertIn('&lt;script&gt;',admin_html)
        self.assertNotIn('<script>alert',admin_html)

    def test_new_codes_cannot_select_retired_v1(self):
        dashboard = self.admin.get('/admin').get_data(as_text=True)
        self.assertIn('sql-migracion-junior-v2',dashboard)
        self.assertNotIn('sql-migracion-junior-v1',dashboard)
        self.assertEqual(self.post(self.admin,'/admin/codigos',{'count':1,'version_id':999999}).status_code,400)
        evaluation = self.create_code()
        with self.app.app_context():
            self.assertEqual(version_info(evaluation['version_id'])['exam_id'],V2['exam_id'])

    def test_restart_keeps_v2_answers_and_does_not_duplicate_questions(self):
        evaluation = self.begin()
        self.submit({'13':'CREATE TABLE respuesta_sin_ejecutar (id INTEGER);'})
        with self.app.app_context():
            before = '\n'.join(get_db().iterdump())
        create_app(self.config)
        with self.app.app_context():
            self.assertEqual('\n'.join(get_db().iterdump()),before)
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM questions').fetchone()[0],13)

    def test_upgrade_keeps_v1_pending_active_and_graded_attempts(self):
        # Build the previous installation with real v1 data and the same DB file.
        old_dir = Path(self.temp.name)/'old-exams'
        old_dir.mkdir()
        (old_dir/'v1.json').write_text(json.dumps(V1),encoding='utf-8')
        old_config = {**self.config,'DATABASE':str(Path(self.temp.name)/'upgrade.sqlite3'),'EXAMS_DIR':str(old_dir)}
        self.app = create_app(old_config)
        self.client,self.admin = self.app.test_client(),self.app.test_client()
        with self.app.app_context():
            with get_db() as db:
                db.execute("INSERT INTO settings VALUES ('admin_password',?)",(self.password_hash,))
        self.post(self.admin,'/admin/login',{'password':self.password})
        pending = self.create_code()
        complete = self.begin()
        self.submit({str(i):q['grading'].get('correct_option','Respuesta v1') for i,q in enumerate(V1['questions'],1)})
        values = {f'score_{i}':q['points'] for i,q in enumerate(V1['questions'],1) if q['type']!='multiple_choice'}
        self.post(self.admin,f'/admin/evaluaciones/{complete["id"]}',{**values,'action':'finalize'})
        active = self.begin()
        self.post(self.client,'/api/respuestas',payload={'revision':0,'answers':{'6':'SQL de v1 guardado'}})
        state = self.state()
        cookie,admin_cookie = self.client.get_cookie('session'),self.admin.get_cookie('session')
        with self.app.app_context():
            old_evaluations = [tuple(r) for r in get_db().execute('SELECT * FROM evaluations ORDER BY id')]
            old_answers = [tuple(r) for r in get_db().execute('SELECT * FROM answers ORDER BY evaluation_id,question_id')]
            old_questions = [tuple(r) for r in get_db().execute('SELECT * FROM questions ORDER BY id')]
        # Use the actual production bank: only v2 is enabled for new codes.
        self.app = create_app({**self.config,'DATABASE':old_config['DATABASE']})
        self.client,self.admin = self.app.test_client(),self.app.test_client()
        self.client.set_cookie('session',cookie.value)
        self.admin.set_cookie('session',admin_cookie.value)
        with self.app.app_context():
            db = get_db()
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM evaluations ORDER BY id')],old_evaluations)
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM answers ORDER BY evaluation_id,question_id')],old_answers)
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM questions WHERE version_id=? ORDER BY id',(active['version_id'],))],old_questions)
            self.assertEqual([r['exam_id'] for r in active_versions()],[V2['exam_id']])
            self.assertEqual(db.execute('SELECT SUM(score) FROM answers WHERE evaluation_id=?',(complete['id'],)).fetchone()[0],100)
        self.assertEqual(self.state(),state)
        self.assertEqual(len(self.state()['answers']),12)
        other = self.app.test_client()
        self.post(other,'/acceso',{'name':'Código antiguo','code':pending['code']})
        self.post(other,'/comenzar')
        old_state = other.get('/api/evaluacion').json
        self.assertEqual(old_state['deadline']-old_state['server_now'],2700)
        self.assertEqual(len(old_state['answers']),12)
        historical = self.admin.get(f'/admin/evaluaciones/{complete["id"]}').get_data(as_text=True)
        self.assertIn('sql-migracion-junior-v1',historical)
        self.assertNotIn('Q13',historical)
        self.assertEqual(self.submit().status_code,200)
        new = self.begin()
        self.assertNotEqual(new['version_id'],active['version_id'])
        self.assertEqual(len(self.state()['answers']),13)
        self.assertEqual(self.state()['deadline']-self.state()['server_now'],3000)


if __name__ == '__main__':
    unittest.main()
