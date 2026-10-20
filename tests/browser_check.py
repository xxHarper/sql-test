"""Optional end-to-end browser verification against a real local Waitress server.

Run: python tests/browser_check.py
Requires playwright and its Chromium browser, not needed by the application.
Uses a temporary database and a randomly generated test password.
"""
import secrets
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
from exam_fixture import QUESTIONS
from run import deadline_worker


def main():
    screenshots = Path(__file__).resolve().parents[1] / 'artifacts'
    screenshots.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        clock = [int(time.time())]
        password = secrets.token_urlsafe(24)
        app = create_app({'DATABASE':str(Path(directory) / 'browser.sqlite3'), 'NOW': lambda: clock[0]})
        with app.app_context():
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
                candidate.get_by_role('button', name='Comenzar evaluación').click()
                expect(candidate.locator('#save-status')).to_contain_text('Todos los cambios guardados')
                assert candidate.locator('.question-card:visible').count() == 1
                for question in QUESTIONS[:5]:
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
                for question in QUESTIONS[6:]:
                    candidate.locator(f'[data-go="{question["id"]-1}"]').click()
                    candidate.locator(f'#answer-{question["id"]}').fill('Respuesta de prueba para revisión manual.')
                    expect(candidate.locator('#save-status')).to_contain_text('Todos los cambios guardados')
                candidate.set_viewport_size({'width':390,'height':844})
                candidate.locator('[data-go="10"]').click()
                candidate.screenshot(path=str(screenshots / 'movil.png'), full_page=True)
                overflow = candidate.evaluate('''() => [...document.querySelectorAll('body *')]
                    .filter(e => e.getBoundingClientRect().right > innerWidth && getComputedStyle(e).position !== 'absolute')
                    .map(e => ({tag:e.tagName, cls:e.className, right:e.getBoundingClientRect().right})).slice(0,15)''')
                assert candidate.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Mobile page overflow: {overflow}'
                candidate.get_by_role('button', name='Entregar evaluación', exact=True).click()
                expect(candidate.locator('#answered-count')).to_have_text('12')
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
                for question in QUESTIONS[5:]:
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
                # Verify expiration in the browser without waiting 45 real minutes.
                timeout_context = browser.new_context(viewport={'width':390,'height':844})
                timeout_page = timeout_context.new_page()
                timeout_page.goto(url + '/acceso')
                timeout_page.get_by_label('Nombre completo').fill('Prueba de vencimiento')
                timeout_page.get_by_label('Código de evaluación').fill(codes[1])
                timeout_page.get_by_role('button', name='Ver instrucciones').click()
                timeout_page.get_by_role('button', name='Comenzar evaluación').click()
                expect(timeout_page.locator('#save-status')).to_contain_text('Todos los cambios guardados')
                clock[0] += 2700
                expect(timeout_page.get_by_role('heading', name='Gracias, Prueba de vencimiento.')).to_be_visible(timeout=15000)
                expect(timeout_page.get_by_text('Se entregaron automáticamente tus respuestas', exact=False)).to_be_visible()
                assert not errors, errors
                assert not external, external
                browser.close()
                print('OK: navegador Chromium, flujo candidato/admin, guardado, desconexión, recarga, móvil, entrega, calificación, CSV y vencimiento. Sin errores JavaScript ni peticiones externas.')
        finally:
            stop.set()
            worker.join(3)
            server.close()


if __name__ == '__main__':
    main()
