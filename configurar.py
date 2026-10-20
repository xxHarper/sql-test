"""Local console-only administrator setup and password reset."""
import argparse
import getpass
import secrets

from werkzeug.security import generate_password_hash
from sql_assessment import create_app
from sql_assessment.db import get_db


def configure(app, reset=False):
    with app.app_context():
        db = get_db()
        exists = db.execute("SELECT 1 FROM settings WHERE key='admin_password'").fetchone()
        if exists and not reset:
            return
        print('\nSQL Assessment - configuración local del administrador')
        print('Elige una contraseña de 12 a 256 caracteres. No se mostrará al escribir.\n')
        while True:
            try:
                password = getpass.getpass('Nueva contraseña: ')
                confirmation = getpass.getpass('Repite la contraseña: ')
            except (EOFError, KeyboardInterrupt):
                raise SystemExit('\nConfiguración cancelada. No se iniciará el servidor.')
            if not 12 <= len(password) <= 256 or password.isspace():
                print('Usa entre 12 y 256 caracteres, no solo espacios.\n')
                continue
            if not secrets.compare_digest(password.encode('utf-8'), confirmation.encode('utf-8')):
                print('Las contraseñas no coinciden.\n')
                continue
            with db:
                db.execute("INSERT INTO settings VALUES ('admin_password',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (generate_password_hash(password),))
                if reset:
                    # Rotating the signing key invalidates all existing sessions.
                    db.execute("UPDATE settings SET value=? WHERE key='secret_key'", (secrets.token_hex(32),))
                    db.execute("DELETE FROM rate_limits WHERE bucket LIKE 'admin:%'")
            print('\nContraseña guardada como hash seguro. Configuración completa.\n')
            return


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restablecer', action='store_true', help='Restablecer contraseña e invalidar sesiones; detener antes el servidor.')
    arguments = parser.parse_args()
    configure(create_app(), reset=arguments.restablecer)
