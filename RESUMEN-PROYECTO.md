# Ortodoncia Richard — Resumen del proyecto (LÉEME PRIMERO)

Mapa general para retomar o construir algo NUEVO relacionado, sin leer todo el
`CLAUDE.md` (que tiene el detalle fino de cada sistema y bug). Cuando necesites
profundizar en un sistema, salta a su sección en `CLAUDE.md`.

## Qué es
Clínica de ortodoncia en Las Condes, Santiago. El proyecto tiene 4 piezas:
1. **Sitio web** estático (HTML/CSS/JS puro) — GitHub Pages, dominio
   `ortodonciarichard.cl` (DNS Cloudflare). Repo `github.com/delreal90/ortodonciarichard`.
2. **Backend Flask** (`admin/`) — en **Render** (plan Starter, siempre activo).
   **Deploy = `git push`** (Render redespliega solo desde el mismo repo).
3. **Extensión F2** (`dentidesk-assistant/`, Manifest V3) — asistente que la
   secretaria invoca con F2 sobre `app.dentidesk.cl`. Proyecto **separado, pensado
   para comercializarse**; carpeta compartida entre los PC de la clínica.
4. **Sub-apps** servidas por el mismo backend: panel admin, agendamiento online,
   consentimientos, seguros complementarios, compras/stock.

## Infra y convenciones (importantes)
- **Deploy backend:** `cd ortodonciarichard && git add . && git commit && git push`.
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
- **Zona horaria:** Render corre en UTC; usar los helpers `ahora_chile()`
  (zoneinfo `America/Santiago`, paquete `tzdata` en requirements).
- **Paciente de prueba:** Alberto Del Real, RUT **17.406.985-9**,
  delreal90@gmail.com, celular de prueba **+56 9 8903 2888**. Autorizado crear
  citas de prueba. ⚠️ NUNCA escribir a +56 9 9903 2888 (es un tercero real).
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

## Backend: módulos (`admin/`)
- `server.py` — todas las rutas Flask + schedulers en hilos (`_loop_*`). ~3.7k líneas.
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
  que actualiza DentiDesk. **WABA REAL `106738482086473`**, número +56 9 3355 8189.
  ⚠️ crear plantillas SIEMPRE en la WABA real, no en la de prueba. → "WhatsApp Cloud API".
- **Consentimientos informados** — F2 → link firmado (celular/tablet) → PDF reportlab
  + respaldo Google Drive. → "Consentimientos informados".
- **Seguros complementarios** — F2 tras emitir boleta → rellena el PDF OFICIAL de la
  aseguradora (Zurich/Colmena mapeadas) con datos+glosa de la boleta → email. Auto-envío
  opcional (vigilante en la extensión, APAGADO por defecto). → "Seguros Complementarios".
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
- **Asistente F2** — extensión MV3: `content.js` lee el modal, `background.js` hace los
  fetch, `config.js` (apiBase + adminToken en texto plano → no subir a repo público). → "Asistente F2".

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
