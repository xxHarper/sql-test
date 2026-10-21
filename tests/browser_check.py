"""Optional end-to-end browser verification against a real local Waitress server.

Run: python tests/browser_check.py
Requires playwright and its Chromium browser, not needed by the application.
Uses a temporary database and a randomly generated test password.
"""
import secrets
import hashlib
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright, expect
from waitress import create_server
from werkzeug.security import generate_password_hash
from sql_assessment import create_app
from sql_assessment.db import get_db
from sql_assessment.exams import active_versions, version_info
from run import deadline_worker


def main():
    screenshots = Path(__file__).resolve().parents[1] / 'artifacts'
    screenshots.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        clock = [int(time.time())]
        password = secrets.token_urlsafe(24)
        app = create_app({'DATABASE':str(Path(directory) / 'browser.sqlite3'), 'NOW': lambda: clock[0]})
        with app.app_context():
            active = active_versions()
            assert len(active) == 2 and active[0]['exam_id'] == 'sql-migracion-junior-v2'
            excel_version = active[1]['id']
            excel_document = json.loads(active[1]['source_json'])
            document = json.loads(active[0]['source_json'])
            questions = [dict(id=i, points=q['points'], kind=q['type'], correct=q['grading'].get('correct_option')) for i,q in enumerate(document['questions'],1)]
            assert len(questions) == 13 and document['duration_minutes'] == 50
            with get_db() as db:
                db.execute("INSERT INTO settings VALUES ('admin_password',?)", (generate_password_hash(password),))
        server = create_server(app, host='127.0.0.1', port=0, threads=4)
        port = server.effective_port
        url = f'http://127.0.0.1:{port}'
        serving = threading.Thread(target=server.run, daemon=True)
        serving.start()
        stop = threading.Event()
        worker = threading.Thread(target=deadline_worker, args=(app, stop), daemon=True)
        worker.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
                admin_context = browser.new_context(viewport={'width':1440,'height':1000})
                candidate_context = browser.new_context(viewport={'width':1440,'height':1000})
                admin = admin_context.new_page()
                candidate = candidate_context.new_page()
                errors, external = [], []
                for page in [admin, candidate]:
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('console', lambda message: errors.append(message.text) if 'Content Security Policy' in message.text else None)
                    page.on('request', lambda request: external.append(request.url) if not request.url.startswith(url) else None)
                candidate.goto(url)
                candidate.screenshot(path=str(screenshots / 'bienvenida.png'), full_page=True)
                admin.goto(url + '/admin')
                admin.get_by_label('Contraseña de administrador').fill(password)
                admin.get_by_role('button', name='Entrar al panel').click()
                expect(admin.get_by_role('heading', name='Evaluaciones', exact=True)).to_be_visible()
                expect(admin.locator('#version-id option:checked')).to_contain_text('sql-migracion-junior-v2')
                admin.locator('#count').fill('2')
                admin.get_by_role('button', name='Crear códigos').click()
                codes = admin.locator('.code-row code').all_text_contents()
                assert len(codes) == 2
                admin.screenshot(path=str(screenshots / 'administracion.png'), full_page=True)
                candidate.get_by_role('link', name='Acceder a mi evaluación').click()
                candidate.get_by_label('Nombre completo').fill('Candidata de prueba')
                candidate.get_by_label('Código de evaluación').fill(codes[0])
                candidate.get_by_role('button', name='Ver instrucciones').click()
                expect(candidate.get_by_role('heading', name='Hola, Candidata de prueba.')).to_be_visible()
                expect(candidate.locator('.metrics')).to_contain_text('50')
                candidate.get_by_role('button', name='Comenzar evaluación').click()
                expect(candidate.locator('#save-status')).to_contain_text('Todos los cambios guardados')
                assert candidate.locator('.question-card:visible').count() == 1
                for question in questions[:5]:
                    candidate.locator(f'[data-go="{question["id"]-1}"]').click()
                    candidate.locator(f'input[data-answer="{question["id"]}"][value="{question["correct"]}"]').check()
                    expect(candidate.locator('#save-status')).to_contain_text('Todos los cambios guardados')
                candidate.locator('[data-go="5"]').click()
                candidate.locator('#answer-6').fill('SELECT nombre, correo FROM Clientes;')
                expect(candidate.locator('#save-status')).to_contain_text('Todos los cambios guardados')
                candidate.screenshot(path=str(screenshots / 'pregunta-sql.png'), full_page=True)
                # Simulate a brief connection failure, then recover unsaved changes.
                candidate_context.set_offline(True)
                candidate.locator('#answer-6').fill('SELECT nombre, correo FROM Clientes;\n-- respuesta recuperada')
                expect(candidate.locator('#network-alert')).to_be_visible(timeout=15000)
                candidate_context.set_offline(False)
                expect(candidate.locator('#save-status')).to_contain_text('Todos los cambios guardados', timeout=20000)
                candidate.reload()
                expect(candidate.locator('#save-status')).to_contain_text('Todos los cambios guardados')
                expect(candidate.locator('#answer-6')).to_have_value('SELECT nombre, correo FROM Clientes;\n-- respuesta recuperada')
                for question in questions[6:]:
                    candidate.locator(f'[data-go="{question["id"]-1}"]').click()
                    answer = document['questions'][question['id']-1]['grading']['reference_answer'] if question['id']==13 else 'Respuesta de prueba para revisión manual.'
                    if question['id']==13:
                        expect(candidate.locator('[data-question="13"] .sql-editor textarea')).to_be_visible()
                        for q in document['questions']:
                            if q['type'] != 'multiple_choice':
                                assert q['grading']['reference_answer'] not in candidate.content()
                    candidate.locator(f'#answer-{question["id"]}').fill(answer)
                    expect(candidate.locator('#save-status')).to_contain_text('Todos los cambios guardados')
                candidate.screenshot(path=str(screenshots / 'pregunta-13.png'), full_page=True)
                candidate.set_viewport_size({'width':390,'height':844})
                candidate.locator('[data-go="10"]').click()
                candidate.screenshot(path=str(screenshots / 'movil.png'), full_page=True)
                overflow = candidate.evaluate('''() => [...document.querySelectorAll('body *')]
                    .filter(e => e.getBoundingClientRect().right > innerWidth && getComputedStyle(e).position !== 'absolute')
                    .map(e => ({tag:e.tagName, cls:e.className, right:e.getBoundingClientRect().right})).slice(0,15)''')
                assert candidate.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Mobile page overflow: {overflow}'
                candidate.get_by_role('button', name='Entregar evaluación', exact=True).click()
                expect(candidate.locator('#answered-count')).to_have_text(str(len(questions)))
                candidate.get_by_role('button', name='Seguir revisando').click()
                expect(candidate.locator('#submit-dialog')).not_to_be_visible()
                candidate.get_by_role('button', name='Entregar evaluación', exact=True).click()
                candidate.get_by_role('button', name='Confirmar entrega').click()
                expect(candidate.get_by_role('heading', name='Gracias, Candidata de prueba.')).to_be_visible()
                assert 'Muy buen candidato' not in candidate.locator('body').inner_text()
                admin.reload()
                row = admin.locator('tr', has=admin.locator('code', has_text=codes[0]))
                expect(row).to_contain_text('Entregado')
                row.get_by_role('link', name='Revisar').click()
                expect(admin.locator('.grading-question')).to_have_count(13)
                for criterion in document['questions'][12]['grading']['rubric']:
                    expect(admin.get_by_text(criterion['criterion'] + ' — ' + str(criterion['points']) + ' puntos',exact=True)).to_have_count(1)
                for question in questions[5:]:
                    admin.locator(f'#score-{question["id"]}').fill(str(question['points']))
                admin.locator('#comment-6').fill('Consulta clara. <script>no ejecutar</script>')
                admin.get_by_role('button', name='Guardar revisión', exact=True).click()
                expect(admin.get_by_text('Revisión guardada.', exact=True)).to_be_visible()
                admin.get_by_role('button', name='Finalizar calificación').click()
                expect(admin.locator('.score-summary')).to_contain_text('100')
                expect(admin.locator('.score-summary')).to_contain_text('Muy buen candidato')
                with admin.expect_download() as download_info:
                    admin.get_by_role('link', name='Exportar resultados').click()
                download = download_info.value
                download.save_as(str(screenshots / 'resultados-prueba.csv'))
                assert 'Candidata de prueba' in Path(download.path()).read_text(encoding='utf-8-sig')
                admin.screenshot(path=str(screenshots / 'calificacion.png'), full_page=True)
                # The same authenticated candidate now selects and completes Excel.
                admin.goto(url + '/admin')
                admin.locator('#version-id').select_option(str(excel_version))
                admin.locator('#count').fill('1')
                admin.get_by_role('button', name='Crear códigos').click()
                excel_code = admin.locator('.code-row code').first.inner_text()
                assert excel_code.startswith('EXC-')
                candidate.get_by_role('link', name='Volver a Evaluaciones').click()
                expect(candidate.get_by_role('heading', name='SQL Junior',exact=True)).to_be_visible()
                expect(candidate.get_by_role('heading', name='Excel Junior',exact=True)).to_be_visible()
                candidate.screenshot(path=str(screenshots / 'selector-evaluaciones.png'),full_page=True)
                candidate.get_by_role('link', name='Elegir Excel Junior').click()
                expect(candidate.locator('#name')).to_have_value('Candidata de prueba')
                expect(candidate.locator('#name')).to_have_attribute('readonly','')
                candidate.get_by_label('Código de evaluación').fill(excel_code)
                candidate.get_by_role('button', name='Ver instrucciones').click()
                with candidate.expect_download() as info:
                    candidate.get_by_role('link',name='Descargar archivo de práctica').click()
                workbook = Path(info.value.path()).read_bytes()
                assert hashlib.sha256(workbook).hexdigest() == excel_document['resources'][0]['sha256']
                candidate.get_by_role('button', name='Comenzar evaluación').click()
                expect(candidate.locator('#save-status')).to_contain_text('Todos los cambios guardados')
                expect(candidate.locator('.question-card')).to_have_count(12)
                expect(candidate.locator('#timer')).to_contain_text('44:')
                candidate.locator('.practice-resources summary').click()
                expect(candidate.locator('.practice-resources')).to_contain_text('C010')
                assert candidate.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Excel mobile overflow'
                candidate.locator('.practice-resources summary').click()
                candidate.locator('[data-go="5"]').click()
                candidate.screenshot(path=str(screenshots / 'excel-movil.png'),full_page=True)
                for i,q in enumerate(excel_document['questions'],1):
                    candidate.locator(f'[data-go="{i-1}"]').click()
                    if q['type']=='multiple_choice':
                        candidate.locator(f'input[data-answer="{i}"][value="{q["grading"]["correct_option"]}"]').check()
                    else:
                        candidate.locator(f'#answer-{i}').fill('=E2*F2; copiar hasta G13' if i==6 else 'Explicación o pseudocódigo para revisión manual.')
                    expect(candidate.locator('#save-status')).to_contain_text('Todos los cambios guardados')
                candidate.get_by_role('button',name='Entregar evaluación',exact=True).click()
                expect(candidate.locator('#answered-count')).to_have_text('12')
                candidate.get_by_role('button',name='Confirmar entrega').click()
                expect(candidate.get_by_role('heading',name='Gracias, Candidata de prueba.')).to_be_visible()
                admin.reload()
                row = admin.locator('tr',has=admin.locator('code',has_text=excel_code))
                row.get_by_role('link',name='Revisar',exact=False).click()
                expect(admin.locator('.grading-question')).to_have_count(12)
                for i,q in enumerate(excel_document['questions'],1):
                    if q['type']!='multiple_choice':
                        admin.locator(f'#score-{i}').fill(str(q['points']))
                admin.get_by_role('button',name='Finalizar calificación').click()
                expect(admin.locator('.score-summary')).to_contain_text('100')
                admin.get_by_role('link',name='Ver evaluaciones del candidato').click()
                expect(admin.locator('tbody tr')).to_have_count(2)
                expect(admin.locator('tbody')).to_contain_text('EXCEL_JUNIOR')
                expect(admin.locator('tbody')).to_contain_text('sql-migracion-junior-v2')
                admin.screenshot(path=str(screenshots / 'candidato-sql-excel.png'),full_page=True)
                # Verify expiration in the browser without waiting 50 real minutes.
                timeout_context = browser.new_context(viewport={'width':390,'height':844})
                timeout_page = timeout_context.new_page()
                timeout_page.goto(url + '/acceso')
                timeout_page.get_by_label('Nombre completo').fill('Prueba de vencimiento')
                timeout_page.get_by_label('Código de evaluación').fill(codes[1])
                timeout_page.get_by_role('button', name='Ver instrucciones').click()
                timeout_page.get_by_role('button', name='Comenzar evaluación').click()
                expect(timeout_page.locator('#save-status')).to_contain_text('Todos los cambios guardados')
                clock[0] += document['duration_minutes'] * 60
                expect(timeout_page.get_by_role('heading', name='Gracias, Prueba de vencimiento.')).to_be_visible(timeout=15000)
                expect(timeout_page.get_by_text('Se entregaron automáticamente tus respuestas', exact=False)).to_be_visible()
                assert not errors, errors
                assert not external, external
                browser.close()
                print('OK: SQL 13/100/50 y Excel 12/100/45 en una app; mismo candidato, selector, descarga SHA-256, tablas de apoyo, móvil, calificaciones 100/100, historial por candidato, CSV, recuperación y vencimiento. Sin errores JavaScript ni peticiones externas.')
        finally:
            stop.set()
            worker.join(3)
            server.close()


if __name__ == '__main__':
    main()
