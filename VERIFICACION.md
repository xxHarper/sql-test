# Verificación de SQL Assessment

Comprobación realizada el 20 de octubre de 2026 con Python 3.14, Flask 3.1.3, SQLite y Waitress 3.0.2 en el entorno Linux disponible.

## Pruebas automáticas del backend

Comando: `python -m unittest discover -s tests -v`.

**Resultado: 58 pruebas aprobadas** (25 de regresión de v1, 20 de importación/versionado, 5 de arranque y 8 específicas del examen v2).

Las pruebas de regresión sobre v1 (con archivos y bases temporales, sin activar v1 para nuevos candidatos) cubren:

- Creación automática e idempotente de las 12 preguntas, con 100 puntos en total.
- Páginas públicas y recursos estáticos locales.
- Autenticación y vencimiento de la sesión de administrador.
- Contraseña Unicode, hash seguro y restablecimiento con revocación de sesiones.
- Límites de intentos, CSRF y encabezados de seguridad.
- Aceptación de red privada y rechazo de IP públicas, incluso con encabezados de proxy falsificados.
- Registro sin iniciar el reloj, códigos consumidos una sola vez y dos registros concurrentes sobre un mismo código.
- Inicio idempotente y duración de 2,700 segundos desde el servidor.
- Guardado, revisiones para detectar conflictos entre pestañas y validación de entradas, incluido texto Unicode largo.
- Recuperación de respuestas y plazo tras recargar o reiniciar la aplicación.
- Aislamiento entre candidatos y ausencia de claves, guías y puntajes en las respuestas del portal.
- Entrega voluntaria definitiva y puntuación de opción múltiple.
- Rechazo de escrituras al vencer y conservación de la última respuesta guardada.
- Vencimiento por trabajador de segundo plano, sin solicitudes del candidato.
- Almacenamiento de SQL como texto y escape XSS en respuestas y nombres.
- Revisión parcial y completa, límites de puntuación y rechazo de NaN/infinito.
- Renderizado del panel, guía y revisión individual.
- Clasificación por umbrales y exportación CSV con UTF-8, campos multilínea y neutralización de fórmulas.

Las pruebas de integración de JSON/versionado cubren:

- Coincidencia exacta del documento oficial almacenado: enunciados, IDs `Q01`–`Q12`, opciones, puntuaciones, tablas y objetos `grading`.
- Visualización de las 12 preguntas y sus opciones; instrucciones del JSON antes del inicio.
- Máximo de 100 puntos, cinco calificaciones automáticas y siete manuales hasta completar 100/100.
- Ausencia de campos y valores privados en HTML/API/proyecciones públicas; los JSON privados no se sirven como archivos web.
- Reinicios idempotentes, sin duplicar filas ni cambiar resultados; el formato y el orden de propiedades no crean versiones nuevas.
- Cambios de preguntas, respuesta correcta, puntos y tablas crean versiones nuevas y conservan íntegros los intentos terminados, sus respuestas y calificaciones.
- Códigos pendientes e intentos activos siguen vinculados a la versión original y conservan su plazo y clave de corrección.
- JSON inválido no modifica SQLite; una instalación nueva con archivo inválido no crea la base.
- Validación del lote completo, IDs y propiedades duplicadas, sumas, rúbricas, opciones correctas, tipos, secciones, tablas, JSON mal formado y valores no finitos.
- Una excepción durante la importación revierte la transacción completa.
- Disparadores SQLite impiden sobrescribir versiones o cambiar el vínculo de un intento.
- Alta de otra evaluación y de una pregunta adicional desde JSON, con 13 preguntas y CSV adaptado, sin editar código.
- Calificación manual por tipo incluso cuando una abierta ocupa la primera posición.
- Los archivos JSON permanecen intactos después de guardar respuestas y calificar.
- Volver a contenido histórico reutiliza su versión sin eliminar las posteriores.
- Migración de una base con el esquema anterior real: conserva datos de intentos calificados, activos y códigos pendientes, además de notas, respuestas, comentarios y clave de sesión. Un intento activo de esa base puede seguir y entregarse con la clave de corrección original.

El archivo histórico `evaluaciones/historico/reactivos_sql_junior.json` es idéntico byte por byte al proporcionado en Descargas. SHA-256: `c816a800ded628ede4e5e11e907b8381e1a9c17a7404bb9a55a0daa486b58766`.

## Actualización oficial v2

La evaluación activa verificada en SQLite y en el banco de archivos es **`sql-migracion-junior-v2`**: **13 preguntas, 100 puntos y 50 minutos**. Es la única habilitada para códigos nuevos. `sql-migracion-junior-v1` permanece en SQLite, deshabilitada para nuevos códigos, y su JSON está archivado en `evaluaciones/historico/`. Los códigos emitidos anteriormente conservan la versión que tenían asignada.

El archivo v2 tiene un `exam_id` propio. Su revisión interna es **1 dentro de `sql-migracion-junior-v2`**, sin sobrescribir `sql-migracion-junior-v1`. El panel muestra ambas identidades de forma inequívoca. No se alteró el formato del JSON para forzar otra numeración.

Los ocho casos nuevos comprueban:

- Banco activo v2 y copia exacta del documento, sin IDs duplicados: distribución 20 + 40 + 30 + 10 = 100.
- Plazo de 3,000 segundos; Q13 con `type=sql_text`, el mismo editor y máximo de 10 puntos.
- Examen completo con 13 respuestas, cinco puntajes automáticos y ocho manuales; Q13 rechaza más de 10 puntos y es obligatoria para finalizar la revisión. Resultado de la prueba: 100/100 y CSV con Q13.
- Criterios y referencia exactos de Q13 disponibles para el administrador y ausentes en las respuestas públicas; acceso a los archivos JSON bloqueado desde HTTP.
- `CREATE TABLE`, `DROP TABLE` y texto XSS se almacenan como respuestas; no se ejecuta SQL del candidato y se escapa el HTML.
- Selección de v2 para códigos nuevos; ninguna opción permite emitir códigos de la v1 retirada.
- Reinicio sin duplicar las 13 preguntas ni alterar respuestas.
- Actualización sobre una base temporal con v1: comparación íntegra de preguntas, respuestas, comentarios, estados y calificaciones anteriores. Los códigos pendientes e intentos activos conservan 12 preguntas y 45 minutos; los nuevos reciben 13 preguntas y 50 minutos.

La copia v2 es idéntica byte por byte al archivo proporcionado. SHA-256: `dcdda714c5325271ded64c7635460d0db3e3814c513c2456f19743b05c4feb85`.

El importador y el editor existentes ya admiten este formato; no fue necesario cambiar sus reglas ni el esquema. Se comprobó además que todos los archivos de `sql_assessment/static/` (CSS, JavaScript e icono) son idénticos a la entrega anterior. Solo se actualizó el texto identificador de examen/revisión del panel para distinguir v1 y v2.

La carpeta local no tenía base de datos ni intentos previos al efectuar esta actualización. Se importaron v1 y v2 en orden y se consultó el catálogo real, con v2 habilitada y v1 conservada. La prueba de conservación de resultados se realizó además sobre una base temporal con intentos previos reales de prueba, sin añadir candidatos de demostración a la base local.

## Puerto y arranque

- Puerto predeterminado: **5002**, configurable mediante `PORT`. El argumento `--port` conserva prioridad sobre la variable.
- Cinco pruebas comprueban el puerto predeterminado, `PORT`, la prioridad del argumento, el rechazo de puertos inválidos antes de abrir la base y el archivo `iniciar.bat` con CRLF, valor predeterminado condicional y `--lan --abrir`.
- El comando de arranque imprime las direcciones del portal, del panel y de la LAN con el puerto efectivo. Con `--lan` utiliza `0.0.0.0`.
- Se ejecutó el recorrido de navegador descrito abajo con Waitress escuchando realmente en **`0.0.0.0:5002`** y Chromium accediendo a **`http://127.0.0.1:5002`**. Las peticiones del frontend utilizan el mismo origen del backend.
- No se modificaron otros servicios, sus puertos ni el firewall. La ejecución nativa del BAT sigue limitada por el entorno, como se indica al final de este documento.

## Navegador real

Comando: `python tests/browser_check.py`, con Playwright y Chromium instalados solo como herramientas de desarrollo.

**Resultado: recorrido completo aprobado con el examen oficial JSON v2**, sin errores JavaScript ni solicitudes externas. La prueba levanta Waitress en loopback con una base temporal y una contraseña aleatoria. Comprueba:

1. Inicio, autenticación del administrador y creación de códigos.
2. Registro de candidato, instrucciones e inicio.
3. Una sola pregunta visible, navegación y respuestas de opción múltiple.
4. Edición SQL, guardado y progreso.
5. Desconexión simulada y guardado automático al restablecer la conexión.
6. Recarga con recuperación de respuesta y pregunta activa.
7. Las 13 respuestas y vista móvil de 390 px sin desbordamiento horizontal.
8. Cancelación de la confirmación y entrega voluntaria.
9. Agradecimiento sin calificación del candidato.
10. Revisión de las ocho preguntas manuales, incluida Q13, comentarios, guardado parcial y finalización en 100/100.
11. Descarga CSV real desde el navegador.
12. Entrega automática visible al avanzar el reloj controlado del servidor hasta el vencimiento.
13. Ausencia de errores JavaScript y de solicitudes a servicios externos desde las páginas.

La navegación móvil mantiene la corrección realizada anteriormente. No se modificó el diseño en esta actualización. Las capturas quedan en `artifacts/`, incluida `pregunta-13.png`, al ejecutar la prueba.

## Paquetes offline

Los 13 wheels incluidos se validaron como archivos ZIP íntegros. Se incluye `wheelhouse/SHA256SUMS.txt` para verificar integridad. Se comprobó la resolución offline de dependencias para Windows x64 con CPython 3.10–3.14, sin acceder a índices de paquetes. Colorama, requerido por Click en Windows, también está incluido.

## Límites de la verificación

- El entorno no permitió ejecutar programas nativos de Windows. `iniciar.bat` está escrito para Windows con finales de línea CRLF, pero su ejecución nativa y el firewall deben comprobarse en el equipo de destino.
- La comprobación de navegador se realizó en Chromium; no se ejecutó una matriz de navegadores.
- No se probó una conexión desde otro dispositivo físico. Antes del primer examen real, comprueba la IP privada, el permiso del firewall y el acceso desde otro equipo de tu LAN.
- Las pruebas usan datos temporales. No hay contraseña preconfigurada ni evaluaciones de demostración en la base de producción.
