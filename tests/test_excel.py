"""Excel integration, immutable resources, identity boundaries and safe upgrades."""
import copy
import csv
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from contextlib import closing

from markupsafe import escape
from werkzeug.security import generate_password_hash
import test_app
from sql_assessment import create_app
from sql_assessment.db import get_db
from sql_assessment.exams import ExamValidationError, admin_questions, load_documents, public_questions, version_info

ROOT = Path(__file__).resolve().parents[1]
SOURCE = json.loads((ROOT/'evaluaciones/reactivos_excel_junior.json').read_text(encoding='utf-8'))
SQL = json.loads((ROOT/'evaluaciones/reactivos_sql_junior_v2.json').read_text(encoding='utf-8'))
WORKBOOK = (ROOT/'evaluaciones/plantilla_excel_junior.xlsx').read_bytes()


class ExcelTests(unittest.TestCase):
    post = test_app.AssessmentTests.post

    @classmethod
    def setUpClass(cls):
        cls.password = 'Pruebas Excel integración 2026'
        cls.password_hash = generate_password_hash(cls.password)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.exams = self.directory/'exams'
        shutil.copytree(ROOT/'evaluaciones', self.exams)
        self.clock = 1800000000
        self.config = dict(TESTING=True, DATABASE=str(self.directory/'test.sqlite3'), EXAMS_DIR=str(self.exams), NOW=lambda:self.clock)
        self.app = create_app(self.config)
        self.client, self.admin = self.app.test_client(), self.app.test_client()
        with self.app.app_context():
            with get_db() as db:
                db.execute("INSERT INTO settings VALUES ('admin_password',?)", (self.password_hash,))
        self.post(self.admin, '/admin/login', {'password':self.password})

    def tearDown(self):
        self.temp.cleanup()

    def code(self, exam_id='EXCEL_JUNIOR'):
        with self.app.app_context():
            version = get_db().execute('SELECT active_version_id FROM exam_catalog WHERE exam_id=?',(exam_id,)).fetchone()[0]
        self.assertEqual(self.post(self.admin, '/admin/codigos', {'count':1,'version_id':version}).status_code,302)
        with self.app.app_context():
            return dict(get_db().execute('SELECT * FROM evaluations ORDER BY id DESC LIMIT 1').fetchone())

    def claim(self, exam_id='EXCEL_JUNIOR', client=None):
        client = client or self.client
        attempt = self.code(exam_id)
        response = self.post(client, '/acceso', {'name':'Ana Candidata','code':attempt['code'],'exam_id':exam_id})
        self.assertEqual(response.status_code,302)
        return attempt

    def begin(self, exam_id='EXCEL_JUNIOR', client=None):
        client = client or self.client
        attempt = self.claim(exam_id,client)
        self.assertEqual(self.post(client, '/comenzar', {'evaluation_id':attempt['id']}).status_code,302)
        return attempt

    def submit(self, answers=None):
        state = self.client.get('/api/evaluacion').json
        return self.post(self.client,'/api/entregar',payload={'revision':state['revision'],'answers':answers or {}})

    def restart(self):
        cookies = [c.get_cookie('session') for c in (self.client,self.admin)]
        self.app = create_app(self.config)
        self.client,self.admin = self.app.test_client(),self.app.test_client()
        for client,cookie in zip((self.client,self.admin),cookies):
            if cookie:
                client.set_cookie('session',cookie.value)

    def dump(self):
        with self.app.app_context():
            return '\n'.join(get_db().iterdump())

    def test_exact_source_adapter_and_admin_rubrics(self):
        with self.app.app_context():
            db=get_db()
            row=db.execute("SELECT * FROM exam_versions WHERE exam_id='EXCEL_JUNIOR'").fetchone()
            doc=json.loads(row['source_json'])
            self.assertEqual(doc['source_document'], SOURCE)
            self.assertEqual((len(doc['questions']),row['max_score'],row['duration_seconds']),(12,100,2700))
            meta=version_info(row['id'])
            self.assertEqual((meta['manual_count'],meta['publication_version']),(7,'1.0.0'))
            for stored,source in zip(admin_questions(row['id']),SOURCE['preguntas']):
                self.assertEqual((stored['source_id'],stored['prompt'],stored['points']), (source['id'],source['enunciado'],source['puntaje']))
                if stored['kind']=='choice':
                    self.assertEqual(stored['correct'],source['correccion']['respuesta_correcta'])
                    self.assertEqual(stored['grading']['notes'],source['correccion']['justificacion_admin'])
                    self.assertEqual(stored['options'],[dict(id=o['id'],text=o['texto']) for o in source['opciones']])
                else:
                    self.assertEqual(stored['guide'],source['correccion']['respuesta_referencia'])
                    self.assertEqual(sum(c['points'] for c in stored['grading']['rubric']),stored['points'])
                    self.assertEqual(stored['grading']['rubric'],[dict(criterion=c['criterio'],points=c['puntos'],description=c['descripcion']) for c in source['correccion']['rubrica']['criterios']])

    def test_selector_instructions_context_and_download(self):
        welcome=self.client.get('/').get_data(as_text=True)
        for text in ['SQL Junior','Excel Junior','50 minutos','45 minutos','13 preguntas','12 preguntas']:
            self.assertIn(text,welcome)
        self.assertEqual(self.client.get('/evaluaciones').status_code,200)
        attempt=self.claim()
        instructions=self.client.get('/instrucciones').get_data(as_text=True)
        for text in SOURCE['evaluacion']['instrucciones_candidato']:
            self.assertIn(str(escape(text)),instructions)
        url=f'/recursos/{attempt["id"]}/dataset_excel_junior_v1'
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data,WORKBOOK)
        self.assertEqual(hashlib.sha256(response.data).hexdigest(),SOURCE['recursos'][0]['sha256'])
        self.assertIn('attachment;',response.headers['Content-Disposition'])
        self.post(self.client,'/comenzar')
        html=self.client.get('/evaluacion').get_data(as_text=True)
        self.assertEqual(html.count('class="question-card card"'),12)
        self.assertIn('Descargar archivo de práctica',html)
        self.assertIn('45:00',html)
        self.assertIn('Clientes',html)
        self.assertIn('C010',html)
        self.assertEqual(self.client.get('/api/evaluacion').json['deadline']-self.clock,2700)

    def test_public_projections_and_resource_access_are_private(self):
        attempt=self.begin()
        with self.app.app_context():
            safe=json.dumps(public_questions(attempt['version_id']))+json.dumps(version_info(attempt['version_id']))
        text=safe+self.client.get('/evaluacion').get_data(as_text=True)+self.client.get('/api/evaluacion').get_data(as_text=True)
        for field in ['respuesta_correcta','respuesta_referencia','justificacion_admin','rubrica','reference_answer','correct_option','grading','source_document']:
            self.assertNotIn(field,text)
        for q in SOURCE['preguntas']:
            correction=q['correccion']
            if 'respuesta_referencia' in correction:
                self.assertNotIn(correction['respuesta_referencia'],text)
                self.assertNotIn(str(escape(correction['respuesta_referencia'])),text)
        for path in ['/evaluaciones/reactivos_excel_junior.json','/static/reactivos_excel_junior.json','/evaluaciones/plantilla_excel_junior.xlsx','/recursos/1/../../instance/assessment.sqlite3']:
            self.assertEqual(self.client.get(path).status_code,404)
        self.assertEqual(self.client.get('/admin/candidatos/1').status_code,302)
        other=self.app.test_client()
        self.assertEqual(other.get(f'/recursos/{attempt["id"]}/dataset_excel_junior_v1').status_code,403)
        self.begin(client=other)
        self.assertEqual(other.get(f'/recursos/{attempt["id"]}/dataset_excel_junior_v1').status_code,403)
        self.assertEqual(self.client.get(f'/recursos/{attempt["id"]}/no-existe').status_code,404)
        guide=self.admin.get(f'/admin/guia?version_id={attempt["version_id"]}').get_data(as_text=True)
        for q in SOURCE['preguntas']:
            correction=q['correccion']
            self.assertIn(str(escape(correction.get('respuesta_referencia',correction.get('justificacion_admin')))),guide)

    def test_automatic_and_manual_partial_grades_bounds_and_no_code_execution(self):
        attempt=self.begin()
        answers={str(q['numero']):q['correccion']['respuesta_correcta'] for q in SOURCE['preguntas'][:5]}
        answers.update({'6':'=E2*F2','7':'=IF(H2="Pagado","Cobrado","No cobrado")','12':'Sub FormatearVentas()\nShell "probe"\nEnd Sub <script>alert(1)</script>'})
        self.assertEqual(self.submit(answers).status_code,200)
        with self.app.app_context():
            qs=admin_questions(attempt['version_id'],attempt['id'])
            self.assertEqual([q['score'] for q in qs],[4]*5+[None]*7)
            self.assertEqual(qs[11]['answer'],answers['12'])
        url=f'/admin/evaluaciones/{attempt["id"]}'
        for invalid in ['-1','10.1','NaN','inf']:
            self.assertEqual(self.post(self.admin,url,{'score_6':invalid,'action':'save'}).status_code,400)
        self.post(self.admin,url,{'score_6':'5.5','action':'save'})
        with self.app.app_context():
            qs=admin_questions(attempt['version_id'],attempt['id'])
            self.assertEqual(qs[5]['score'],5.5)
            self.assertEqual(qs[6]['score'],None)
        maximum={f'score_{q["numero"]}':q['puntaje'] for q in SOURCE['preguntas'][5:]}
        maximum['action']='finalize'
        self.assertEqual(self.post(self.admin,url,maximum).status_code,302)
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT SUM(score) FROM answers WHERE evaluation_id=?',(attempt['id'],)).fetchone()[0],100)
        self.assertEqual(self.client.get('/api/evaluacion').json['status'],'graded')
        html=self.admin.get(url).get_data(as_text=True)
        self.assertIn('&lt;script&gt;',html)
        self.assertNotIn('<script>alert',html)

    def test_wrong_and_empty_choices_receive_zero(self):
        attempt=self.begin()
        self.submit({'1':'A','2':'A','3':'B','4':'D'})
        with self.app.app_context():
            self.assertEqual([q['score'] for q in admin_questions(attempt['version_id'],attempt['id'])][:5],[0]*5)

    def test_same_candidate_both_exams_switching_isolated_and_stale_tab_rejected(self):
        sql=self.begin(SQL['exam_id'])
        self.post(self.client,'/api/respuestas',payload={'revision':0,'answers':{'6':'SQL original'}})
        sql_state=self.client.get('/api/evaluacion').json
        excel=self.begin()
        excel_state=self.client.get('/api/evaluacion').json
        self.assertEqual(excel_state['answers']['6'],'')
        with self.client.session_transaction() as session:
            csrf=session['csrf_token']
        stale=self.client.post('/api/respuestas',json={'revision':0,'answers':{'6':'wrong exam'}},headers={'X-CSRF-Token':csrf,'X-Evaluation-ID':str(sql['id'])})
        self.assertEqual(stale.status_code,409)
        self.assertEqual(self.client.get('/api/evaluacion',headers={'X-Evaluation-ID':str(sql['id'])}).status_code,409)
        self.assertEqual(self.client.get('/api/evaluacion').json,excel_state)
        self.submit({'6':'Excel original'})
        self.assertEqual(self.post(self.client,'/salir').status_code,409)
        self.post(self.client,f'/seleccionar/{sql["id"]}')
        self.assertEqual(self.client.get('/api/evaluacion').json,sql_state)
        self.assertEqual(self.post(self.client,'/comenzar',{'evaluation_id':excel['id']}).status_code,409)
        self.submit()
        with self.app.app_context():
            db=get_db()
            ids=[r[0] for r in db.execute('SELECT candidate_id FROM candidate_attempts ORDER BY evaluation_id')]
            self.assertEqual(len(set(ids)),1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM candidates').fetchone()[0],1)
        html=self.admin.get(f'/admin/candidatos/{ids[0]}').get_data(as_text=True)
        self.assertIn(SQL['exam_id'],html)
        self.assertIn('EXCEL_JUNIOR',html)
        other=self.app.test_client()
        self.begin(client=other)
        self.assertEqual(self.post(other,f'/seleccionar/{sql["id"]}').status_code,403)
        self.assertEqual(self.post(self.client,'/salir').status_code,302)
        self.assertEqual(self.client.get('/api/evaluacion').status_code,403)

    def test_selected_exam_must_match_code_and_identity_is_reused(self):
        attempt=self.code(SQL['exam_id'])
        response=self.post(self.client,'/acceso',{'name':'Ana Candidata','code':attempt['code'],'exam_id':'EXCEL_JUNIOR'})
        self.assertEqual(response.status_code,400)
        with self.app.app_context():
            self.assertIsNone(get_db().execute('SELECT candidate_token FROM evaluations WHERE id=?',(attempt['id'],)).fetchone()[0])
        self.begin()
        self.submit()
        self.assertIn('Ana Candidata',self.client.get('/acceso?exam_id='+SQL['exam_id']).get_data(as_text=True))
        new=self.code(SQL['exam_id'])
        self.post(self.client,'/acceso',{'name':'Nombre alterado','code':new['code']})
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT name FROM evaluations WHERE id=?',(new['id'],)).fetchone()[0],'Ana Candidata')

    def test_filters_and_csv_separate_results(self):
        sql=self.begin(SQL['exam_id']); self.submit({'6':'respuesta SQL'})
        excel=self.begin(); self.submit({'6':'=E2*F2'})
        html=self.admin.get('/admin?examen=EXCEL_JUNIOR').get_data(as_text=True)
        self.assertIn(excel['code'],html)
        self.assertNotIn(sql['code'],html)
        rows=list(csv.DictReader(io.StringIO(self.admin.get('/admin/exportar.csv?examen=EXCEL_JUNIOR').get_data(as_text=True).lstrip('\ufeff'))))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['exam_id'],'EXCEL_JUNIOR')
        self.assertEqual(rows[0]['P6 respuesta'],"'=E2*F2")
        self.assertNotIn('P13 respuesta',rows[0])

    def test_restart_twice_preserves_attempt_cookie_resource_and_import_counts(self):
        attempt=self.begin(); self.submit({'6':'respuesta estable'})
        before=self.dump()
        for _ in range(2):
            self.restart()
            self.assertEqual(self.dump(),before)
            self.assertTrue(all(not r['created'] for r in self.app.extensions['exam_imports']))
            self.assertEqual(self.client.get('/api/evaluacion').json['answers']['6'],'respuesta estable')
            self.assertEqual(self.client.get(f'/recursos/{attempt["id"]}/dataset_excel_junior_v1').data,WORKBOOK)
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM questions').fetchone()[0],25)

    def test_immutable_publication_and_resources_new_version_keeps_old_attempt(self):
        attempt=self.begin()
        source=copy.deepcopy(SOURCE)
        source['preguntas'][0]['enunciado']='Pregunta de versión siguiente'
        path=self.exams/'reactivos_excel_junior.json'
        path.write_text(json.dumps(source),encoding='utf-8')
        before=self.dump()
        with self.assertRaisesRegex(ExamValidationError,'nueva versión'):
            create_app(self.config)
        self.assertEqual(self.dump(),before)
        source['evaluacion']['version']='1.1.0'
        buffer=io.BytesIO(WORKBOOK)
        with zipfile.ZipFile(buffer,'a') as archive:
            archive.comment=b'Publication 1.1.0'
        next_workbook=buffer.getvalue()
        (self.exams/'plantilla_excel_junior.xlsx').write_bytes(next_workbook)
        source['recursos'][0]['sha256']=hashlib.sha256(next_workbook).hexdigest()
        path.write_text(json.dumps(source),encoding='utf-8')
        self.restart()
        html=self.client.get('/evaluacion').get_data(as_text=True)
        self.assertIn(str(escape(SOURCE['preguntas'][0]['enunciado'])),html)
        self.assertNotIn(source['preguntas'][0]['enunciado'],html)
        self.assertEqual(self.client.get(f'/recursos/{attempt["id"]}/dataset_excel_junior_v1').data,WORKBOOK)
        next_attempt=self.begin()
        self.assertNotEqual(next_attempt['version_id'],attempt['version_id'])
        self.assertEqual(self.client.get(f'/recursos/{next_attempt["id"]}/dataset_excel_junior_v1').data,next_workbook)
        with self.app.app_context():
            db=get_db()
            for statement in ["UPDATE exam_resources SET content=X'00'",'DELETE FROM exam_resources']:
                with self.assertRaises(sqlite3.IntegrityError), db:
                    db.execute(statement)

    def test_frozen_download_survives_source_file_change_but_restart_rejects_hash(self):
        attempt=self.claim()
        (self.exams/'plantilla_excel_junior.xlsx').write_bytes(b'changed')
        self.assertEqual(self.client.get(f'/recursos/{attempt["id"]}/dataset_excel_junior_v1').data,WORKBOOK)
        before=self.dump()
        with self.assertRaisesRegex(ExamValidationError,'SHA-256'):
            create_app(self.config)
        self.assertEqual(self.dump(),before)

    def test_invalid_source_rubrics_ids_policy_and_resource_paths_fail_before_db_write(self):
        path=self.exams/'reactivos_excel_junior.json'
        before=self.dump()
        changes=[lambda d:d['preguntas'][1].update(id=d['preguntas'][0]['id']),
                 lambda d:d['preguntas'][1].update(numero=1),
                 lambda d:d['preguntas'][0]['opciones'][0].update(id='B'),
                 lambda d:d['preguntas'][0]['correccion'].update(respuesta_correcta='X'),
                 lambda d:d['preguntas'][5]['correccion']['rubrica']['criterios'][0].update(puntos=5),
                 lambda d:d['evaluacion']['politica_calificacion'].update(mostrar_rubricas_al_candidato=True),
                 lambda d:d['preguntas'][5]['contexto_tabla'].update(hoja='missing'),
                 lambda d:d['recursos'][0].update(archivo='../private.xlsx'),
                 lambda d:d['recursos'].append(copy.deepcopy(d['recursos'][0])),
                 lambda d:d.update(recursos=[]),
                 lambda d:d['evaluacion'].update(version='invalid')]
        for change in changes:
            doc=copy.deepcopy(SOURCE); change(doc)
            path.write_text(json.dumps(doc),encoding='utf-8')
            with self.assertRaises(ExamValidationError):
                create_app(self.config)
            self.assertEqual(self.dump(),before)
        path.write_text(json.dumps(SOURCE),encoding='utf-8')
        (self.exams/'plantilla_excel_junior.xlsx').unlink()
        with self.assertRaises(ExamValidationError):
            create_app(self.config)

    def test_resource_formulas_and_macros_rejected_even_with_matching_hash(self):
        path=self.exams/'reactivos_excel_junior.json'
        before=self.dump()
        for macro in [False,True]:
            buffer=io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(WORKBOOK)) as original, zipfile.ZipFile(buffer,'w') as modified:
                for name in original.namelist():
                    content=original.read(name)
                    if not macro and name=='xl/worksheets/sheet1.xml':
                        content=content.replace(b'</x:sheetData>',b'<x:row r="99"><x:c r="A99"><x:f>SUM(1,2)</x:f></x:c></x:row></x:sheetData>')
                    modified.writestr(name,content)
                if macro:
                    modified.writestr('xl/vbaProject.bin',b'never execute')
            content=buffer.getvalue()
            source=copy.deepcopy(SOURCE)
            source['recursos'][0]['sha256']=hashlib.sha256(content).hexdigest()
            path.write_text(json.dumps(source),encoding='utf-8')
            (self.exams/'plantilla_excel_junior.xlsx').write_bytes(content)
            with self.assertRaisesRegex(ExamValidationError,'macros' if macro else 'fórmulas'):
                create_app(self.config)
            self.assertEqual(self.dump(),before)

    def test_expiration_scores_excel_automatically_and_keeps_manual_pending(self):
        attempt=self.begin()
        self.post(self.client,'/api/respuestas',payload={'revision':0,'answers':{'1':'B','6':'=E2*F2'}})
        self.clock+=2700
        state=self.client.get('/api/evaluacion').json
        self.assertEqual(state['status'],'expired')
        self.assertEqual(state['answers']['6'],'=E2*F2')
        with self.app.app_context():
            qs=admin_questions(attempt['version_id'],attempt['id'])
            self.assertEqual([q['score'] for q in qs],[4,0,0,0,0]+[None]*7)

    def test_pre_upgrade_sql_data_and_backup_survive_two_restarts(self):
        # Reproduce the previous schema on a disposable DB, with three SQL states.
        sql=self.begin(SQL['exam_id']); self.submit({'6':'SQL histórico'})
        self.post(self.admin,f'/admin/evaluaciones/{sql["id"]}',{**{f'score_{i}':q['points'] for i,q in enumerate(SQL['questions'],1) if q['type']!='multiple_choice'},'action':'finalize'})
        active=self.begin(SQL['exam_id'])
        self.post(self.client,'/api/respuestas',payload={'revision':0,'answers':{'13':'CREATE TABLE histórico (id INTEGER);'}})
        self.code(SQL['exam_id'])
        with self.app.app_context():
            db=get_db()
            before={table:[tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')] for table in ['evaluations','answers','questions','exam_versions','events','settings']}
            # Remove Excel and the three new tables only in this disposable test fixture.
            for name in ['immutable_questions_delete','immutable_exam_versions_delete','immutable_resources_delete']:
                db.execute('DROP TRIGGER '+name)
            db.execute('DROP TABLE candidate_attempts'); db.execute('DROP TABLE candidates'); db.execute('DROP TABLE exam_resources')
            excel_id=db.execute("SELECT id FROM exam_versions WHERE exam_id='EXCEL_JUNIOR'").fetchone()[0]
            db.execute("DELETE FROM exam_catalog WHERE exam_id='EXCEL_JUNIOR'")
            db.execute('DELETE FROM questions WHERE version_id=?',(excel_id,))
            db.execute('DELETE FROM exam_versions WHERE id=?',(excel_id,))
            db.commit()
            before={table:[tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')] for table in before}
        self.restart()
        backup=Path(self.app.extensions['migration_backup'])
        self.assertTrue(backup.exists())
        with closing(sqlite3.connect(backup)) as db:
            for table,values in before.items():
                self.assertEqual(db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall(),values)
        for _ in range(2):
            with self.app.app_context():
                db=get_db()
                for table,values in before.items():
                    # Newly imported Excel questions/version are additive.
                    rows=[tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                    self.assertEqual([r for r in rows if r[0] in {v[0] for v in values}],values)
            self.restart()
        self.assertEqual(self.client.get('/api/evaluacion').json['answers']['13'],'CREATE TABLE histórico (id INTEGER);')
        self.assertEqual(self.client.get('/api/evaluacion').json['status'],'in_progress')


if __name__=='__main__':
    unittest.main()
