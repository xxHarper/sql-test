"""Port configuration and the Windows launcher's server entry point."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run
from sql_assessment import create_app


class StartupTests(unittest.TestCase):
    def invoke(self, port=None, arguments=None):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({'TESTING':True, 'DATABASE':str(Path(directory)/'startup.sqlite3')})
            environment = {} if port is None else {'PORT':port}
            output = io.StringIO()
            with patch.dict(os.environ, environment, clear=True), \
                 patch('sys.argv', ['run.py', '--lan', *(arguments or [])]), \
                 patch('run.create_app', return_value=app), \
                 patch('configurar.configure'), patch('run.serve') as serve, \
                 patch('run.socket.getaddrinfo', return_value=[(None,None,None,None,('192.168.1.10',0))]), \
                 contextlib.redirect_stdout(output):
                run.main()
            return serve.call_args.kwargs, output.getvalue()

    def test_default_port_and_lan_addresses(self):
        settings, output = self.invoke()
        self.assertEqual(settings['host'], '0.0.0.0')
        self.assertEqual(settings['port'], 5002)
        self.assertIn('Portal: http://127.0.0.1:5002', output)
        self.assertIn('Administrador: http://127.0.0.1:5002/admin', output)
        self.assertIn('Red local: http://192.168.1.10:5002', output)

    def test_environment_port(self):
        settings, output = self.invoke('5012')
        self.assertEqual(settings['port'],5012)
        self.assertIn('http://192.168.1.10:5012',output)

    def test_explicit_argument_overrides_environment(self):
        settings, output = self.invoke('5012',['--port','5013'])
        self.assertEqual(settings['port'],5013)
        self.assertIn('http://127.0.0.1:5013',output)

    def test_invalid_ports_stop_before_database_initialization(self):
        for port in ['', 'abc', '1023', '65536']:
            with self.subTest(port=port), patch.dict(os.environ,{'PORT':port},clear=True), \
                 patch('sys.argv',['run.py','--lan']), patch('run.create_app') as factory, \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                try:
                    run.main()
                finally:
                    factory.assert_not_called()
            self.assertEqual(error.exception.code,2)

    def test_windows_launcher_keeps_lan_and_respects_environment(self):
        source = (Path(__file__).resolve().parents[1]/'iniciar.bat').read_bytes()
        self.assertNotIn(b'\n',source.replace(b'\r\n',b''))
        self.assertIn(b'if not defined PORT set "PORT=5002"',source)
        self.assertIn(b'run.py --lan --abrir',source)
        self.assertNotIn(b'--port ',source.split(b':arrancar')[1].split(b':errorpython')[0])


if __name__ == '__main__':
    unittest.main()
