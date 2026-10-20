# SQL Assessment

Aplicación local para evaluar fundamentos de SQL y migración desde Excel en candidatos junior. Flask + SQLite + HTML/CSS/JavaScript, con todos los recursos en el proyecto. No usa servicios externos, CDN ni Docker.

## Inicio rápido en Windows

1. Instala **Python de 64 bits, versión 3.10 a 3.14**, si aún no lo tienes. Marca **Add python.exe to PATH**. El equipo debe tener Python antes de desconectarlo de internet; el intérprete no se incluye.
2. Conserva esta carpeta completa en un disco local del equipo servidor, incluida `wheelhouse/`. Evita OneDrive, carpetas de red y abrir dos copias del servidor sobre la misma base.
3. Haz doble clic en **`iniciar.bat`**. Crea `.venv`, instala los paquetes incluidos **sin conectarse a internet** y prepara automáticamente la base de datos.
4. En el primer arranque, escribe y confirma una contraseña de administrador de **12 a 256 caracteres**. No se muestra mientras escribes; se almacena como hash scrypt, sin contraseña predeterminada.
5. Se abre el portal en **http://127.0.0.1:5002**. El panel está en **http://127.0.0.1:5002/admin**.
6. Deja abierta la ventana del servidor durante las evaluaciones. Para detenerlo, presiona **Ctrl+C**.

El navegador debe ser una versión reciente de Edge, Chrome o Firefox, con JavaScript y cookies habilitados. La interfaz usa fuentes del sistema.

### Acceso por red local

`iniciar.bat` activa la red privada. En otro dispositivo conectado a la misma red, abre `http://IP_DEL_SERVIDOR:5002`. El servidor muestra las direcciones detectadas; también puedes consultar la IPv4 con `ipconfig` en Windows.

Si Windows solicita acceso de red para Python, habilita **solo redes privadas**, no redes públicas. Si la política de tu organización bloquea conexiones entrantes, pide al responsable permitir TCP 5002 en el perfil privado, restringido a la subred local. No se modifica automáticamente el firewall.

No configures reenvío de puertos en el router ni túneles o proxies públicos. El servidor rechaza IP de origen públicas y no confía en encabezados de proxy. El modo LAN escucha en `0.0.0.0`, pero únicamente acepta loopback, RFC1918 e IPv6 privadas/locales. Usa una LAN de confianza: HTTP local no cifra el tráfico. El administrador debe acceder preferentemente desde el propio servidor.

Para usarlo únicamente desde el equipo servidor, abre una terminal en la carpeta y ejecuta:

```bat
.venv\Scripts\python.exe run.py --abrir
```

El puerto se obtiene de la variable de entorno **`PORT`**, con **5002** como valor predeterminado. `iniciar.bat` respeta un `PORT` ya configurado y mantiene la escucha LAN en `0.0.0.0`. No se modifica la configuración de otros servicios. No hace falta un archivo `.env`.

Para usar otro puerto libre, en Símbolo del sistema, desde esta carpeta:

```bat
set "PORT=5003"
iniciar.bat
```

O en PowerShell:

```powershell
$env:PORT = "5003"
.\iniciar.bat
```

Para esa ejecución, abre `http://IP_DEL_SERVIDOR:5003`. Al dejar `PORT` sin definir se vuelve a 5002. También se conserva la opción explícita `--port`, que tiene prioridad sobre `PORT`:

```bat
.venv\Scripts\python.exe run.py --lan --port 5003 --abrir
```

El servidor imprime el puerto efectivo del portal, del panel y de las direcciones LAN, y abre el navegador con ese mismo puerto. El frontend utiliza rutas relativas para acceder al backend, por lo que sigue el puerto del servidor automáticamente.

## Aplicar una evaluación

1. Entra al panel y genera entre 1 y 25 códigos. Cada fila representa una evaluación independiente y permanece en el historial.
2. Copia un código por candidato y comparte la dirección del portal. No compartas la contraseña del administrador.
3. El candidato ingresa su nombre completo y código. El código se consume al registrar el acceso, **antes** de comenzar el examen; no podrá registrarse otra persona con él. El estado seguirá siendo «No iniciado», con la indicación «Acceso registrado», hasta que comience.
4. El candidato lee las instrucciones y presiona **Comenzar evaluación**. En ese momento inicia el plazo de 45 minutos del servidor.
5. Responde una pregunta a la vez; puede navegar por todas, revisar su progreso y entregar con confirmación. El texto SQL es solo una respuesta: **nunca se ejecuta**.
6. Tras la entrega aparece un agradecimiento y la referencia. No se publican las soluciones, comentarios del evaluador ni la calificación para el candidato.

### Guardado, recargas y desconexiones

- Cada edición se guarda después de 500 ms sin escribir; navegar también solicita guardado. Espera el indicador **Todos los cambios guardados** antes de cerrar o recargar.
- Las respuestas confirmadas están en SQLite. Una cookie firmada identifica la evaluación y permite recuperarla al recargar o reabrir el mismo navegador; dura hasta 12 horas. No borres cookies, no cambies de navegador ni uses pestañas simultáneas.
- Un borrador en `sessionStorage` ayuda a conservar cambios pendientes dentro de esa pestaña ante una recarga o fallo breve de red. Solo se recupera si coincide con la revisión del servidor. No sustituye el guardado confirmado.
- Ante una caída de red se informa el fallo y se reintenta. El plazo sigue corriendo; al vencer se entregan **las respuestas que llegaron al servidor**. No se aceptan escrituras tardías ni se suman minutos por desconexión.
- La entrega voluntaria envía la última versión y verifica que se haya guardado antes de confirmar.
- El reloj de JavaScript es informativo. Cada escritura verifica el plazo en el servidor dentro de una transacción. Un trabajador revisa vencimientos cada segundo incluso si ningún candidato mantiene una página abierta.
- Si se apaga el servidor, las respuestas confirmadas permanecen en SQLite. Al reiniciar se conserva el plazo original y las evaluaciones con plazo vencido se cierran al ser procesadas. No se pausa el examen; mantén el equipo encendido y con su reloj correcto.
- Si se pierden las cookies de una evaluación, **el código consumido no se puede reutilizar**. El administrador puede emitir una evaluación nueva; el registro anterior se conserva. No hay función pública para recuperar una sesión con solo saber el código.

## Evaluación y criterios

| Sección | Preguntas | Puntos |
| --- | --- | --- |
| Conocimientos generales | 1–5, cuatro opciones | 20 |
| SQL práctico | 6–9, revisión manual | 40 |
| Migración y bases de datos | 10–12, abiertas | 40 |
| **Total** | **12** | **100** |

Las cinco preguntas de opción múltiple se califican automáticamente al entregar. Las siete restantes requieren revisión manual. En **Ver detalle / Revisar** están la respuesta del candidato, la guía, los criterios y un comentario opcional para cada pregunta. También existe una guía general protegida en `/admin/guia`.

**Guardar revisión** permite trabajo parcial; **Finalizar calificación** exige puntajes para las preguntas 6–12 y cambia el estado a «Calificado». Los puntajes admiten decimales en la interfaz. Cada pregunta está limitada a su máximo, el total no puede superar 100. Puedes corregir una revisión; si borras un puntaje de un examen calificado y guardas, vuelve a pendiente de calificar. Los resultados parciales se etiquetan como tales y no se exportan como calificación final.

| Calificación orientativa | Clasificación |
| --- | --- |
| 85–100 | Muy buen candidato |
| 70–84 | Buen candidato |
| 55–69 | Con potencial |
| 0–54 | Requiere reforzar conocimientos |

Con decimales se aplican los umbrales ≥85, ≥70 y ≥55, sin redondear antes de clasificar. La orientación apoya la entrevista y no sustituye la decisión del evaluador.

### Estados

- **No iniciado:** código creado o candidato registrado en instrucciones.
- **En proceso:** plazo de 45 minutos iniciado.
- **Entregado:** entrega voluntaria pendiente de calificar.
- **Vencido:** entrega automática por tiempo, pendiente de calificar.
- **Calificado:** revisión manual finalizada, con nota sobre 100.

El historial registra creación, acceso, inicio, entrega y guardados/finalización de revisión. Las fechas se muestran y exportan en **UTC**, con etiqueta explícita. La duración se expresa en minutos y segundos.

**Exportar CSV** descarga todas las evaluaciones, estados, fechas, tiempo utilizado, calificación final cuando exista, respuestas, puntajes y comentarios. El CSV tiene BOM UTF-8 para Excel, comillas para campos multilínea y neutralización de fórmulas en entradas que comienzan con `=`, `+`, `-` o `@`. Las respuestas de opción múltiple se exportan como texto de la opción elegida.

## Contraseña y respaldo

Para restablecer la contraseña, **detén el servidor**, abre una terminal local y ejecuta:

```bat
.venv\Scripts\python.exe configurar.py --restablecer
```

Este comando cambia la clave que firma las sesiones, por lo que invalida **todas las sesiones**, incluidas las de candidatos. Hazlo fuera de una evaluación activa. Reinicia después con `iniciar.bat`. Quien tenga acceso a los archivos del servidor tiene acceso privilegiado: protege la carpeta con permisos de Windows y usa una contraseña que no utilices en otros servicios.

Los datos están en `instance/assessment.sqlite3`. Para respaldar, **detén el servidor y copia toda la carpeta `instance/`** a un lugar seguro. Para restaurar, detén el servidor y repón esa carpeta. La base contiene contraseñas hasheadas, clave de sesión, datos personales, guías, respuestas e historial; no la compartas con candidatos ni la subas a repositorios. Los archivos `-wal` y `-shm`, si existen, forman parte de SQLite; no los borres con el servidor activo. No ejecutes la base desde una carpeta compartida en red.

## Estructura

```text
SQL Assessment/
├── iniciar.bat             # Instalación offline y arranque para Windows
├── run.py                  # Servidor Waitress y trabajador de vencimientos
├── configurar.py           # Contraseña inicial y restablecimiento local
├── importar_evaluaciones.py # Validación/importación por consola
├── evaluaciones/           # Archivos JSON privados, fuente de verdad
│   └── reactivos_sql_junior.json
├── requirements.txt        # Dependencias fijadas
├── wheelhouse/             # Paquetes offline para Windows x64, Python 3.10–3.14
├── sql_assessment/
│   ├── __init__.py         # Fábrica Flask y protecciones comunes
│   ├── db.py               # Esquema, conexiones, entrega y puntuación automática
│   ├── exams.py            # Validación, importación y versiones JSON
│   ├── legacy.py           # Migración de bases anteriores a JSON
│   ├── candidate.py        # Acceso, examen y API de respuestas
│   ├── admin.py            # Autenticación, panel, calificación y CSV
│   ├── templates/          # Plantillas Jinja con escape automático
│   └── static/             # CSS, JavaScript e icono locales
├── tests/                  # Regresión, importación, versiones y navegador
├── instance/              # Se crea automáticamente; no distribuir con datos reales
└── README.md
```

## Exámenes desde JSON: importar y versionar

La fuente de verdad del examen oficial es **`evaluaciones/reactivos_sql_junior.json`**, copiado íntegramente del archivo proporcionado. No se usa un banco de preguntas dentro del código. El archivo es privado del servidor; no está en `static/` ni tiene una ruta de descarga pública.

Al arrancar, la aplicación lee todos los archivos `*.json` de `evaluaciones/` y valida **el lote completo antes de abrir SQLite**. Si alguno es inválido, informa archivo, campo y problema y cancela el arranque sin crear ni modificar la base. También rechaza propiedades JSON duplicadas, IDs de reactivo/opción/sección duplicados, referencias inválidas, tipos desconocidos, modos de calificación incorrectos y tablas con filas de anchura incorrecta.

Cada archivo debe usar la estructura real del oficial: `schema_version`, `exam_id`, `title`, `description`, `duration_minutes`, `max_score`, `instructions`, `sections` y `questions`. `internal_notes` es opcional y privado. Las secciones contienen `id`, `title`, `points`. Cada pregunta contiene `id`, `section`, `type`, `points`, `prompt`, `grading`, y opcionalmente `tables` y `title`. `options` se requiere para `multiple_choice`.

- `multiple_choice`: opciones con `id` y `text`; `grading.correct_option` identifica la correcta. Calificación automática al entregar.
- `sql_text` y `open_text`: `grading.mode` debe ser `manual`; `reference_answer`, `rubric` (lista de `criterion` y `points`) y `notes` opcional son solo para el evaluador.
- `tables`: lista de objetos con `name`, `columns` y `rows`. Se conservan nombres, textos, valores, orden y tipos del JSON, incluidos ceros iniciales de IDs almacenados como texto, ceros numéricos y correos vacíos.
- `max_score` debe ser **100** y la suma de preguntas debe ser **100**. Cada sección debe coincidir con la suma de sus preguntas y cada rúbrica con el máximo de su reactivo. Los puntos deben ser positivos y finitos.
- El examen oficial tiene 12 preguntas, cinco automáticas y siete manuales. Se admite agregar preguntas en futuros archivos (hasta 200), manteniendo el total de 100; la interfaz, el guardado, la revisión y el CSV ajustan sus conteos automáticamente.

### Cambiar el examen o agregar otro sin editar Python

1. Detén el servidor y respalda `instance/` antes de actualizar el proyecto o sus exámenes.
2. Para revisar el mismo examen, edita su JSON y conserva `exam_id`, incluso aunque el ID oficial termine en `-v1`. El número de revisión lo administra SQLite. `schema_version: 1` identifica el formato del archivo, no la revisión del examen.
3. Para agregar otra evaluación, coloca otro JSON de la misma estructura con un **`exam_id` distinto** en `evaluaciones/`. Mantén solo un archivo vigente por `exam_id`; las versiones históricas se conservan en SQLite y no deben duplicarse como archivos dentro de esa carpeta.
4. Valida los archivos sin acceder a la base:

```bat
.venv\Scripts\python.exe importar_evaluaciones.py --validar
```

5. Si la validación es correcta, ejecuta `iniciar.bat`. La importación es automática. También puedes importar explícitamente desde la consola con `.venv\Scripts\python.exe importar_evaluaciones.py`, con el servidor detenido.
6. En el panel, selecciona la evaluación y su versión vigente antes de generar códigos. La guía permite consultar todas las versiones, incluidas las anteriores.

Una huella SHA-256 del contenido JSON canónico evita duplicados. Cambiar solo espacios, indentación u orden de propiedades no crea otra versión. Cambiar contenido crea una versión nueva e inmutable; no sobrescribe las anteriores, incluso si aún no tienen candidatos. Volver exactamente a un contenido histórico reutiliza esa versión.

**Cada código queda vinculado a su versión al crearse**, incluidos los que todavía no se han utilizado. Cambiar el JSON afecta a los códigos nuevos, no a los ya emitidos. Los intentos activos conservan sus preguntas, opciones, tablas, duración, respuestas correctas y rúbricas. Las respuestas, comentarios, entregas y notas anteriores permanecen en SQLite; nunca se escriben en los JSON. El CSV identifica `exam_id`, versión e ID original de cada pregunta.

Quitar un archivo de la carpeta lo retira de la selección para códigos nuevos al reiniciar; no borra sus versiones ni sus resultados. Debe permanecer al menos un archivo válido. Si necesitas reemplazar preguntas para un candidato con un código ya emitido, genera un código nuevo de la versión nueva.

### Actualizar una base creada antes de JSON

La primera inicialización migra el esquema automáticamente y, dentro de una transacción, conserva el examen anterior como **SQL Assessment · examen anterior** (`legacy-sql-assessment`). Preserva los IDs internos de preguntas y respuestas, opciones elegidas, puntajes, comentarios, estados, tiempos y clave de sesión. También conserva los códigos pendientes y los intentos activos con sus preguntas anteriores. Después importa el oficial como un examen independiente para códigos nuevos. No borres la base ni reemplaces la carpeta `instance/` al actualizar el proyecto.

`legacy.py` solo recupera las tablas de ejemplo que la versión inicial guardaba en Python y que aún no estaban en SQLite. No se utiliza para definir exámenes nuevos. Las preguntas y guías de la versión histórica se recuperan de la base anterior.

## Pruebas

Desde Windows, después de instalar:

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Las pruebas usan bases temporales y comprueban las 12 preguntas y 100 puntos, acceso y límites de intentos, aislamiento, CSRF, XSS, bloqueo de SQL, temporizador, vencimiento sin navegador, reinicio y recarga, guardado y conflictos entre pestañas, entrega definitiva, revisión manual, protección de guías y exportación CSV. No se crean credenciales de prueba en la base real.

La comprobación opcional de navegador está en `tests/browser_check.py`. Usa un servidor Waitress real y una base temporal, verifica escritorio y móvil, desconexión y recuperación, entrega voluntaria, vencimiento, revisión y descarga CSV. Genera capturas en `artifacts/`. Para prepararla en un equipo de desarrollo con internet (no se requiere para operar la aplicación):

```bat
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe tests\browser_check.py
```

Consulta también `VERIFICACION.md` para los resultados y los límites de la comprobación realizada.

Para una comprobación de operación en tu red, usa un código de prueba, contesta varias preguntas, recarga, entrega, califica y exporta desde el panel. Verifica al menos otro dispositivo antes de aplicar exámenes reales: las políticas del firewall y del router dependen de cada red.

## Desarrollo y dependencias

No hay compilación frontend. Solo dos dependencias directas: Flask y Waitress; el resto son sus dependencias fijadas en `requirements.txt`. Los wheels incluyen sus licencias en los metadatos. `wheelhouse/` incluye binarios de MarkupSafe para CPython 3.10–3.14 Windows x64 y paquetes universales. No soporta instalación offline en Windows ARM, Python 32 bits ni versiones futuras sin preparar los paquetes correspondientes.

En un equipo Windows **con internet**, para preparar otro conjunto de paquetes desde el Python que se usará en destino:

```bat
python -m pip download -r requirements.txt --dest wheelhouse --only-binary=:all:
```

Para desarrollo en Linux o macOS (la primera instalación requiere internet o wheels para ese sistema):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
.venv/bin/python -m unittest discover -s tests -v
```

No uses `flask run --debug` para aplicar evaluaciones. `run.py` utiliza Waitress sin depuración, con conexiones SQLite por solicitud, claves foráneas, WAL y transacciones de escritura. Las entradas se almacenan con parámetros; ningún endpoint ejecuta el SQL de las respuestas.

Las protecciones de cookies, escape y encabezados se basan en la [documentación de seguridad de Flask](https://flask.palletsprojects.com/en/stable/web-security/); el servidor usa la [configuración documentada de Waitress](https://docs.pylonsproject.org/projects/waitress/en/stable/arguments.html). Estos enlaces son solo documentación: la aplicación no los consulta durante instalación offline ni operación.

## Solución de problemas

- **Puerto ocupado:** elige otro puerto libre para SQL Assessment mediante `PORT` o `--port`. Conserva las otras aplicaciones en sus puertos actuales. `PORT` debe ser un entero entre 1024 y 65535.
- **Python no encontrado:** instala Python x64 con PATH; cierra y vuelve a abrir la terminal.
- **Entorno de otro sistema operativo:** si moviste el proyecto desde Linux, elimina solo `.venv/` y ejecuta `iniciar.bat` para recrearlo en Windows. No borres `instance/`.
- **No se puede instalar offline:** verifica Python x64 3.10–3.14 y la carpeta `wheelhouse/` completa. No hace falta actualizar pip para usar estos paquetes.
- **No accede otro equipo:** comprueba que ambos dispositivos estén en la misma red privada, que el servidor siga abierto y que el firewall permita el puerto en esa subred. Las redes de invitados pueden aislar dispositivos.
- **Código consumido:** continúa desde el navegador original; si perdiste la sesión, pide un código nuevo al administrador.
- **Formulario caducado:** recarga antes de reintentar. La sesión de administrador dura 8 horas y tiene un token CSRF.
- **Demasiados intentos:** espera 5 minutos; el límite se almacena en SQLite y permanece al reiniciar.
