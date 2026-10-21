"""Adapter for the supplied Spanish examination format. No candidate code executes."""
from copy import deepcopy
from .exams import ExamValidationError


def adapt_document(source, filename):
    """Preserve the complete source privately and map it to the existing exam schema."""
    try:
        if source['schema_version'] != '1.0':
            raise ValueError('schema_version debe ser "1.0"')
        meta = source['evaluacion']
        if meta['publicable'] is not True:
            raise ValueError('la evaluación no está marcada como publicable')
        questions = source['preguntas']
        numbers = [q['numero'] for q in questions]
        if any(type(n) is not int for n in numbers) or numbers != list(range(1, len(questions) + 1)):
            raise ValueError('numeración duplicada, incompleta o fuera de orden')
        policy = meta['politica_calificacion']
        auto = [q['numero'] for q in questions if q['tipo'] == 'opcion_multiple']
        manual = [q['numero'] for q in questions if q['tipo'] == 'respuesta_abierta']
        if (policy['preguntas_automaticas'] != auto or policy['preguntas_manuales'] != manual
                or policy['mostrar_respuestas_correctas_al_candidato'] is not False
                or policy['mostrar_rubricas_al_candidato'] is not False
                or policy['calificacion_parcial_permitida'] is not True):
            raise ValueError('política de calificación incompatible con tipos o privacidad')
        sheets = source['datos_apoyo']['hojas']
        if not isinstance(source['recursos'], list) or not source['recursos']:
            raise ValueError('debe declararse al menos un recurso de práctica')
        tables = {name: {'name': name, 'columns': t['columnas'], 'rows': t['filas']}
                  for name, t in sheets.items()}
        document = dict(schema_version=1, exam_id=meta['codigo'], title=meta['titulo'],
                        description=meta['descripcion'], duration_minutes=meta['duracion_minutos'],
                        max_score=meta['puntaje_maximo'], instructions=meta['instrucciones_candidato'],
                        publication_version=meta['version'], sections=[], questions=[],
                        support_tables=list(tables.values()),
                        support_note=source['datos_apoyo'].get('aclaracion', ''),
                        source_document=deepcopy(source), resources=[])
        for resource in source['recursos']:
            if resource['descargable_por_candidato'] is not True:
                raise ValueError('los recursos de práctica deben ser descargables')
            document['resources'].append(dict(id=resource['id'], filename=resource['archivo'],
                label=resource['nombre'], mime_type=resource['mime_type'], sha256=resource['sha256']))
        for q in questions:
            choice = q['tipo'] == 'opcion_multiple'
            if q['tipo'] not in ('opcion_multiple', 'respuesta_abierta'):
                raise ValueError(f'tipo de pregunta desconocido: {q["tipo"]}')
            correction = q['correccion']
            if correction['modo'] != ('automatica' if choice else 'manual'):
                raise ValueError('modo de corrección incompatible con el tipo de pregunta')
            section = 'fundamentos' if choice else 'practica'
            converted = dict(id=q['id'], section=section, type='multiple_choice' if choice else 'open_text',
                             points=q['puntaje'], prompt=q['enunciado'], title=q['tema'])
            if choice:
                converted['options'] = [dict(id=o['id'], text=o['texto']) for o in q['opciones']]
                converted['grading'] = dict(correct_option=correction['respuesta_correcta'],
                                            notes=correction['justificacion_admin'])
            else:
                converted['grading'] = dict(mode='manual', reference_answer=correction['respuesta_referencia'],
                    rubric=[dict(criterion=c['criterio'], points=c['puntos'], description=c['descripcion'])
                            for c in correction['rubrica']['criterios']])
            if 'contexto_tabla' in q:
                context = q['contexto_tabla']
                if context['origen'] != 'datos_apoyo.hojas' or context['hoja'] not in tables:
                    raise ValueError('contexto_tabla debe referirse a una hoja de datos_apoyo')
                converted['tables'] = [tables[context['hoja']]]
            document['questions'].append(converted)
        for sid, title in [('fundamentos', 'Conocimientos de Excel'), ('practica', 'Excel práctico')]:
            points = sum(q['points'] for q in document['questions'] if q['section'] == sid)
            if points:
                document['sections'].append(dict(id=sid, title=title, points=points))
        return document
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ExamValidationError([f'{filename}: formato Excel inválido: {error}']) from error
