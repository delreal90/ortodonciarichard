# Ortodoncia Richard — Resumen del proyecto (LÉEME PRIMERO)

Mapa general para retomar o construir algo NUEVO relacionado, sin leer todo el
`CLAUDE.md` (que tiene el detalle fino de cada sistema y bug). Cuando necesites
profundizar en un sistema, salta a su sección en `CLAUDE.md`.

## Qué es
Clínica de ortodoncia en Las Condes, Santiago. El proyecto tiene 4 piezas:
1. **Sitio web** estático (HTML/CSS/JS puro) — GitHub Pages, dominio
   `ortodonciarichard.cl` (DNS Cloudflare). Repo `github.com/delreal90/ortodonciarichard`.
   Optimizado para SEO y búsqueda por IA (2026-07-24): schema.org JSON-LD
   (Dentist + 4 doctores como Physician, con credenciales y N° de registro de la
   Superintendencia de Salud), meta OG/canonical/geo, `robots.txt` (permite crawlers
   de IA: GPTBot/ClaudeBot/PerplexityBot/etc.) y `sitemap.xml`.
2. **Backend Flask** (`admin/`) — en **Render** (plan Starter, siempre activo).
   **Deploy = `git push`** (Render redespliega solo desde el mismo repo).
3. **Extensión F2** (`dentidesk-assistant/`, Manifest V3) — asistente que la
   secretaria invoca con F2 sobre `app.dentidesk.cl`. Proyecto **separado, pensado
   para comercializarse**; carpeta compartida entre los PC de la clínica.
4. **Sub-apps** servidas por el mismo backend: panel admin, agendamiento online,
   consentimientos, seguros complementarios, compras/stock.

## Infra y convenciones (importantes)
- **Deploy backend:** `cd ortodonciarichard && git add . && git commit && git push`.
- 🔒 **El repo es PÚBLICO** (sirve el sitio por GitHub Pages). Ningún dato personal puede
  quedar versionado: exports de pacientes, fechas de nacimiento, credenciales y tokens van
  siempre a `.gitignore` + disco persistente. Revisar `git status` antes de un `git add .`.
- **Secretos:** SIEMPRE como env vars en Render (`DENTIDESK_*`, `ADMIN_TOKEN`,
  `KIOSK_TOKEN`, `WA_TOKEN`/`WA_*`, `SMTP_USER`/`SMTP_PASS`, `CONSENT_SECRET`,
  `SEGUROS_SECRET`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `PRINT_TOKEN`, `COMPRAS_SEED_*`).
  En local viven en `admin/scheduling_secrets.json` (gitignored). Nunca en git.
- **Persistencia:** JSON en disco persistente de Render, rutas derivadas de
  `PATIENT_INDEX_PATH` (patrón: cada módulo define sus paths con `os.environ.get`).
  **Excepción:** Compras usa SQLite. Los `*.json` de runtime están gitignored.
- **Auth del backend:** header `X-Admin-Token` vs env `ADMIN_TOKEN`
  (`_check_admin_token()` en server.py; sin token seteado = dev local permite todo).
  Hay tokens de menor alcance: `KIOSK_TOKEN` (tablet), `PRINT_TOKEN` (agente de
  impresión), y Compras tiene su propio login por usuario/rol (`X-Compras-Token`).
- 🕐 **Zona horaria — regla dura:** Render corre en **UTC**, 3-4 h ADELANTE de Chile.
  **NUNCA usar `datetime.now()` ni `date.today()`.** Siempre `fechas.ahora_chile()` /
  `fechas.hoy_chile()` (`admin/fechas.py`, único lugar; zoneinfo `America/Santiago`,
  paquete `tzdata` en requirements). Había 4 copias del helper y a `scheduling.py` le
  faltaba: la agenda online **le escondía horas válidas al paciente** todos los días.
  Los módulos con nombre propio (`consentimientos.ahora_chile`, `seguros.ahora_chile`,
  `stats._ahora_cl`, `compras.ahora_cl`, `cumpleanos.ahora_chile`) ahora delegan en él.
- 🧪 **Pruebas:** `cd admin && python test_todo.py` → 162 pruebas, 8 suites, **cero red,
  cero correo, cero WhatsApp, cero DentiDesk**. Se puede correr en cualquier momento, aun
  con producción andando. Correrlas antes de cada push. Cubren: hora de Chile, cobertura
  de auth de las 162 rutas, el webhook que cancela citas, las guardas de los 3 sistemas de
  avisos, compras (recurrentes/stock/migraciones), cumpleaños y el registro de reservas.
- 🔑 **Auth — no se olvide:** el control de acceso se escribe a mano en cada handler
  (121 copias de `if not _check_admin_token()`). `test_seguridad.py` recorre TODAS las
  rutas y **falla si agregas una sin llave** y sin declararla pública con su razón. Si
  falla, no agregues tu ruta a la lista sin pensarlo antes.
- 🚫 **Rutas de administración:** van al set `RUTAS_SOLO_LOCAL` de `server.py`, **nunca**
  un `if EN_RENDER` suelto dentro de la función. Tener dos mecanismos fue lo que dejó
  `/api/upload` abierto en producción.
- **Paciente de prueba:** Alberto Del Real — RUT `&lt;RUT_PACIENTE_PRUEBA&gt;`,
  `&lt;EMAIL_PACIENTE_PRUEBA&gt;`, celular `&lt;CELULAR_PACIENTE_PRUEBA&gt;`. Autorizado crear
  citas de prueba. ⚠️ NUNCA escribir a `&lt;CELULAR_TERCERO_NO_ESCRIBIR&gt;` (difiere en un
  dígito del anterior y es de un tercero real).
- 🔑 **Los valores reales de esos marcadores `&lt;ASI&gt;` viven en `DATOS-PRIVADOS.md`**
  (gitignored, en la raíz del proyecto). Este archivo y `CLAUDE.md` son PÚBLICOS —
  no volver a escribir en ellos un RUT, celular, email o ID de Meta/Drive.
- Sin frameworks front (HTML/CSS/JS vanilla, intencional). Python 3.11.

## DentiDesk — hechos duros (la clave de casi todo)
`app.dentidesk.cl`. **La API oficial solo tiene 6 endpoints** (authentication,
getAgendaDay, updateAgenda, getAgendaStatus, createAgenda, getAvailableHours) y
**NO expone**: búsqueda de paciente por RUT, ficha clínica, evoluciones, ni boletas.
- **JWT de un solo uso** (cada consulta = 2 round-trips; no cachear el token).
- **Dedup de pacientes por RUT+EMAIL** (email que no coincide → ficha duplicada).
  Por eso existe la base local `pacientes.py` (`patient_index.json`).
- IDs reales: **IdLocation 408**; profesionales **Octavio 9412 / Rodrigo 8452 /
  Alberto 639 / Patricio 9308**. IdStatus: 2120 nueva, 40968 confirmado-semana-WA,
  32180 confirmado-WA, 2122 cancelado, 2132 reagendado.
- `updateAgenda` SOLO cambia el IdStatus (no mueve hora ni duración — verificado).
- `getAgendaDay` no trae bloqueos/feriados; `getAvailableHours` sí (401 = día sin horas).
- **Lo que NO está en la API se lee scrapeando la web con la SESIÓN del navegador**
  (desde la extensión / fetch same-origin): evoluciones en `historial.php?id_paciente=`,
  boletas DTE en `POST ajax/ajaxConfigIntegracionSii.php` (`accion=sii_consultar_dtes_emitidos&mes=N`).
  El login tiene reCAPTCHA → no se puede automatizar login server-side.

## Revisión y limpieza (2026-07-25)

Auditoría completa del proyecto tras 3 meses de crecer copiando y pegando. Lo que se
arregló y **no hay que volver a romper**:

- **`/api/upload` estaba abierto en Render**: sin token, sin bloqueo solo-local y sin
  sanear el nombre (un `../` escribía fuera de `images/`). Cerrado + prueba de regresión.
- **Datos personales en el repo público**: RUT, celular y email salieron de `CLAUDE.md`
  y `RESUMEN-PROYECTO.md`; ahora usan marcadores `<ASI>` y los valores reales viven en
  **`DATOS-PRIVADOS.md`** (gitignored, en la raíz).
- **XSS en la pestaña Equipo del panel**: el nombre se interpolaba crudo en `onclick`.
  Ahora todo pasa por `_esc()` y los botones referencian la POSICIÓN, no el nombre.
- **Huso horario**: ver la regla dura más arriba. Era el bug más caro del proyecto.
- **`stats.py` perdía reservas**: único módulo de persistencia sin lock; su `eliminar()`
  hacía read-modify-write sin escritura atómica.
- **Excepciones tragadas** en 8 sitios, la peor: si fallaba `marcar_enviada` el barrido
  le reenviaba la confirmación a un paciente que ya la tenía, sin dejar rastro.
- **Registros que crecían para siempre** (confirmaciones, recordatorios, recaptación):
  ahora podan. ⚠️ De cada RUT se conserva SIEMPRE el último envío (la guarda
  `enviado_reciente` se calcula sobre él) y un programado `pendiente` no se poda nunca.
- **Sitio de 43,6 MB a 23,8 MB**, carga inicial 78 KB: imágenes recomprimidas (el poster
  del hero pesaba 5 MB), `preload="metadata"` en el video (11,9 MB que se bajaban solos)
  y `loading="lazy"` en 17 imágenes. Nueva variable `--gold-text` para texto sobre fondo
  claro: el dorado de marca da 2,29:1 de contraste y falla WCAG AA.
- **Panel**: la pestaña WhatsApp usaba `wa_token`/`wa_url` propios en vez de las claves
  compartidas `stats_token`/`stats_url` — el token puesto en Estadísticas no se propagaba.

**Deuda de mantenimiento — saldada (2026-07-28):**
- 9 copias del guardado JSON → `jsonstore.py`.
- Andamiaje triplicado de los 3 sistemas de avisos → `avisos.py`. Habían divergido:
  a `control_dental` le faltaban `lista_no_molestar()` y `en_no_molestar()`, así que
  `server.py` leía su registro a mano.
- 5 copias del sobre de los correos → `notify._email_layout()`. Verificado comparando
  el HTML generado antes y después: **idéntico carácter por carácter** en las 6 variantes.
  ⚠️ Los estilos van EN LÍNEA y la maquetación es con `<table>` anidadas a propósito: es
  lo único que Gmail y Outlook renderizan igual. No "modernizar" a CSS externo.
- El patrón remoto de las 6 pestañas del panel → `remotoUrl/Token/Headers/Fetch/Init`.
  Nota honesta: acá el conteo de líneas casi no bajó (la duplicación eran fragmentos de
  1-3 líneas repartidos, no bloques). El "~70% del JS duplicado" de la auditoría inicial
  estaba sobrestimado; el valor real es que la lógica vive en un solo lugar.

Hueco preexistente que NO se tocó: `_segFetch`/`_cdFetch`/`_satFetch` del panel no revisan
403 ni error de red, a diferencia de las otras tres pestañas.

## Backend: módulos (`admin/`)
- `fechas.py` — **hora de Chile, único lugar**. `ahora_chile()` / `hoy_chile()` /
  `ahora_chile_aware()`. Todo lo demás delega acá (ver la regla dura arriba).
- `jsonstore.py` — **el guardado de datos, único lugar**. `JsonStore(path, default,
  indent, claves, default_si_falta)` con escritura atómica, lock propio (RLock) y
  `actualizar(fn)` para el read-modify-write indivisible. Lo usan los 9 módulos que
  antes reimplementaban `_load`/`_save`. ⚠️ Si un archivo se corrompe, **NO se pisa**:
  se aparta como `.corrupto-<n>` y queda aviso en el log. Excepción: `stats.py` no lo
  usa porque sus archivos son JSONL (una línea por reserva, con append constante).
- `avisos.py` — lo que comparten recaptación / control dental / NPS: `rut_key()`,
  `ListaNoMolestar`, `bloqueo()` y `primera_guarda()`. **`no_molestar` se evalúa
  siempre primero y nunca es forzable** — es el opt-out del paciente, ningún override
  del F2 lo salta. Los opt-out son independientes entre sistemas a propósito.
- `test_todo.py` — corre las 7 suites de pruebas. `test_fechas`, `test_seguridad`,
  `test_stats`, `test_cumpleanos`, `test_webhook_wa`, `test_avisos`, `test_compras`.
- `server.py` — todas las rutas Flask + schedulers en hilos (`_loop_*`). ~5.4k líneas,
  162 rutas.
- `scheduling.py` + `scheduling_config.json` — reglas de negocio, motivos, IDs,
  ocupación simulada. `dentidesk.py` — cliente de la API (modo mock si `enabled=false`).
- `notify.py` — email SMTP Gmail + `.ics`; despacha email/WhatsApp. `wa_cloud.py` —
  cliente WhatsApp Cloud API. `confirmaciones.py`, `recordatorios_wa.py`,
  `webhook_wa.py` — automatización de confirmaciones/recordatorios/botones.
- `pacientes.py` — base local anti-duplicados RUT→{nombres,apellidos,email,telefono}.
- `consentimientos.py` (+ `consentimiento.html`, `drive_backup.py`) — firma digital.
- `seguros.py` (+ `seguros_secretaria.html`, `seguros_seed/`) — formularios de reembolso.
- `compras.py` (+ `compras.html/js`, `print_agent.py`) — compras/stock (SQLite).
- `panel.html` — panel admin (pestañas; las "remotas" hablan directo a Render con ADMIN_TOKEN).

## Sistemas construidos (una línea + dónde mirar en CLAUDE.md)
- **Agendamiento online** — modal 4 pasos en el sitio → crea cita en DentiDesk.
  Caché de disponibilidad. → "Agendamiento online".
- **Confirmaciones de cita** — online al instante + barrido 4 ciclos/día + manual F2. → "Confirmaciones".
- **WhatsApp Cloud API (Meta)** — confirmaciones/recordatorios + webhook Confirmo/Anular
  que actualiza DentiDesk. **WABA REAL `&lt;WABA_ID_REAL&gt;`**, número +56 9 3355 8189.
  ⚠️ crear plantillas SIEMPRE en la WABA real, no en la de prueba. → "WhatsApp Cloud API".
- **Consentimientos informados** — F2 → link firmado (celular/tablet) → PDF reportlab
  + respaldo Google Drive. → "Consentimientos informados".
- **Seguros complementarios** — F2 tras emitir boleta → rellena el PDF OFICIAL de la
  aseguradora (**las 7 mapeadas**: Zurich, Colmena, MetLife, BUPA, Bice Vida, Consorcio,
  Vida Cámara — motor de relleno AcroForm/overlay con PyMuPDF) con datos+glosa de la boleta
  → email. Auto-envío opcional (vigilante en la extensión, APAGADO por defecto). → "Seguros
  Complementarios".
- **Recordatorio de control (recaptación)** — la asistente abre la cita de la última
  atención, F2 → WhatsApp al paciente para que agende. 3 guardas (no molestar / ya tiene
  hora / enviado hace poco). Reemplazó al Google Sheet "PACIENTES POR LLAMAR" — no
  revivirlo. → "Recordatorio de control".
- **Recordatorio de Control Dental** — al paciente con aparatos fijos/alineadores le llega un
  email cada 6 meses para que vaya a su dentista general. La inscripción es automática: un
  barrido diario de la agenda detecta instalaciones y retiros. Control manual desde F2 y
  panel. ⚠️ En el barrido de días pasados NO se usa `dentidesk._ESTADOS_INACTIVOS` (incluye
  "Atendido", que ahí es justo la señal buena). → "Recordatorio de Control Dental".
- **Compras / Gastos / Stock** — app con login/roles propios, SQLite, escaneo, etiquetas. → "Compras".
- **NPS / Satisfacción por WhatsApp** — barrido de citas atendidas → encuesta de 3 botones
  (Excelente/Buena/Puede mejorar). Promotor → link de reseña Google con el nombre del doctor;
  detractor → aviso a recepción. Módulo `nps.py`, override manual desde F2 (Enviar/No Enviar),
  pestaña "Satisfacción" en el panel. Arranca APAGADO (falta plantilla Meta + baseline). → "NPS / Encuesta de satisfacción".
- **Fechas de nacimiento y cumpleaños** — la base de pacientes ya tiene `fecha_nacimiento`
  (+ `id_paciente`), importada del export "Listado de Cumpleaños" de DentiDesk (**es una tabla
  HTML disfrazada de `.xls`**, se parsea con bs4). Cobertura ~49%. Se autorrellena en Seguros
  y alimenta la sección de cumpleaños del reporte diario (`cumpleanos.py`). → "Fechas de
  nacimiento y cumpleaños".
- **Asistente F2** — extensión MV3: `content.js` lee el modal, `background.js` hace los
  fetch, `config.js` (apiBase + adminToken en texto plano → no subir a repo público). → "Asistente F2".
- **PSQ (cuestionario de sueño pediátrico)** — página pública `/psq` (sin sesión, como
  `/consentimiento`): el apoderado responde el PSQ-CL de 22 ítems (versión chilena
  validada, Bertrán et al. 2024), el backend calcula el puntaje (corte 0,227) y envía el
  resultado por email al doctor que atendió por última vez al paciente en DentiDesk
  (`dentidesk.doctor_de_paciente`, hilo aparte para no bloquear la respuesta). Sin doctor
  resuelto o sin email configurado (`EMAIL_<DOCTOR>`) → cae a recepción. → "PSQ".

## Memorias (contexto que no está en el código)
En `C:\Users\ESTUDIO3D\.claude\projects\...\memory\`: índice en `MEMORY.md`. Relevantes:
`ortodoncia-richard-backend`, `asistente-f2-dentidesk`, `whatsapp-cloud-api-ortodoncia`,
`seguros-complementarios`.

## Para un producto NUEVO relacionado
Reutilizable directo: el backend Flask en Render (agrega un módulo autocontenido +
rutas en server.py, mismo patrón), la base local de pacientes, `notify.py`/`wa_cloud.py`
para avisar, el patrón de página pública con token firmado (consentimientos/seguros son
el molde), y la extensión F2 como puente hacia DentiDesk (leer el modal / scrapear con
la sesión). El límite recurrente es siempre el mismo: **DentiDesk expone poco por API;
lo demás se lee desde el navegador con la sesión activa.**
