"""One-time migration of pre-JSON databases. Never seeds questions for new exams.

The old application stored example tables in Python, so these frozen historical
tables are needed ONLY to preserve the display of its existing attempts.
"""
import json
from .exams import canonical, fingerprint

CLIENTS = [["1", "Ana Ruiz", "ana@correo.com"], ["2", "Luis Mora", "luis@correo.com"], ["3", "Erika Soto", "erika@correo.com"]]
ORDERS = [["101", "1", "1500"], ["102", "2", "900"], ["103", "1", "1200"], ["104", "3", "2500"]]
EXCEL = [["12", "Ana Ruiz", "5512345678", "ana@correo.com"], ["12", "Ana Ruiz", "5512345678", "ana@correo.com"], ["23", "Luis Mora", "55A1234567", "luis@correo.com"], ["0007", "Erika Soto", "5598765432", ""]]


def migrate_legacy(db, timestamp):
    rows = db.execute('SELECT * FROM questions WHERE version_id IS NULL ORDER BY id').fetchall()
    if not rows:
        if db.execute('SELECT 1 FROM evaluations WHERE version_id IS NULL').fetchone():
            raise RuntimeError('La base anterior contiene evaluaciones sin preguntas; no se modificó. Restaura un respaldo íntegro.')
        return
    document = dict(schema_version=0, exam_id='legacy-sql-assessment', title='SQL Assessment · examen anterior',
                    description='Versión histórica anterior a la importación JSON.', duration_minutes=45, max_score=100,
                    instructions=['Se conserva el examen original de esta evaluación.'], sections=[], questions=[])
    sections = {}
    for position, row in enumerate(rows, 1):
        section_id = sections.setdefault(row['section'], f'seccion-{len(sections)+1}')
        options = [{'id':str(i), 'text':text} for i, text in enumerate(json.loads(row['options']) or [])]
        grading = {'correct_option':row['correct']} if row['kind']=='choice' else {
            'mode':'manual', 'reference_answer':row['guide'], 'rubric':[{'criterion':row['rubric'], 'points':row['points']}], 'notes':''}
        tables = []
        if row['kind']=='sql':
            tables = [dict(name='Clientes', columns=['id_cliente','nombre','correo'], rows=CLIENTS),
                      dict(name='Pedidos', columns=['id_pedido','id_cliente','monto'], rows=ORDERS)]
        elif row['kind']=='cleanup':
            tables = [dict(name='Hoja de Excel · Clientes', columns=['ID','Nombre','Teléfono','Correo'], rows=EXCEL)]
        q = dict(id=f'legacy-{row["id"]}', section=section_id, title=row['title'], prompt=row['prompt'],
                 type={'choice':'multiple_choice','sql':'sql_text','text':'open_text','cleanup':'open_text'}[row['kind']],
                 points=row['points'], grading=grading, tables=tables)
        if options:
            q['options'] = options
        document['questions'].append(q)
    document['sections'] = [dict(id=sid,title=title,points=sum(q['points'] for q in document['questions'] if q['section']==sid)) for title,sid in sections.items()]
    cursor = db.execute('''INSERT INTO exam_versions(exam_id,version,content_hash,source_json,title,description,
        duration_seconds,max_score,created_at,source_file) VALUES (?,1,?,?,?,?,2700,100,?,?)''',
        (document['exam_id'], fingerprint(document), canonical(document), document['title'], document['description'], timestamp, '(base anterior)'))
    version_id = cursor.lastrowid
    for position, (row, q) in enumerate(zip(rows, document['questions']), 1):
        db.execute('''UPDATE questions SET version_id=?,source_id=?,position=?,section_id=?,options=?,tables_json=?,
            grading_json=?,source_json=? WHERE id=?''',
            (version_id, q['id'], position, q['section'], canonical(q.get('options', [])), canonical(q['tables']),
             canonical(q['grading']), canonical(q), row['id']))
    # Includes unclaimed codes: a code never silently switches to another exam.
    db.execute('UPDATE evaluations SET version_id=? WHERE version_id IS NULL', (version_id,))
