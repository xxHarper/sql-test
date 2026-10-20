"""Waitress server, compatible with Windows and Linux. Never runs Flask debug."""
import argparse
import ipaddress
import logging
import os
import socket
import threading
import webbrowser

from waitress import serve
from sql_assessment import create_app
from sql_assessment.db import expire_attempts


def deadline_worker(app, stop):
    while not stop.wait(1):
        try:
            with app.app_context():
                expire_attempts()
        except Exception:
            logging.exception('No se pudo actualizar el vencimiento; se reintentará.')


def main():
    parser = argparse.ArgumentParser(description='SQL Assessment - servidor local')
    parser.add_argument('--lan', action='store_true', help='Permitir red privada; por defecto solo localhost.')
    parser.add_argument('--port', type=int, default=os.environ.get('PORT', '5002'),
                        help='Puerto de escucha; prioridad: --port, variable PORT, 5002.')
    parser.add_argument('--abrir', action='store_true', help='Abrir el portal en el navegador.')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('El puerto debe estar entre 1024 y 65535.')
    from sql_assessment.exams import ExamValidationError
    try:
        app = create_app()
    except ExamValidationError as error:
        raise SystemExit(str(error))
    for result in app.extensions['exam_imports']:
        print(f'Examen: {result["exam_id"]} · versión {result["version"]} · {"importada" if result["created"] else "sin cambios"}', flush=True)
    from configurar import configure
    configure(app)
    url = f'http://127.0.0.1:{args.port}'
    print(f'\nSQL Assessment\nPortal: {url}\nAdministrador: {url}/admin', flush=True)
    if args.lan:
        try:
            addresses = {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
            for address in sorted(addresses):
                if ipaddress.ip_address(address).is_private and not ipaddress.ip_address(address).is_loopback:
                    print(f'Red local: http://{address}:{args.port}', flush=True)
        except OSError:
            pass
        print('Acceso limitado a direcciones privadas. Usa solo una red de confianza. No abras puertos en el router.', flush=True)
    print('Mantén esta ventana abierta. Para detener: Ctrl+C.\n', flush=True)
    stop = threading.Event()
    worker = threading.Thread(target=deadline_worker, args=(app, stop), daemon=True)
    worker.start()
    if args.abrir:
        opener = threading.Timer(1.5, webbrowser.open, args=(url,))
        opener.daemon = True
        opener.start()
    try:
        serve(app, host='0.0.0.0' if args.lan else '127.0.0.1', port=args.port, threads=8, clear_untrusted_proxy_headers=True)
    finally:
        stop.set()


if __name__ == '__main__':
    main()
