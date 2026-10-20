"""Validate official JSON documents before any database access; import immutable versions.

Documents are data only. Public projections use explicit allowlists, never raw JSON.
"""
import hashlib
import json
import math
import re
from decimal import Decimal
from pathlib import Path


class ExamValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__('No se importaron evaluaciones. Corrige estos errores:\n' + '\n'.join(f'- {e}' for e in errors))


def canonical(document):
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(document):
    return hashlib.sha256(canonical(document).encode('utf-8')).hexdigest()


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Propiedad JSON duplicada: {key}')
        result[key] = value
    return result


def validate_document(document, source='<JSON>'):
    errors = []

    def error(path, message):
        errors.append(f'{source}: {path}: {message}')

    def obj(value, path, required, optional=()):
        if not isinstance(value, dict):
            error(path, 'debe ser un objeto')
            return {}
        for key in required:
            if key not in value:
                error(f'{path}.{key}', 'campo obligatorio ausente')
        for key in value.keys() - set(required) - set(optional):
            error(f'{path}.{key}', 'campo desconocido en schema_version 1')
        return value

    def text(value, path, empty=False):
        if not isinstance(value, str) or ('\x00' in value) or (not empty and not value.strip()):
            error(path, 'debe ser texto válido' + ('' if empty else ' no vacío'))
            return False
        return True

    def identifier(value, path):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', value):
            error(path, 'identificador inválido (letras, números, guion y guion bajo; máximo 128 caracteres)')
            return False
        return True

    def number(value, path):
        if type(value) not in (int, float) or not 0 < value <= 100 or not math.isfinite(value):
            error(path, 'debe ser un número finito mayor que 0 y hasta 100')
            return None
        return Decimal(str(value))

    def array(value, path, nonempty=True):
        if not isinstance(value, list) or (nonempty and not value):
            error(path, 'debe ser una lista' + (' no vacía' if nonempty else ''))
            return []
        return value

    root = obj(document, '$', ('schema_version', 'exam_id', 'title', 'description', 'duration_minutes', 'max_score', 'instructions', 'sections', 'questions'), ('internal_notes',))
    if type(root.get('schema_version')) is not int or root.get('schema_version') != 1:
        error('schema_version', 'solo se admite la versión 1')
    identifier(root.get('exam_id'), 'exam_id')
    text(root.get('title'), 'title')
    text(root.get('description'), 'description', empty=True)
    if type(root.get('duration_minutes')) is not int or not 1 <= root.get('duration_minutes', 0) <= 1440:
        error('duration_minutes', 'debe ser un entero entre 1 y 1440')
    if type(root.get('max_score')) not in (int, float) or root.get('max_score') != 100:
        error('max_score', 'debe ser exactamente 100')
    if 'internal_notes' in root:
        text(root['internal_notes'], 'internal_notes', empty=True)
    for i, instruction in enumerate(array(root.get('instructions'), 'instructions')):
        text(instruction, f'instructions[{i}]')
    sections = {}
    for i, value in enumerate(array(root.get('sections'), 'sections')):
        path = f'sections[{i}]'
        section = obj(value, path, ('id', 'title', 'points'))
        sid = section.get('id')
        if identifier(sid, path + '.id'):
            if sid in sections:
                error(path + '.id', f'identificador de sección duplicado: {sid}')
            sections[sid] = section
        text(section.get('title'), path + '.title')
        number(section.get('points'), path + '.points')
    questions = array(root.get('questions'), 'questions')
    if len(questions) > 200:
        error('questions', 'se admiten hasta 200 preguntas por evaluación')
    seen, total, totals = set(), Decimal(0), {sid: Decimal(0) for sid in sections}
    for i, value in enumerate(questions):
        path = f'questions[{i}]'
        q = obj(value, path, ('id', 'section', 'type', 'points', 'prompt', 'grading'), ('options', 'tables', 'title'))
        qid = q.get('id')
        if identifier(qid, path + '.id'):
            if qid in seen:
                error(path + '.id', f'identificador de pregunta duplicado: {qid}')
            seen.add(qid)
        sid = q.get('section')
        if not isinstance(sid, str) or sid not in sections:
            error(path + '.section', 'debe referirse al id de una sección existente')
        score = number(q.get('points'), path + '.points')
        if score is not None:
            total += score
            if isinstance(sid, str) and sid in totals:
                totals[sid] += score
        text(q.get('prompt'), path + '.prompt')
        if 'title' in q:
            text(q['title'], path + '.title')
        kind = q.get('type')
        if kind not in ('multiple_choice', 'sql_text', 'open_text'):
            error(path + '.type', 'debe ser multiple_choice, sql_text u open_text')
        grading = q.get('grading')
        if kind == 'multiple_choice':
            grading = obj(grading, path + '.grading', ('correct_option',))
            options = array(q.get('options'), path + '.options')
            if not 2 <= len(options) <= 26:
                error(path + '.options', 'debe contener entre 2 y 26 opciones')
            option_ids = set()
            for j, value in enumerate(options):
                opath = f'{path}.options[{j}]'
                option = obj(value, opath, ('id', 'text'))
                oid = option.get('id')
                if identifier(oid, opath + '.id'):
                    if oid in option_ids:
                        error(opath + '.id', f'identificador de opción duplicado: {oid}')
                    option_ids.add(oid)
                text(option.get('text'), opath + '.text')
            if not isinstance(grading.get('correct_option'), str) or grading.get('correct_option') not in option_ids:
                error(path + '.grading.correct_option', 'debe identificar una de las opciones')
        elif kind in ('sql_text', 'open_text'):
            if 'options' in q:
                error(path + '.options', 'solo se permite en multiple_choice')
            grading = obj(grading, path + '.grading', ('mode', 'reference_answer', 'rubric'), ('notes',))
            if grading.get('mode') != 'manual':
                error(path + '.grading.mode', 'SQL y preguntas abiertas requieren mode="manual"')
            text(grading.get('reference_answer'), path + '.grading.reference_answer')
            if 'notes' in grading:
                text(grading['notes'], path + '.grading.notes', empty=True)
            rubric_total = Decimal(0)
            for j, value in enumerate(array(grading.get('rubric'), path + '.grading.rubric')):
                rpath = f'{path}.grading.rubric[{j}]'
                criterion = obj(value, rpath, ('criterion', 'points'))
                text(criterion.get('criterion'), rpath + '.criterion')
                points = number(criterion.get('points'), rpath + '.points')
                if points is not None:
                    rubric_total += points
            if score is not None and rubric_total != score:
                error(path + '.grading.rubric', f'suma {rubric_total}; debe sumar {score}')
        if 'tables' in q:
            for j, value in enumerate(array(q['tables'], path + '.tables', nonempty=False)):
                tpath = f'{path}.tables[{j}]'
                table = obj(value, tpath, ('name', 'columns', 'rows'))
                text(table.get('name'), tpath + '.name')
                columns = array(table.get('columns'), tpath + '.columns')
                for k, column in enumerate(columns):
                    text(column, f'{tpath}.columns[{k}]')
                for k, row in enumerate(array(table.get('rows'), tpath + '.rows', nonempty=False)):
                    if not isinstance(row, list) or len(row) != len(columns):
                        error(f'{tpath}.rows[{k}]', f'debe contener {len(columns)} celdas')
                        continue
                    for cell in row:
                        if cell is not None and not isinstance(cell, (str, int, float, bool)):
                            error(f'{tpath}.rows[{k}]', 'las celdas deben ser valores escalares JSON')
                        elif isinstance(cell, float) and not math.isfinite(cell):
                            error(f'{tpath}.rows[{k}]', 'no se admiten números no finitos')
    if total != 100:
        error('questions.points', f'la suma es {total}; debe ser 100')
    for sid, section in sections.items():
        points = section.get('points')
        if type(points) in (int, float) and 0 < points <= 100 and totals[sid] != Decimal(str(points)):
            error(f'sections.{sid}.points', f'declara {points}; sus preguntas suman {totals[sid]}')
    if errors:
        raise ExamValidationError(errors)
    return document


def load_documents(directory):
    """Read and validate the entire batch before connecting to SQLite."""
    paths = sorted(Path(directory).glob('*.json'))
    if not paths:
        raise ExamValidationError([f'{directory}: no hay archivos .json de evaluación'])
    result, errors, identities = [], [], set()
    for path in paths:
        try:
            document = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique_keys,
                                  parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f'Número JSON inválido: {value}')))
            validate_document(document, path.name)
            if document['exam_id'] in identities:
                errors.append(f'{path.name}: exam_id duplicado entre archivos: {document["exam_id"]}; conserva un archivo vigente por examen')
            identities.add(document['exam_id'])
            result.append((path.name, document))
        except ExamValidationError as error:
            errors.extend(error.errors)
        except (OSError, UnicodeError, ValueError, RecursionError) as error:
            errors.append(f'{path.name}: {error}')
    if errors:
        raise ExamValidationError(errors)
    return result


def import_documents(db, documents, timestamp):
    """Called inside the schema/import transaction. Existing versions are never updated."""
    db.execute('UPDATE exam_catalog SET enabled=0')
    results = []
    for filename, document in documents:
        digest = fingerprint(document)
        row = db.execute('SELECT id,version FROM exam_versions WHERE exam_id=? AND content_hash=?', (document['exam_id'], digest)).fetchone()
        if row:
            version_id, version = row['id'], row['version']
            created = False
        else:
            version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM exam_versions WHERE exam_id=?', (document['exam_id'],)).fetchone()[0]
            cursor = db.execute('''INSERT INTO exam_versions(exam_id,version,content_hash,source_json,title,
                description,duration_seconds,max_score,created_at,source_file) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (document['exam_id'], version, digest, canonical(document), document['title'], document['description'],
                 document['duration_minutes'] * 60, document['max_score'], timestamp, filename))
            version_id = cursor.lastrowid
            sections = {s['id']: s for s in document['sections']}
            for position, q in enumerate(document['questions'], 1):
                grading = q['grading']
                choice = q['type'] == 'multiple_choice'
                db.execute('''INSERT INTO questions(section,kind,title,prompt,points,options,correct,guide,rubric,
                    version_id,source_id,position,section_id,tables_json,grading_json,source_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (sections[q['section']]['title'], {'multiple_choice':'choice','sql_text':'sql','open_text':'text'}[q['type']],
                     q.get('title', f'Pregunta {position:02d}'), q['prompt'], q['points'], canonical(q.get('options', [])),
                     grading['correct_option'] if choice else None,
                     next(o['text'] for o in q['options'] if o['id'] == grading['correct_option']) if choice else grading['reference_answer'],
                     canonical(grading.get('rubric', [])), version_id, q['id'], position, q['section'],
                     canonical(q.get('tables', [])), canonical(grading), canonical(q)))
            created = True
        db.execute('''INSERT INTO exam_catalog(exam_id,active_version_id,enabled) VALUES (?,?,1)
            ON CONFLICT(exam_id) DO UPDATE SET active_version_id=excluded.active_version_id,enabled=1''', (document['exam_id'], version_id))
        results.append({'exam_id':document['exam_id'], 'version':version, 'created':created})
    return results


def version_info(version_id):
    from .db import get_db
    row = get_db().execute('SELECT * FROM exam_versions WHERE id=?', (version_id,)).fetchone()
    if row is None:
        raise RuntimeError(f'Versión de evaluación inexistente: {version_id}')
    document = json.loads(row['source_json'])
    # Deliberate allowlist: no grading, raw document or internal_notes leaves this helper.
    return dict(id=row['id'], exam_id=row['exam_id'], version=row['version'], title=row['title'],
                description=row['description'], duration_minutes=row['duration_seconds'] // 60,
                max_score=row['max_score'], instructions=document['instructions'], sections=document['sections'],
                question_count=len(document['questions']),
                manual_count=sum(q['type'] != 'multiple_choice' for q in document['questions']))


def active_versions():
    from .db import get_db
    return get_db().execute('''SELECT v.* FROM exam_catalog c JOIN exam_versions v ON v.id=c.active_version_id
        WHERE c.enabled=1 ORDER BY v.exam_id''').fetchall()


def public_questions(version_id):
    from .db import get_db
    rows = get_db().execute('''SELECT position,source_id,section,section_id,kind,title,prompt,points,options,tables_json
        FROM questions WHERE version_id=? ORDER BY position''', (version_id,)).fetchall()
    return [dict(id=q['position'], source_id=q['source_id'], section=q['section'], section_id=q['section_id'],
                 kind=q['kind'], title=q['title'], prompt=q['prompt'], points=q['points'],
                 options=[{'id':o['id'], 'text':o['text']} for o in json.loads(q['options'])],
                 tables=[{'name':t['name'], 'columns':t['columns'], 'rows':t['rows']} for t in json.loads(q['tables_json'])]) for q in rows]


def admin_questions(version_id, evaluation_id=None):
    from .db import get_db
    rows = get_db().execute('''SELECT q.*,a.answer,a.score,a.comment FROM questions q
        LEFT JOIN answers a ON a.question_id=q.id AND a.evaluation_id=?
        WHERE q.version_id=? ORDER BY q.position''', (evaluation_id, version_id)).fetchall()
    result = []
    for row in rows:
        q = dict(row)
        q['db_id'], q['id'] = q['id'], q['position']
        q['options'] = json.loads(q['options'])
        q['tables'] = json.loads(q['tables_json'])
        q['grading'] = json.loads(q['grading_json'])
        q['answer_text'] = next((o['text'] for o in q['options'] if o['id'] == q['answer']), q['answer'] or 'Sin respuesta')
        result.append(q)
    return result
