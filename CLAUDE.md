# CLAUDE.md — Ortodoncia Richard

Contexto completo del proyecto para retomar en cualquier sesión futura.

---

## El proyecto

Sitio web estático de una página (scroll) para la **Clínica Ortodoncia Richard**, Las Condes, Santiago, Chile. Construido con HTML + CSS + JavaScript puro, alojado en GitHub Pages.

- **Repositorio:** https://github.com/delreal90/ortodonciarichard
- **GitHub Pages:** https://delreal90.github.io/ortodonciarichard
- **Dominio propio:** https://ortodonciarichard.cl (DNS pendiente de configurar en nic.cl)
- **Servidor local de desarrollo:** Python `http.server` en puerto 3000, configurado en `.claude/launch.json`

---

## La clínica

**Nombre:** Ortodoncia Richard (no "Ortodoncistas Richard")
**Dirección:** Paul Harris 10.349, oficina 305, piso 3, Las Condes, Santiago
**Teléfono:** +56 2 2217 3499
**WhatsApp:** +56 9 3355 8189
**Email:** recepcion@ortodonciarichard.cl
**Horario:** Lunes a Viernes, 9:00 a 19:30 hrs
**Redes:** Facebook e Instagram como @ortodonciarichard

---

## El equipo

### Especialistas (con foto y modal de CV)
| ID en JS | Nombre | Rol |
|---|---|---|
| `octavio` | Dr. Octavio Del Real S. | Ortodoncista |
| `rodrigo` | Dr. Rodrigo Oyonarte W. | Ortodoncista |
| `alberto` | Dr. Alberto Del Real V. | Ortodoncista |
| `patricio` | Dr. Patricio Vial U. | Rehabilitador Oral e Implantólogo |

Los 3 ortodoncistas son miembros de AAO, WFO y SORT Chile.
El Dr. Patricio Vial es miembro de implantología.

### Staff (placeholders — pendiente fotos y nombres)
- 2 Secretarias (placeholders S1, S2)
- 5 Asistentes Dentales (placeholders A1–A5)
- 3 Laboratorio y Aseo (placeholders L1–L3)

---

## Paleta de colores

```css
--navy:       #1A2E4A   /* color principal */
--navy-light: #243C5E
--navy-dark:  #111E30
--gold:       #C9A84C   /* acento dorado */
--gold-light: #D4B870
--white:      #FFFFFF
--light-bg:   #F0F5FB
--text-dark:  #1A2535
--text-mid:   #4A5568
```

Tipografía: **Playfair Display** (títulos) + **Inter** (cuerpo), ambas desde Google Fonts.
Íconos: **Font Awesome 6.5** vía CDN.

---

## Estructura de secciones

| Sección | ID | Descripción |
|---|---|---|
| Hero | `#inicio` | Video de fondo (0.75x velocidad), tagline, botones CTA |
| Nosotros | `#nosotros` | Descripción clínica, logos AAO/WFO/SORT Chile, 4 feature cards |
| Equipo | `#equipo` | Tabs (Especialistas/Secretaría/Asistentes/Laboratorio), modal CV al clic |
| Tratamientos | `#tratamientos` | Grid de 8 cards con fotos de casos clínicos |
| Clínica | `#galeria` | Galería de fotos + íconos de instalaciones |
| Agenda | `#agenda` | CTA con botón WhatsApp (preparado para DentiDesk) |
| Pacientes | `#pacientes` | Primera consulta (4 pasos) + FAQ en 6 tabs con acordeón |
| Contacto | `#contacto` | Formulario Web3Forms + mapa + datos + botones Google Maps/Waze |
| Footer | — | Logo, links, redes, datos de contacto |

---

## Archivos clave

```
index.html      ← estructura completa del sitio
css/styles.css  ← todos los estilos
js/main.js      ← lógica: nav sticky, modal doctores, FAQ tabs, acordeón, formulario
README.md       ← instrucciones de mantenimiento para el cliente
```

### Imágenes importantes
```
images/logo.jpg          ← logo celeste con fondo blanco (nav al hacer scroll)
images/logo-png.png      ← logo blanco sin fondo (nav sobre hero oscuro)
images/video.MP4         ← video del hero (reproducción a 0.75x)
images/sortch-png.png    ← logo SORT Chile
images/aao-png.png       ← logo AAO
images/WFO-png.png       ← logo WFO
images/urgencias.webp    ← guía de urgencias de la AAO (en sección Pacientes)
images/dr-*.jpeg/.png    ← fotos de los 4 doctores
images/ejemplo-*.jpg     ← fotos de casos clínicos (sección Tratamientos)
```

---

## Funcionalidades implementadas

- **Nav sticky** — transparente sobre hero, blanco con sombra al hacer scroll
- **Doble logo** — `logo-png.png` (blanco) sobre hero, `logo.jpg` (celeste) al hacer scroll
- **Hero con video** — `video.MP4` a 0.75x velocidad, fallback a `clinica-1.jpg`
- **Modal de doctores** — clic en card abre modal con foto, bio, formación y especialidades
- **Tabs del equipo** — Especialistas / Secretaría / Asistentes / Laboratorio
- **Placeholders con iniciales** — para staff sin foto aún
- **FAQ con tabs + acordeón** — 6 categorías, 25 preguntas extraídas del sitio Wix original
- **Primera consulta** — bloque de 4 pasos visuales
- **Formulario Web3Forms** — envía a recepcion@ortodonciarichard.cl
  - Access Key: `f0aa501d-602a-4212-ac11-16b414a91b61`
  - Si no está configurado, redirige a WhatsApp como fallback
- **Botones de navegación** — Google Maps y Waze desde la dirección
- **Scroll reveal** — animaciones suaves con Intersection Observer
- **Smooth scroll** — con offset del nav
- **Active nav link** — resalta la sección activa al hacer scroll
- **Mobile responsive** — breakpoints en 1024px y 640px

---

## Integraciones y servicios externos

| Servicio | Uso | Estado |
|---|---|---|
| GitHub Pages | Hosting | ✅ Activo |
| Web3Forms | Formulario de contacto | ✅ Configurado |
| Google Fonts | Tipografías | ✅ Activo |
| Font Awesome 6.5 CDN | Íconos | ✅ Activo |
| Google Maps embed | Mapa en contacto | ✅ Activo |
| WhatsApp MCP | Claude puede leer y enviar mensajes WhatsApp | ✅ Activo |
| Claude in Chrome | Claude puede leer la agenda de DentiDesk visualmente | ✅ Activo |
| DentiDesk (app.dentidesk.cl) | Agenda online (API pendiente) | ⏳ Pendiente API |
| nic.cl DNS | Dominio personalizado | ⏳ Pendiente |

---

## DentiDesk — Estados de Cita

DentiDesk usa íconos de colores para indicar el estado de cada cita en la agenda:

| Estado | Descripción |
|---|---|
| No confirmado | Cita agendada sin confirmar aún |
| Confirmado | Paciente confirmó asistencia |
| Hora Cancelada | El paciente canceló la hora |
| Confirmado por e-mail | Confirmación vía correo |
| Cancelado por e-mail | Cancelación vía correo |
| Atendido | Paciente ya fue atendido |
| Re-agendado | La cita fue movida a otro horario |
| En Sala de Espera | Paciente llegó y espera en sala |
| En sillón | Paciente está en atención |
| Paciente no llega | No se presentó a la cita |
| Hora cancelada por la clínica | Cancelación iniciada por la clínica |
| No Contesta el Teléfono | Se intentó llamar sin éxito |
| Primera Consulta Ingresada | Nueva consulta inicial registrada |
| Ficha Primera Consulta | Primera consulta con ficha clínica |
| Confirmado por WhatsApp | Confirmación recibida por WhatsApp |
| Falta enviada por WhatsApp | Se notificó falta al paciente por WhatsApp |
| 1 SEMANA Confirmado por WhatsApp | Confirmación enviada con 1 semana de anticipación |
| No seguir (conversado con tratante) | Paciente no continúa tratamiento |

### Sufijos en nombre del paciente
Los pacientes aparecen con sufijos que indican tipo de cita:
- `-D` = Dispositivo (control de aparato)
- `-DD` = posiblemente doble dispositivo o tipo de control
- `-DE` = posiblemente dispositivo especial
- Número (ej: `3295L`) = número de ficha del paciente

### Workflow actual sin API (usando Claude in Chrome)
1. Abrir sesión Code con el bridge de WhatsApp corriendo
2. Pedir a Claude: *"Abre DentiDesk y dime la agenda del Dr. [nombre] para hoy"*
3. Claude navega a `app.dentidesk.cl`, cambia a vista Día, filtra por doctor
4. Cruza con mensajes de WhatsApp si es necesario

### Cuando esté disponible la API de DentiDesk
Reemplazar el botón WhatsApp en `#agenda` (buscar comentario `TODO: DentiDesk` en `index.html`).

### Agendamiento online — INTEGRACIÓN REAL CABLEADA (probada en vivo)
Flujo completo construido y **probado en vivo contra DentiDesk** (auth + disponibilidad).
Datos reales (diccionario API 375): IdLocation **408**, IdStatus nueva cita **2120**,
profesionales Octavio 9412 / Rodrigo 8452 / Alberto 639 / Patricio 9308.

**Credenciales (NUNCA en git):**
- Local: `admin/scheduling_secrets.json` (gitignored) con email/password + `enabled:true`.
- Render (producción): variables de entorno `DENTIDESK_EMAIL`, `DENTIDESK_PASSWORD`,
  `DENTIDESK_ENABLED=true`. `load_config()` las superpone (env > secrets > config).
- El config versionado queda con `enabled:false` y credenciales vacías (seguro por defecto).
- ⚠️ La password entregada es TEMPORAL — rotar en DentiDesk y actualizar env var.

**Quirks de la API DentiDesk (importantes):**
- `getAvailableHours` responde **401** cuando el profesional NO tiene horas ese día
  (NO es error de auth). Formato OK: `{"data":{"YYYY-MM-DD":["10:00","11:30",...]}}`.
- Granularidad real **15 min** (config `slot_minutos:15`).
- **NO existe endpoint de búsqueda de paciente por RUT** → el paciente siempre ingresa
  sus datos; el RUT se valida y se envía en `createAgenda` (RutPatient).
- Solo 6 endpoints: authentication, getAgendaDay, updateAgenda, getAgendaStatus,
  createAgenda, getAvailableHours.
- Disponibilidad: server.py consulta los 15 días en paralelo (ThreadPool) + cache 90s
  (1ª carga ~4s, navegación posterior instantánea).

**createAgenda probado en vivo** ✅ (cita de prueba creada y verificada). Aprendizajes:
- `EmailPatient` es OBLIGATORIO (sin email → 401 "Faltan datos obligatorios").
- **DEDUPLICACIÓN: DentiDesk asocia la cita a la ficha existente solo si coinciden
  RUT + EMAIL.** Si el email no coincide con el de la ficha → crea paciente DUPLICADO.

**Solución anti-duplicados (implementada) — `admin/pacientes.py`:**
- Base local `patient_index.json` (gitignored, datos personales) `{RUT → nombres,
  apellidos, email, telefono}`. Sembrada con el export Excel del panel (~4.000 pacientes)
  y refrescada 2×/día barriendo `getAgendaDay` (`admin/actualizar_pacientes.py`).
- Al agendar: si el RUT está en la base, el backend usa SU email registrado → DentiDesk
  reconoce al paciente y NO duplica. Paciente nuevo (no en base) → ingresa su email.
- PRIVACIDAD: al frontend solo va enmascarado (`ma***@gm***.cl`, `*****1234`); el email
  real nunca sale del backend (evita cosechar datos probando RUTs).
- DentiDesk NO expone IdPatient ni endpoint de pacientes → este es el único camino.
- Ruta de la base configurable por env `PATIENT_INDEX_PATH` (para disco persistente).

**Pendiente para ir 100% live:**
1. Setear env vars en Render: `DENTIDESK_EMAIL`, `DENTIDESK_PASSWORD`, `DENTIDESK_ENABLED=true`.
2. DECIDIR dónde vive la base de pacientes en producción (Render tiene disco efímero):
   (a) backend en el PC siempre-encendido de la clínica (persiste + co-ubicado con
   WhatsApp), o (b) Render con disco persistente + seed por upload. PENDIENTE definir.

**Arquitectura (modular, reutilizable por el futuro bot de WhatsApp):**
```
admin/scheduling_config.json  ← config editable (NO tocar código): % ocupación por
                                 doctor/franja, motivos, IDs DentiDesk, reglas, credenciales
admin/scheduling.py           ← CEREBRO sin red: reglas de negocio, simulación de
                                 ocupación (determinista), grilla horaria, .ics
admin/dentidesk.py            ← cliente API DentiDesk (auth JWT 1-uso + availability +
                                 create). Modo mock si enabled=false
admin/notify.py               ← confirmación: email SMTP + .ics, fallback WhatsApp (bridge :8080)
admin/confirmaciones.py       ← barrido de citas presenciales/teléfono (4 ciclos diarios);
                                 marcar_enviada() para no duplicar con online/F2
admin/server.py               ← rutas Flask: /api/agenda/config|disponibilidad|reservar,
                                 /api/scheduling-config, /api/asistente/confirmar-cita (F2),
                                 y el scheduler (_loop_confirmaciones + refresco pacientes)
js/agenda-dentidesk.js        ← modal de 4 pasos (motivo→doctor→fecha/hora→datos)
index.html                    ← botón "Agendar hora online" + markup del modal
admin/panel.html              ← sección "Agenda online" para ajustar % de ocupación

dentidesk-assistant/          ← extensión F2 (proyecto SEPARADO, a comercializar)
  manifest.json, config.js, content.js, background.js, INSTALAR.md
```

**Reglas implementadas:** anticipación mínima 12h (salvo Urgencia); 5 motivos;
simulación de ocupación mínima aparente determinista y consistente
(65-70% próximos 5 hábiles / 45-55% semana sig. / 30-40% semana posterior),
configurable por doctor desde el panel; foto del doctor desde `doctorData` (main.js).

**Pendiente para producción:**
1. Credenciales DentiDesk en `scheduling_config.json` (email, password, basic auth).
2. `professional_id` real de cada doctor y `id_reason` de cada motivo (los entrega
   la clínica con las credenciales). Hoy octavio=7 es de ejemplo, el resto en 0.
3. `enabled: true`.
4. Hosting del backend Flask accesible desde el sitio (en GitHub Pages el front es
   estático; el backend debe correr en un servidor con HTTPS, o mover la lógica a
   otra función serverless). El frontend apunta a `window.AGENDA_API_BASE` (default
   `http://localhost:5001`).
5. Verificar el endpoint real de `getAvailableHours` (formato de respuesta) — el
   parser en `dentidesk.py` ya contempla lista de strings o de objetos `{Hour}`.

---

## Confirmaciones de cita — online, automáticas (4 ciclos) y manuales (F2)

Hay TRES formas en que un paciente recibe el correo de confirmación (mismo HTML +
`.ics`, generado por `notify.enviar_confirmacion()`):

1. **Online** — quien agenda por el sitio recibe la confirmación al instante dentro
   de `/api/agenda/reservar`. Esa cita se registra como enviada (`marcar_enviada`)
   para que el barrido no la duplique.
2. **Automática (barrido)** — `confirmaciones.barrer_y_confirmar()` corre en 4 ciclos
   diarios: `_HORARIOS_CONFIRMACION = ['11:00','13:30','17:00','19:45']` (hora Chile,
   en `server.py` → `_loop_confirmaciones`). Recorre `getAgendaDay`, detecta citas
   NUEVAS con email (agendadas presencial/teléfono) y les envía la confirmación.
   - **Anti-spam:** la PRIMERA corrida solo SIEMBRA (registra lo existente sin enviar).
     Si no, le llegaría correo a cientos de pacientes que ya tenían hora.
   - Solo envía a citas creadas desde el último barrido (con 1 día de margen).
   - Registro en `confirmaciones_enviadas.json` (`{IdAgenda: timestamp}`, gitignored).
3. **Manual (asistente F2)** — la secretaria fuerza el envío inmediato desde la
   extensión de navegador (ver sección siguiente). Endpoint
   `POST /api/asistente/confirmar-cita`.

### Regla clave: asimetría manual vs automático
- **F2 (manual) envía SIEMPRE, sin condiciones** — no consulta el registro antes de
  enviar. Si la secretaria lo aprieta es porque alguien lo pidió (aunque ya se haya
  enviado antes, por automático o por otro F2). Después de enviar, marca la cita.
- **El barrido automático NO reenvía** lo que ya está en el registro (`ida in idx`
  → skip). Así, lo que F2 ya mandó, los ciclos de las 11/13:30/17/19:45 lo saltan.
- En resumen: el envío manual es "el jefe" (manda sin preguntar); el automático es
  "tímido" (solo manda lo que nunca se tocó). Esto NO requiere código extra: emerge
  del diseño (F2 no chequea registro + barrido sí lo chequea).

---

## Asistente F2 — extensión de navegador (`dentidesk-assistant/`)

Producto SEPARADO del sitio (se comercializará; ver `HANDOFF` en Desktop/Borrame).
Extensión Manifest V3 para Chrome/Edge/Firefox que la secretaria invoca con **F2**
mientras tiene abierta una cita en `app.dentidesk.cl`.

**Carpeta:** `C:\Users\ESTUDIO3D\Claude Code Playground\dentidesk-assistant\`
```
manifest.json   ← MV3; content_scripts = [config.js, content.js]; host_permissions
                  incluye app.dentidesk.cl + el backend (onrender + localhost:5001)
config.js       ← valores PRECARGADOS (apiBase + adminToken). Editar aquí para dejar
                  la extensión lista en un PC nuevo sin escribir nada en el panel ⚙.
                  ⚠️ Contiene el ADMIN_TOKEN en texto plano → NO subir a repo público.
content.js      ← F2 lee el modal (#id_agenda, #email, #motivo, …), muestra panel
                  flotante en Shadow DOM, llama al backend vía background.js.
background.js   ← service worker: hace el fetch al endpoint (evita CORS del content).
INSTALAR.md     ← cómo cargar la extensión descomprimida + configurar.
```

**Cómo lee la cita:** del DOM del modal abierto (no de la API). IDs clave:
`#id_agenda`, `#id_paciente`, `#nombre`, `#apellido`, `#email`, `#diacita/#mescita/
#aniocita`, `#horac/#minutos`, `#dentista_cita`, `#motivo` (value=id, text=label).

**Flujo del botón "Enviar confirmación":**
```
F2 → content.js lee idAgenda + email del modal
   → background.js → POST {apiBase}/api/asistente/confirmar-cita
        body: { id_agenda, fecha, email }   header: X-Admin-Token
   → backend trae la agenda FRESCA (getAgendaDay force=True, sin caché)
   → valida estado activo + email (respaldo: el email del modal si DentiDesk no lo tiene)
   → notify.enviar_confirmacion()  →  marcar_enviada()
   → panel muestra "✅ Confirmación enviada a co***@gm***.cl"
```

**Dos quirks resueltos (importantes si se retoma):**
1. **Bootstrap `enforceFocus`** — el modal de DentiDesk roba el foco a cualquier
   elemento fuera de `#modal_cita`. Por eso el panel se cuelga DENTRO de `#modal_cita`
   (no de `<body>`); así Bootstrap lo considera "parte del modal" y deja escribir en
   los inputs (p.ej. el campo del token). Hay además un interceptor de `focusin` en
   captura como defensa adicional.
2. **Caché de agenda** — el endpoint usa `force=True` para no leer datos viejos: el
   asistente se usa justo después de editar/guardar la cita en DentiDesk.

**Config / multi-PC:** el token y la URL se guardan por navegador en
`chrome.storage.local` (panel ⚙). Para un PC nuevo, lo más simple es precargar el
token en `config.js`. Para producto multi-clínica habrá que pasar a token por
instalación (no compartido) — ver HANDOFF.

**Endpoint backend** (`server.py` → `asistente_confirmar_cita`):
`POST /api/asistente/confirmar-cita`, body `{id_agenda, fecha, email?}`, protegido por
`ADMIN_TOKEN` (header `X-Admin-Token`). Funciona también en modo mock (enabled=false).

---

## Consentimientos informados — firma digital + respaldo Drive

Sistema para que el paciente (o apoderado) firme el consentimiento informado de
ortodoncia digitalmente, quede respaldado y se archive en su ficha DentiDesk.

**Documento base:** versión v2 del consentimiento (Word), 7 secciones en 1ª persona.
El screening de antecedentes médicos NO va en este formulario (se levanta en la
pestaña "Anamnesis general" de DentiDesk).

**Flujo completo:**
1. La secretaria, desde el asistente **F2** (con la cita abierta en DentiDesk), aprieta
   una de 3 opciones en la sección "Consentimiento informado": 📧 Mail · 💬 WhatsApp ·
   📋 Tablet de recepción. F2 lee el `#rut` del modal y llama al backend.
2. **Celular (mail/WhatsApp):** el backend genera un token firmado (itsdangerous, 30 días)
   y envía un link. El paciente abre `consentimiento.html?token=…`, que prellena su
   nombre/RUT vía `pacientes.lookup(rut)` y firma en un canvas.
3. **Tablet:** el backend deja el `{rut,tipo,id}` en una cola de 1 ítem; la tablet
   (`consentimiento.html?modo=kiosco`) hace polling cada 4s y salta a una pantalla de
   **confirmación de identidad** ("¿Eres tú? [nombre]") antes de firmar. También permite
   búsqueda manual por RUT (walk-up).
4. Al firmar: se genera el **PDF** (reportlab, 7 secciones + firma + datos del firmante),
   se registra el estado, y se **sube automáticamente a Google Drive** (respaldo).

**Archivos:**
```
admin/consentimiento.html      ← página de firma (celular + kiosco/tablet), canvas vanilla
admin/consentimientos.py       ← tokens, registro de estado, cola tablet, generación de PDF
admin/drive_backup.py          ← subida a Google Drive (cuenta de servicio)
admin/drive_service_account.json ← credenciales cuenta servicio (GITIGNORED)
dentidesk-assistant/content.js ← botón "Consentimiento informado" en F2 (3 canales)
admin/panel.html               ← pestaña "Consentimientos" (worklist + estados)
```

**Endpoints** (`server.py`, todos body JSON):
- `POST /api/consentimiento/enviar` `{rut,tipo,canal}` — F2, protegido ADMIN_TOKEN.
- `GET  /api/consentimiento/datos?token=` — prellenado celular (sin email/tel).
- `GET  /api/consentimiento/tablet/buscar?rut=` — búsqueda kiosco (KIOSK_TOKEN).
- `GET  /api/consentimiento/tablet/cola` — polling de la tablet.
- `POST /api/consentimiento/firmar` — recibe firma, genera PDF, sube a Drive.
- `GET  /api/consentimientos?estado=` — lista para el panel (ADMIN_TOKEN).
- `POST /api/consentimiento/marcar-subido` `{id}` — marca subido a DentiDesk.

Estados: `enviado` → `firmado` → `subido`. Registro en `consentimientos_registro.json`
(gitignored). PDFs en `consentimientos_firmados/` (gitignored). `respaldo_drive` = true/false.

**Google Drive (respaldo):** cuenta de servicio `claude@intrepid-charge-501115-n0.iam.
gserviceaccount.com`, **Unidad compartida** (Shared Drive) ID `0AKiV1nLsqi2dUk9PVA`.
⚠️ Debe ser Unidad compartida, NO carpeta de "Mi unidad" — las cuentas de servicio no
tienen cuota propia (error `storageQuotaExceeded`). Scope `drive` completo (no `drive.file`).
En Render: env var `GOOGLE_SERVICE_ACCOUNT_JSON` = JSON entero (drive_backup.py lo soporta).

### ⚠️ Subida a DentiDesk: NO se puede automatizar (probado en vivo)

Se investigó a fondo subir el PDF a la ficha (pestaña **Informes**, plugin
jquery-uploadfile, POST a `ajax/ajaxUpload.php`). **Ninguna vía funciona sin intervención
humana en este entorno:**
- `file_upload` de la extensión Chrome → bloqueado (no hay carpeta compartible en Claude Code).
- Extensión + diálogo nativo de Windows → imposible (la extensión no ve ventanas del SO).
- computer-use + diálogo nativo → el diálogo de archivos hereda el tier "read" del
  navegador → click/typing bloqueados (confirmado en vivo).
- Inyección JS (`DataTransfer` + `jQuery.trigger('change')`) → sube a temporal
  (`imagen/temp/e_375/p_1103479/…`, `id:0`) pero falta una 2ª fase "guardar" enterrada
  en JS minificado que el filtro de seguridad impide leer.

**Flujo supervisado (el que se usa):** el panel (pestaña Consentimientos) lista los
`firmado` con botón **"Abrir en DentiDesk"** (abre `pacientes.php?rut=…`) y **"Ya lo subí"**
(marca subido). El humano hace solo el gesto bloqueado (elegir el PDF en el diálogo).
En una sesión Code, Claude puede orquestar (abrir paciente, click Subir, verificar) vía
la extensión — el humano solo elige el archivo. Si algún día DentiDesk expone API de
documentos, reemplazar este flujo por subida directa.

---

## Infraestructura decidida (producción)

| Servicio | Rol | Costo |
|---|---|---|
| GitHub Pages | Sitio web estático | Gratis |
| Cloudflare | DNS + CDN + protección DDoS + HTTPS | Gratis |
| Render (plan Starter $7 USD/mes) | Backend Flask — siempre despierto, credenciales privadas | $7 USD/mes |
| nic.cl | Dominio `ortodonciarichard.cl` | ~$8.500 CLP/año |

Flujo: Paciente → Cloudflare → GitHub Pages → (al agendar) → Render (Flask + DentiDesk)

Render se conecta al repo de GitHub: cada `git push` redespliegue automático del backend.
Las credenciales de DentiDesk van como variables de entorno en el panel de Render (nunca en el código).

## Pendientes

### 🔴 Bloqueantes
1. **Credenciales DentiDesk** — email, password, basic auth, `professional_id` de cada doctor, `id_reason` de cada motivo
2. **Confirmar endpoint búsqueda paciente por RUT** — no está en la doc pública, hay que preguntarle a DentiDesk
3. **Configurar Render** — subir backend Flask, configurar variables de entorno, conectar a GitHub
4. **DNS en nic.cl + Cloudflare** — sin borrar registros MX del correo Gmail

### 🟡 Importantes
5. **GitHub Pages custom domain** — Settings → Pages → Custom domain → `ortodonciarichard.cl`
6. **Rate limiting en backend** — agregar `flask-limiter` antes de publicitar el agendamiento online

### 🟢 Mejoras
7. **Fotos y nombres del staff** — reemplazar placeholders de secretarias, asistentes y laboratorio
8. **Fotos adicionales de la clínica** — recepción, 8 boxes, sala diagnóstico, laboratorio, esterilización, rayos
9. **Casos de Instagram** — usar fotos de casos publicados en @ortodonciarichard para la sección de tratamientos
10. **Bot de WhatsApp** — agendar por WhatsApp usando el mismo backend Flask (infraestructura ya diseñada)

---

## Cómo publicar cambios

```bash
cd "C:\Users\ESTUDIO3D\Claude Code Playground\ortodonciarichard"
git add .
git commit -m "descripción del cambio"
git push
```

GitHub Pages publica automáticamente en 1-2 minutos.

---

## Notas técnicas

- El sitio NO usa frameworks (no React, no Vue, no Node) — es HTML/CSS/JS puro intencionalmente para simplificar el hosting y mantenimiento
- El servidor local usa Python `http.server` (Python 3.11 disponible en la máquina)
- Node.js, Go, y MSYS2/gcc están instalados en la máquina
- Git usuario configurado localmente: `delreal90` / `recepcion@ortodonciarichard.cl`
- El video hero es pesado — el preview interno de Claude Code a veces se traba por esto; verificar siempre en `http://localhost:3000` en el navegador real
- La cuenta de GitHub es `delreal90`
- El correo de la clínica funciona con Gmail (`recepcion@ortodonciarichard.cl`) — al configurar el DNS en nic.cl NO tocar los registros MX existentes

---

## WhatsApp Cloud API oficial (Meta) — migración en curso

Objetivo: reemplazar el WhatsApp NO oficial (bridge whatsmeow en `notify.py`, fallback
local que NO corre en Render) por la **Cloud API oficial de Meta**, para enviar
**confirmación al agendar** y **recordatorio previo** desde el backend en producción.

### Datos auditados de la app de Meta (Fase 1, auditada 2026-06-30 vía Claude in Chrome)
| Dato | Valor |
|---|---|
| App | **WA automáticos** · App ID `1047459514605008` |
| Portfolio comercial (business_id) | `205682900395758` |
| Número de PRUEBA (de Meta, gratis) | +1 (555) 649-1179 |
| **Phone Number ID** (test) | `1132643936607937` |
| **WABA ID** (WhatsApp Business Account) | `2209662166461456` |
| Destinatario de prueba registrado | +56 9 8903 2888 (celular Alberto) ✅ |
| **Verificación del negocio** | ✅ **APROBADA** |
| Token | Solo botón "Generar token" (temporal 24h). Producción → **System User token permanente** |
| App | "Sin publicar" (normal en esta etapa) |

NO hacer: "Conviértete en proveedor de tecnología" (Tech Provider) — es para revendedores, no aplica.

### Estado y plan por fases
- **Fase 1 — Auditar app Meta:** ✅ HECHA (tabla arriba). Verificación aprobada, prueba envió OK la semana del 26-06.
- **Fase 2 — Plantillas (templates):** EN CURSO (creadas 2026-06-30 vía Administrador de WhatsApp
  business.facebook.com). Idioma **Spanish (CHL) = `es_CL`**, todas categoría **Utilidad**, pie de
  página fijo "Clínica de Ortodoncia C. Richard", trato "Estimado(a) {nombre}". Variables:
  {{1}}=nombre, {{2}}=doctor, {{3}}=fecha, {{4}}=hora.
  - `conversacion_general` — abridora flexible ({{1}}=nombre, {{2}}=motivo libre). Botón [Sí, díganme]. ✅ En revisión
  - `confirmacion_hora` — sin botones (se manda justo al agendar). ✅ En revisión
  - `recordatorio_semana` — botones [Confirmo][Reagendar][Anular]. ✅ En revisión
  - `recordatorio_dia` — "mañana" en minúscula; botones [Confirmo][Reagendar][Anular]. ✅ En revisión
  - `inasistencia_reagendar` — botón [Reagendar]. ✅ En revisión
  - `primera_consulta` — encabezado VIDEO + botones [Confirmo][Reagendar]. ⏳ Armada; falta subir el
    video (`C:\Users\ESTUDIO3D\Desktop\COCRL\Redes Sociales\Video Primera Consulta.mp4`, 5 MB) —
    la subida de archivo NO se puede automatizar (Chrome MCP solo sube archivos compartidos con la
    sesión); el usuario la hace manual y luego "Enviar para revisión".
  - Reglas de negocio pendientes de cablear en el backend (Fase 3): botón "Anular" → registrar la
    cita como ANULADA en DentiDesk (`updateAgenda`) + avisar a recepción al instante. "Reagendar" →
    bot manda horas disponibles en el chat (reusa getAvailableHours) con opción SIEMPRE visible de
    "hablar con una persona" (handoff a recepción). Vigilar aprobación de Meta con `/loop`.
  - Encabezado de ubicación (pin del mapa) PENDIENTE para confirmacion_hora y recordatorio_dia:
    falta el lat/long exacto de la clínica (el sitio usa Maps por dirección, no por coordenadas).
- **Fase 3 — Código:** módulo nuevo `admin/wa_cloud.py` (POST a `graph.facebook.com/v.../{phone_number_id}/messages`)
  conectado dentro de `_enviar_whatsapp()` en `notify.py` (un solo punto de cambio). Webhook
  entrante en `server.py` (verify token + recepción de respuestas/estados de entrega).
- **Fase 4 — Deploy + prueba:** env vars en Render `WA_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_VERIFY_TOKEN`.
  Generar el token 24h JUSTO al probar (caduca rápido). Probar contra número de test → celular Alberto.
- **Fase 5 — Producción:** registrar el número real **+56 9 3355 8189** en la WABA. ⚠️ Al registrarlo
  en la Cloud API se DESCONECTA del WhatsApp normal del celular (todo pasa a API). Verificación ya
  aprobada acelera esto. Subir tier de envíos.

### Notas clave
- Ventana de 24h: fuera de ella solo se pueden enviar PLANTILLAS aprobadas (caso confirmación/recordatorio).
- El número de prueba solo envía a destinatarios pre-registrados (máx. 5).
- Webhooks ya llegan (probado): `{"object":"whatsapp_business_account",...}`. Render da el HTTPS público.
- El bridge whatsmeow (sección siguiente) queda como herramienta de Claude/MCP, NO como canal de producción.

---

## WhatsApp MCP — Configuración

El MCP de WhatsApp está **instalado y funcionando**. Permite a Claude leer y enviar mensajes de WhatsApp.

### Archivos relevantes
```
C:\Users\ESTUDIO3D\Claude Code Playground\whatsapp-mcp-vgp\   ← repositorio (fork verygoodplugins)
  whatsapp-bridge\        ← bridge Go (whatsmeow)
    whatsapp-bridge.exe   ← ejecutable compilado
    store\
      whatsapp.db         ← sesión autenticada (QR ya escaneado)
      messages.db         ← historial de mensajes
      .bridge-token       ← token de auth para la API HTTP
  whatsapp-mcp-server\    ← servidor MCP Python (FastMCP)
    main.py
C:\Users\ESTUDIO3D\Claude Code Playground\Iniciar WhatsApp Bridge.bat  ← atajar para iniciar bridge
C:\Users\ESTUDIO3D\.claude\settings.json   ← config global MCP
C:\Users\ESTUDIO3D\Claude Code Playground\.mcp.json  ← config MCP nivel proyecto
```

### Cómo usar
1. **Iniciar el bridge**: ejecutar `Iniciar WhatsApp Bridge.bat` (dejar ventana abierta)
2. **Abrir sesión Code** en `Claude Code Playground`
3. Claude ya tiene acceso a las herramientas de WhatsApp automáticamente

### Herramientas disponibles
`list_chats`, `list_messages`, `search_contacts`, `get_contact`, `send_message`, `send_file`, `send_audio_message`, `get_last_interaction`, `get_message_context`

### Notas
- El bridge corre en `http://localhost:8080/api` y requiere token de autenticación
- Solo funciona en sesiones **Code** (no en Cowork ni Chat — esos corren en la nube)
- Si el bridge no está corriendo, los tools de WhatsApp darán error al ejecutarse (pero igual aparecen disponibles)
