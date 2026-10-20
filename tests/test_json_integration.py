"""Official JSON, immutable versions, atomic validation and old-database migration."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
from werkzeug.security import generate_password_hash

import test_app
from exam_fixture import DOCUMENT
from fixtures.legacy_questions import QUESTIONS as LEGACY_QUESTIONS
from sql_assessment import create_app
from sql_assessment.db import get_db
from sql_assessment.exams import ExamValidationError, admin_questions, public_questions, validate_document


class JsonIntegrationTests(unittest.TestCase):
    post = test_app.AssessmentTests.post
    create_code = test_app.AssessmentTests.create_code
    claim = test_app.AssessmentTests.claim
    begin = test_app.AssessmentTests.begin
    state = test_app.AssessmentTests.state
    submit = test_app.AssessmentTests.submit
    grade = test_app.AssessmentTests.grade

    @classmethod
    def setUpClass(cls):
        cls.password = 'Solo pruebas JSON 2026'
        cls.password_hash = generate_password_hash(cls.password)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.exam_dir = self.directory / 'evaluaciones'
        self.exam_dir.mkdir()
        self.source = self.exam_dir / 'oficial.json'
        self.write_document(DOCUMENT)
        self.clock = 1800000000
        self.config = dict(TESTING=True, DATABASE=str(self.directory / 'test.sqlite3'), EXAMS_DIR=str(self.exam_dir), NOW=lambda:self.clock)
        self.app = create_app(self.config)
        self.client, self.admin = self.app.test_client(), self.app.test_client()
        with self.app.app_context():
            with get_db() as db:
                db.execute("INSERT INTO settings VALUES ('admin_password',?)", (self.password_hash,))
        self.post(self.admin, '/admin/login', {'password':self.password})

    def tearDown(self):
        self.temp.cleanup()

    def write_document(self, document, path=None):
        (path or self.source).write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')

    def snapshot(self):
        with self.app.app_context():
            return '\n'.join(get_db().iterdump())

    def restart(self):
        cookie, admin_cookie = self.client.get_cookie('session'), self.admin.get_cookie('session')
        self.app = create_app(self.config)
        self.client, self.admin = self.app.test_client(), self.app.test_client()
        if cookie:
            self.client.set_cookie('session', cookie.value)
        if admin_cookie:
            self.admin.set_cookie('session', admin_cookie.value)

    def test_exact_official_document_and_tables_in_sqlite(self):
        with self.app.app_context():
            db = get_db()
            row = db.execute('SELECT * FROM exam_versions').fetchone()
            self.assertEqual(json.loads(row['source_json']), DOCUMENT)
            questions = db.execute('SELECT * FROM questions ORDER BY position').fetchall()
            self.assertEqual(len(questions), 12)
            self.assertEqual(sum(q['points'] for q in questions), 100)
            for stored, source in zip(questions, DOCUMENT['questions']):
                self.assertEqual(stored['source_id'], source['id'])
                self.assertEqual(stored['prompt'], source['prompt'])
                self.assertEqual(json.loads(stored['source_json']), source)
                self.assertEqual(json.loads(stored['grading_json']), source['grading'])
                self.assertEqual(json.loads(stored['tables_json']), source.get('tables', []))
                self.assertEqual(json.loads(stored['options']), source.get('options', []))
            self.assertEqual(json.loads(questions[10]['tables_json'])[0]['rows'][0][0], '0012')

    def test_candidate_renders_all_12_prompts_options_and_tables(self):
        self.begin()
        html = self.client.get('/evaluacion').get_data(as_text=True)
        from markupsafe import escape
        self.assertEqual(html.count('class="question-card card"'), 12)
        for q in DOCUMENT['questions']:
            self.assertIn(str(escape(q['prompt'])), html)
            self.assertIn(f'data-source-id="{q["id"]}"', html)
            for option in q.get('options', []):
                self.assertIn(str(escape(option['text'])), html)
        instructions = self.app.test_client()
        self.claim(instructions)
        text = instructions.get('/instrucciones').get_data(as_text=True)
        for instruction in DOCUMENT['instructions']:
            self.assertIn(instruction, text)

    def test_five_auto_scores_and_seven_manual_scores(self):
        evaluation = self.begin()
        self.submit({str(i):q['grading']['correct_option'] for i,q in enumerate(DOCUMENT['questions'],1) if q['type']=='multiple_choice'})
        with self.app.app_context():
            scores = get_db().execute('SELECT score FROM answers WHERE evaluation_id=? ORDER BY question_id', (evaluation['id'],)).fetchall()
            self.assertEqual([r['score'] for r in scores], [4]*5 + [None]*7)
        values = {f'score_{i}':q['points'] for i,q in enumerate(DOCUMENT['questions'],1) if q['type']!='multiple_choice'}
        values['action'] = 'finalize'
        self.assertEqual(self.post(self.admin, f'/admin/evaluaciones/{evaluation["id"]}', values).status_code, 302)
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT SUM(score) FROM answers WHERE evaluation_id=?', (evaluation['id'],)).fetchone()[0], 100)
        self.assertEqual(self.state()['status'], 'graded')

    def test_private_data_never_sent_and_json_files_not_served(self):
        document = copy.deepcopy(DOCUMENT)
        document['internal_notes'] = 'PRIVATE-INTERNAL-987'
        document['questions'][5]['grading']['reference_answer'] = 'PRIVATE-REFERENCE-987'
        document['questions'][5]['grading']['rubric'][0]['criterion'] = 'PRIVATE-RUBRIC-987'
        document['questions'][5]['grading']['notes'] = 'PRIVATE-NOTES-987'
        self.write_document(document)
        self.restart()
        evaluation = self.begin()
        with self.app.app_context():
            version_id = get_db().execute('SELECT version_id FROM evaluations WHERE id=?', (evaluation['id'],)).fetchone()[0]
            projection = json.dumps(public_questions(version_id))
        html = self.client.get('/evaluacion').get_data(as_text=True)
        responses = html + projection + self.client.get('/api/evaluacion').get_data(as_text=True)
        for value in ['PRIVATE-INTERNAL-987','PRIVATE-REFERENCE-987','PRIVATE-RUBRIC-987','PRIVATE-NOTES-987','correct_option','reference_answer','grading','rubric']:
            self.assertNotIn(value, responses)
        for path in ['/evaluaciones/reactivos_sql_junior.json','/evaluaciones/oficial.json','/static/../evaluaciones/oficial.json','/api/exams','/api/questions']:
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get('/admin/guia').status_code, 302)
        guide = self.admin.get('/admin/guia').get_data(as_text=True)
        self.assertIn('PRIVATE-RUBRIC-987', guide)
        self.assertIn('PRIVATE-REFERENCE-987', guide)

    def test_unchanged_restart_no_writes_and_no_duplicate_questions(self):
        self.begin()
        self.submit({'1':'b','6':'SELECT nombre, correo FROM Clientes;'})
        before = self.snapshot()
        self.restart()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.app.extensions['exam_imports'][0]['created'])
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM questions').fetchone()[0], 12)

    def test_json_format_and_object_key_order_do_not_create_versions(self):
        self.source.write_text(json.dumps(DOCUMENT, sort_keys=True), encoding='utf-8')
        self.restart()
        self.assertEqual(self.app.extensions['exam_imports'][0]['version'], 1)
        self.assertFalse(self.app.extensions['exam_imports'][0]['created'])

    def test_changed_prompt_grading_points_and_tables_preserve_finished_attempt(self):
        evaluation = self.begin()
        self.submit({'1':'b','6':'Original SQL','11':'Respuesta original'})
        self.grade(evaluation['id'], score=15)
        with self.app.app_context():
            old_evaluation = dict(get_db().execute('SELECT * FROM evaluations WHERE id=?', (evaluation['id'],)).fetchone())
            old_answers = [dict(r) for r in get_db().execute('SELECT * FROM answers WHERE evaluation_id=?', (evaluation['id'],))]
            old_questions = admin_questions(old_evaluation['version_id'], evaluation['id'])
        changed = copy.deepcopy(DOCUMENT)
        changed['questions'][0]['prompt'] = 'Pregunta revisada de otra versión'
        changed['questions'][0]['grading']['correct_option'] = 'a'
        changed['questions'][5]['points'] = 9
        changed['questions'][5]['grading']['rubric'][0]['points'] = 5
        changed['questions'][6]['points'] = 11
        changed['questions'][6]['grading']['rubric'][0]['points'] = 4
        changed['questions'][5]['tables'][0]['rows'][0][1] = 'Cliente de versión nueva'
        self.write_document(changed)
        self.restart()
        self.assertEqual(self.app.extensions['exam_imports'][0]['version'], 2)
        with self.app.app_context():
            self.assertEqual(dict(get_db().execute('SELECT * FROM evaluations WHERE id=?', (evaluation['id'],)).fetchone()), old_evaluation)
            self.assertEqual([dict(r) for r in get_db().execute('SELECT * FROM answers WHERE evaluation_id=?', (evaluation['id'],))], old_answers)
            self.assertEqual(admin_questions(old_evaluation['version_id'], evaluation['id']), old_questions)
        old_html = self.admin.get(f'/admin/evaluaciones/{evaluation["id"]}').get_data(as_text=True)
        self.assertIn(DOCUMENT['questions'][0]['prompt'], old_html)
        self.assertNotIn(changed['questions'][0]['prompt'], old_html)
        new_evaluation = self.begin(name='Candidato nuevo')
        self.assertNotEqual(new_evaluation['version_id'], old_evaluation['version_id'])
        self.assertIn(changed['questions'][0]['prompt'], self.client.get('/evaluacion').get_data(as_text=True))
        self.assertEqual(self.submit({'1':'a'}).status_code, 200)
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT SUM(score) FROM answers WHERE evaluation_id=?', (new_evaluation['id'],)).fetchone()[0],4)

    def test_pending_codes_and_active_attempts_remain_pinned(self):
        pending = self.create_code()
        active = self.begin()
        self.post(self.client, '/api/respuestas', payload={'revision':0,'answers':{'1':'b','6':'original'}})
        original_state = self.state()
        document = copy.deepcopy(DOCUMENT)
        document['duration_minutes'] = 30
        document['questions'][0]['grading']['correct_option'] = 'a'
        self.write_document(document)
        self.restart()
        self.assertEqual(self.state(), original_state)
        self.submit()
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT SUM(score) FROM answers WHERE evaluation_id=?', (active['id'],)).fetchone()[0],4)
        other = self.app.test_client()
        self.post(other, '/acceso', {'name':'Código anterior','code':pending['code']})
        self.post(other, '/comenzar')
        old_state = other.get('/api/evaluacion').json
        self.assertEqual(old_state['deadline'] - old_state['server_now'], 2700)
        self.begin()
        self.assertEqual(self.state()['deadline'] - self.state()['server_now'], 1800)

    def test_invalid_documents_report_errors_without_database_changes(self):
        before = self.snapshot()
        for mutation, expected in [
            (lambda d:d['questions'][1].update(id='Q01'), 'duplicado'),
            (lambda d:d['questions'][0].update(points=3), 'debe ser 100'),
            (lambda d:d.update(max_score=99), 'max_score'),
            (lambda d:d['questions'][0]['grading'].update(correct_option='z'), 'correct_option'),
            (lambda d:d['questions'][5]['grading'].update(mode='auto'), 'mode'),
            (lambda d:d['questions'][5]['grading']['rubric'][0].update(points=5), 'rubric'),
            (lambda d:d['questions'][5]['tables'][0]['rows'].append(['wrong']), 'celdas'),
            (lambda d:d['questions'][0]['options'][0].update(id='b'), 'opción duplicado'),
            (lambda d:d['questions'][0].update(section='missing'), 'section'),
            (lambda d:d.update(schema_version=2), 'schema_version'),
            (lambda d:d.update(duration_minutes=True), 'duration_minutes'),
            (lambda d:d['questions'][0].update(grading=None), 'objeto')]:
            document = copy.deepcopy(DOCUMENT)
            mutation(document)
            self.write_document(document)
            with self.assertRaises(ExamValidationError) as caught:
                create_app(self.config)
            self.assertIn(expected, str(caught.exception))
            self.assertEqual(self.snapshot(), before)

    def test_invalid_batch_is_atomic_even_with_valid_new_exam(self):
        before = self.snapshot()
        added = copy.deepcopy(DOCUMENT)
        added['exam_id'] = 'otro-examen'
        self.write_document(added, self.exam_dir/'a-nuevo.json')
        invalid = copy.deepcopy(DOCUMENT)
        invalid['questions'][0]['id'] = invalid['questions'][1]['id']
        self.write_document(invalid)
        with self.assertRaises(ExamValidationError):
            create_app(self.config)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_fresh_install_does_not_create_database(self):
        self.source.write_text('{malformed JSON', encoding='utf-8')
        database = self.directory/'new.sqlite3'
        with self.assertRaises(ExamValidationError):
            create_app({**self.config, 'DATABASE':str(database)})
        self.assertFalse(database.exists())

    def test_duplicate_properties_bad_roots_and_nonfinite_values(self):
        before = self.snapshot()
        for text in ['{"schema_version":1,"schema_version":1}', '[]', 'null', '{"max_score":NaN}', '{"max_score":Infinity}']:
            self.source.write_text(text, encoding='utf-8')
            with self.assertRaises(ExamValidationError):
                create_app(self.config)
            self.assertEqual(self.snapshot(), before)

    def test_database_import_transaction_rolls_back_failures(self):
        before = self.snapshot()
        def broken_import(db, documents, timestamp):
            db.execute("UPDATE exam_catalog SET enabled=0")
            raise RuntimeError('simulated disk/import failure')
        with patch('sql_assessment.exams.import_documents', side_effect=broken_import):
            with self.assertRaises(RuntimeError):
                create_app(self.config)
        self.assertEqual(self.snapshot(), before)

    def test_versions_and_attempt_binding_cannot_be_overwritten(self):
        evaluation = self.begin()
        with self.app.app_context():
            db = get_db()
            for sql in ["UPDATE questions SET prompt='overwrite'", 'DELETE FROM questions', "UPDATE exam_versions SET title='overwrite'", 'DELETE FROM exam_versions', 'UPDATE evaluations SET version_id=NULL']:
                with self.assertRaises(sqlite3.IntegrityError):
                    with db:
                        db.execute(sql)

    def test_new_exam_and_extra_question_require_only_json(self):
        added = copy.deepcopy(DOCUMENT)
        added['exam_id'] = 'sql-segunda-evaluacion'
        added['title'] = 'Otro examen desde JSON'
        added['questions'][0]['points'] = 2
        added['questions'].append(dict(id='EXTRA', section='fundamentos',type='multiple_choice',points=2,prompt='Pregunta adicional',options=[{'id':'si','text':'Sí'},{'id':'no','text':'No'}],grading={'correct_option':'si'}))
        self.write_document(added, self.exam_dir/'segundo.json')
        self.restart()
        with self.app.app_context():
            new_version = get_db().execute('SELECT id FROM exam_versions WHERE exam_id=?',(added['exam_id'],)).fetchone()[0]
        response = self.post(self.admin, '/admin/codigos', {'count':1,'version_id':new_version})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            code = get_db().execute('SELECT code FROM evaluations ORDER BY id DESC LIMIT 1').fetchone()[0]
        self.post(self.client, '/acceso', {'name':'Otra evaluación','code':code})
        self.post(self.client, '/comenzar')
        html = self.client.get('/evaluacion').get_data(as_text=True)
        self.assertEqual(html.count('class="question-card card"'), 13)
        self.assertIn('de 13 preguntas', html)
        self.assertEqual(self.submit({'13':'si'}).status_code, 200)
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT SUM(score) FROM answers').fetchone()[0],2)
        self.assertIn('P13 respuesta',self.admin.get('/admin/exportar.csv').get_data(as_text=True))

    def test_json_files_never_receive_candidate_data(self):
        original = self.source.read_bytes()
        self.begin()
        self.submit({'6':'CANDIDATE-ANSWER-SQLITE-ONLY'})
        self.grade(1,score=0)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertNotIn(b'CANDIDATE-ANSWER-SQLITE-ONLY', original)

    def test_duplicate_exam_ids_in_files_rejected(self):
        self.write_document(DOCUMENT,self.exam_dir/'duplicado.json')
        with self.assertRaises(ExamValidationError) as caught:
            create_app(self.config)
        self.assertIn('exam_id duplicado', str(caught.exception))

    def test_manual_grading_depends_on_type_not_question_number(self):
        document = copy.deepcopy(DOCUMENT)
        document['sections'] = [{'id':'general','title':'Sección configurable','points':100}]
        document['questions'] = [
            dict(id='MANUAL', section='general',type='open_text',points=60,prompt='Respuesta abierta primero',grading={
                'mode':'manual','reference_answer':'Referencia privada','rubric':[{'criterion':'Criterio privado','points':60}]}),
            dict(id='CHOICE', section='general',type='multiple_choice',points=40,prompt='Opción múltiple al final',options=[{'id':'si','text':'Sí'},{'id':'no','text':'No'}],grading={'correct_option':'si'})]
        self.write_document(document)
        self.restart()
        evaluation = self.begin()
        self.assertEqual(self.submit({'1':'Respuesta manual','2':'si'}).status_code,200)
        url = f'/admin/evaluaciones/{evaluation["id"]}'
        self.post(self.admin,url,{'action':'finalize'})
        self.assertEqual(self.state()['status'],'submitted')
        self.post(self.admin,url,{'score_1':60,'action':'finalize'})
        self.assertEqual(self.state()['status'],'graded')
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT SUM(score) FROM answers WHERE evaluation_id=?',(evaluation['id'],)).fetchone()[0],100)

    def test_reverting_source_reuses_snapshot_without_deleting_versions(self):
        changed = copy.deepcopy(DOCUMENT)
        changed['questions'][0]['prompt'] = 'Otra redacción'
        self.write_document(changed)
        self.restart()
        self.write_document(DOCUMENT)
        self.restart()
        self.assertEqual(self.app.extensions['exam_imports'][0]['version'], 1)
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM exam_versions').fetchone()[0],2)

    def test_migrate_real_pre_json_schema_preserves_attempts_answers_and_scores(self):
        database = self.directory/'legacy.sqlite3'
        with closing(sqlite3.connect(database)) as db:
            db.executescript('''
                CREATE TABLE questions(id INTEGER PRIMARY KEY,section TEXT NOT NULL,kind TEXT NOT NULL,title TEXT NOT NULL,prompt TEXT NOT NULL,points INTEGER NOT NULL,options TEXT,correct TEXT,guide TEXT NOT NULL,rubric TEXT NOT NULL);
                CREATE TABLE evaluations(id INTEGER PRIMARY KEY,code TEXT UNIQUE NOT NULL,name TEXT,status TEXT NOT NULL,created_at INTEGER NOT NULL,started_at INTEGER,deadline INTEGER,submitted_at INTEGER,graded_at INTEGER,candidate_token TEXT UNIQUE,revision INTEGER NOT NULL DEFAULT 0,delivery_reason TEXT);
                CREATE TABLE answers(evaluation_id INTEGER NOT NULL REFERENCES evaluations(id),question_id INTEGER NOT NULL REFERENCES questions(id),answer TEXT NOT NULL DEFAULT '',score REAL,comment TEXT NOT NULL DEFAULT '',updated_at INTEGER NOT NULL,PRIMARY KEY(evaluation_id,question_id));
                CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            ''')
            for q in LEGACY_QUESTIONS:
                db.execute('INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?,?)',(q['id'],q['section'],q['kind'],q['title'],q['prompt'],q['points'],json.dumps(q.get('options')),q.get('correct'),q['guide'],q['rubric']))
            for i,status in [(1,'graded'),(2,'in_progress'),(3,'not_started')]:
                db.execute('INSERT INTO evaluations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(i,f'SQL-LEGACY0{i}','Candidato anterior',status,self.clock,self.clock if i!=3 else None,self.clock+2700 if i!=3 else None,self.clock+10 if i==1 else None,self.clock+20 if i==1 else None,f'legacy-token-{i}',3,'voluntary' if i==1 else None))
            for i in [1,2]:
                for q in LEGACY_QUESTIONS:
                    db.execute('INSERT INTO answers VALUES (?,?,?,?,?,?)',(i,q['id'],q.get('correct') or 'Respuesta anterior',q['points'] if i==1 else None,'Comentario anterior',self.clock))
            db.execute("INSERT INTO settings VALUES ('secret_key','persisted-legacy-secret')")
            old_answers = db.execute('SELECT * FROM answers ORDER BY evaluation_id,question_id').fetchall()
            old_evaluations = db.execute('SELECT * FROM evaluations ORDER BY id').fetchall()
            db.commit()
        app = create_app({**self.config,'DATABASE':str(database)})
        with app.app_context():
            db = get_db()
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM answers ORDER BY evaluation_id,question_id')],old_answers)
            for old, row in zip(old_evaluations,db.execute('SELECT * FROM evaluations ORDER BY id')):
                self.assertEqual(tuple(row)[:-1],old)
            self.assertEqual(db.execute('SELECT SUM(score) FROM answers WHERE evaluation_id=1').fetchone()[0],100)
            version = db.execute('SELECT version_id FROM evaluations WHERE id=1').fetchone()[0]
            historic = admin_questions(version,1)
            self.assertEqual(historic[0]['prompt'],LEGACY_QUESTIONS[0]['prompt'])
            self.assertEqual(historic[0]['answer_text'],LEGACY_QUESTIONS[0]['options'][1])
            self.assertEqual(historic[10]['tables'][0]['rows'][0][0],'12')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM questions').fetchone()[0],24)
            self.assertEqual(db.execute('SELECT COUNT(DISTINCT version_id) FROM evaluations').fetchone()[0],1)
        self.assertEqual(app.secret_key,'persisted-legacy-secret')
        client = app.test_client()
        with client.session_transaction() as session:
            session['candidate_token'] = 'legacy-token-2'
        self.assertEqual(client.get('/evaluacion').status_code,200)
        self.assertEqual(client.get('/api/evaluacion').json['answers']['6'],'Respuesta anterior')
        self.assertEqual(self.post(client,'/api/entregar',payload={'revision':3,'answers':{}}).status_code,200)
        with app.app_context():
            self.assertEqual(get_db().execute('SELECT SUM(score) FROM answers WHERE evaluation_id=2').fetchone()[0],20)
        create_app({**self.config,'DATABASE':str(database)})
        with app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM questions').fetchone()[0],24)


if __name__ == '__main__':
    unittest.main()
