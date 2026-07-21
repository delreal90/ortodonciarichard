# CLAUDE.md — Ortodoncia Richard

Contexto completo del proyecto para retomar en cualquier sesión futura.

> **¿Sesión nueva o producto relacionado? Lee primero [`RESUMEN-PROYECTO.md`](RESUMEN-PROYECTO.md)**
> — mapa de una página (arquitectura, infra, hechos duros de DentiDesk) con
> punteros a las secciones de acá. Este CLAUDE.md tiene el detalle fino de cada
> sistema; el RESUMEN es para no cargar todo el contexto.

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
                                 · /disponibilidad acepta min_dias (2026-07-07): el servidor
                                 escanea lotes de 6 días en paralelo hasta juntar N días CON
                                 horas (tope 30/request). Antes el frontend paginaba en serie
                                 (2-3s por página fría; el JWT de DentiDesk es de UN SOLO USO
                                 —verificado en vivo— así que cada consulta son 2 round-trips)
                                 y con doctores de pocos días la mediana real era 7.5s.
                                 · Disponibilidad v2 (2026-07-07): el caché es por (doctor,
                                 día), NO por motivo — UNA getAvailableHours con el motivo
                                 MÁS CORTO de la especialidad como sonda (DentiDesk descuenta
                                 citas+bloqueos+feriados+vacaciones; getAgendaDay NO trae
                                 bloqueos ni feriados, verificado en vivo) y las horas de cada
                                 motivo se derivan localmente por duración
                                 (dentidesk.horas_que_caben — coincide EXACTO con DentiDesk,
                                 validado). _loop_calentador refresca 15 días hábiles ×
                                 doctores cada 20 min y cada reserva refresca su día al
                                 instante (_refrescar_dia_reservado). "establecida como
                                 feriado" (401) = día sin horas, no error.
                                 · /api/agenda/reservar-estudio (2026-07-07): Estudio Integral
                                 de Ortodoncia = motivo compuesto `estudio_integral` que
                                 agenda 2 citas (estudio_registros IdReason 23935 +
                                 estudio_explicacion 18167, 30 min c/u, motivos `oculto` del
                                 menú) con ≥14 días de separación (config
                                 separacion_min_dias). SOLO pacientes ya en la base local
                                 (gate 403 → el frontend ofrece Primera Consulta). Valida
                                 ambas horas EN VIVO; si la 2ª cita falla, cancela la 1ª
                                 (rollback a id_status_cancelado). El frontend elige hora en
                                 2 etapas (agenda.estudio; la etapa 2 filtra fechas con
                                 agenda.filtroMinFecha).
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

**Flujo del botón "Enviar confirmación" (2026-07-01: ahora 2 botones, la secretaria elige canal):**
```
F2 → "📧 Enviar por email" / "💬 Enviar por WhatsApp" (content.js: confirmarCita('email'|'whatsapp'))
   → background.js → POST {apiBase}/api/asistente/confirmar-cita
        body: { id_agenda, fecha, email, canal }   header: X-Admin-Token
   → backend trae la agenda FRESCA (getAgendaDay force=True, sin caché)
   → valida estado activo + el dato que exige el canal (email para 'email', telefono para
     'whatsapp' — el telefono viene de DentiDesk (Phone), no del modal)
   → notify.enviar_confirmacion(cita, cfg, canal=canal)  →  marcar_enviada()
   → panel muestra "✅ Confirmación enviada por WhatsApp" o "por email a co***@gm***.cl"
```
`canal=None` (no se manda) = comportamiento automático de siempre (email con WhatsApp de
respaldo) — lo sigue usando el agendamiento online y el barrido de confirmaciones, no el F2.

**Consentimiento informado — colapsable (2026-07-01):** el botón "📄 Consentimiento informado"
ahora se despliega (chevron ▾) mostrando los 3 sub-botones (mail/WhatsApp/tablet) en vez de
mostrarlos siempre sueltos. Se cierra solo al mostrar una cita nueva (`toggleConsent()`,
`#consentBody`/`#consentChevron` en content.js).

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
`POST /api/asistente/confirmar-cita`, body `{id_agenda, fecha, email?, canal?}` (`canal`:
`'email'|'whatsapp'`, opcional), protegido por `ADMIN_TOKEN` (header `X-Admin-Token`).
Funciona también en modo mock (enabled=false).

**Pendiente (pedido por el usuario, sin implementar aún):** cuando el motivo es "primera
consulta", agregar además del envío del video (`primera_consulta` template, aún no cableado
al F2 — hoy sigue como placeholder deshabilitado "Próximamente" en `accionesContexto()`) un
botón para enviar el link del **formulario de primera consulta (Google Forms)** — falta que
el usuario pase el link real.

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
- `POST /api/consentimiento/firmar` — recibe firma, genera PDF, sube a Drive, envía copia al mail del paciente.
- `GET  /api/consentimientos?estado=` — lista para el panel (ADMIN_TOKEN).
- `POST /api/consentimiento/marcar-subido` `{id}` — marca subido a DentiDesk.
- `POST /api/consentimiento/borrar` `{id}` — borra un registro SOLO si estado='enviado'
  (nunca firmado). El backend rechaza borrar uno firmado (409); es la fuente de verdad,
  no solo el frontend.

Estados: `enviado` → `firmado` → `subido`. Registro en `consentimientos_registro.json`
(gitignored). PDFs en `consentimientos_firmados/` (gitignored). `respaldo_drive` = true/false,
`drive_file_id` (para el botón "Abrir en Drive" del panel), `pdf_sha256` (hash real del PDF).

**Zona horaria:** el servidor en Render corre en UTC. Todas las fechas del registro y el
sello del PDF usan `consentimientos.ahora_chile()` (zoneinfo `America/Santiago` + paquete
`tzdata` en requirements — Windows/Render no traen tzdata del sistema).

**Integridad del PDF (honesto, no cosmético):** el sello "REGISTRO DE FIRMA" en el PDF
NO es una firma electrónica avanzada (PKI) — es un registro de trazabilidad (ID, fecha/hora,
IP). La integridad real se ancla FUERA del PDF: al generarlo, el servidor calcula el SHA-256
de sus bytes reales (`consentimientos.hash_pdf()`) y lo guarda en `pdf_sha256` del registro.
Para verificar que un PDF no fue adulterado, se recalcula su hash y se compara con ese valor
guardado server-side — un PDF editado nunca podría "auto-corregir" su propio hash impreso.

**Google Drive (respaldo):** cuenta de servicio `claude@intrepid-charge-501115-n0.iam.
gserviceaccount.com`, **Unidad compartida** (Shared Drive) ID `0AKiV1nLsqi2dUk9PVA`.
⚠️ Debe ser Unidad compartida, NO carpeta de "Mi unidad" — las cuentas de servicio no
tienen cuota propia (error `storageQuotaExceeded`). Scope `drive` completo (no `drive.file`).
En Render: env var `GOOGLE_SERVICE_ACCOUNT_JSON` = JSON entero (drive_backup.py lo soporta).
El panel tiene botón **"Abrir en Drive"** (usa `drive_file_id`, abre `drive.google.com/file/d/<id>/view`).

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

## Seguros Complementarios — formularios de reembolso (2026-07-09)

Sistema para que la secretaria rellene y envíe por email el formulario de reembolso del
seguro complementario del paciente, integrado al asistente F2. Aseguradoras objetivo:
Zurich, MetLife, BUPA, Colmena, Vida Cámara, Bice Vida, Consorcio. Los PDFs fuente
(formularios + aranceles) están en `C:\Users\ESTUDIO3D\Claude Code Playground\SEGUROS
COMPLEMENTARIOS\` (FORMULARIOS/ y ARANCELES/; faltan aranceles de BUPA y Bice Vida).

**Flujo:** botón "🛡️ Seguro complementario" en F2 (`dentidesk-assistant/content.js`,
`abrirSeguro()`) → `window.open` a `/seguro?rut=…&nombre=…&motivo=…` (SIN token en la
URL) → `admin/seguros_secretaria.html` (pide la clave admin una vez, la guarda en
`localStorage['stats_token']`, mismo patrón que el panel) → precarga paciente
(`pacientes.lookup`) + última aseguradora usada + prestaciones sugeridas por motivo →
la secretaria ajusta filas/valores → vista previa PDF en iframe → enviar por email
(adjunto + Cc recepción, `notify.enviar_formulario_seguro`). WhatsApp = fase futura
(falta plantilla Meta aprobada en la WABA real).

**Módulo:** `admin/seguros.py` (molde de consentimientos.py). Datos en JSON en el disco
persistente (rutas junto a `PATIENT_INDEX_PATH`): `seguros_aseguradoras.json` (mapeo de
campos por aseguradora), `seguros_prestaciones.json` (arancel interno), `seguros_mapeo_
prestaciones.json` (prestación→[{codigo,descripcion}] por aseguradora, 1→N),
`seguros_mapeo_motivos.json` (motivo→prestaciones sugeridas), `seguros_pacientes.json`
(por RUT: última aseguradora + datos extra fecha_nacimiento/dirección),
`seguros_firmas.json` + `seguros_firmas/` (imagen firma+timbre, rut y especialidad POR
DOCTOR — no viven en otro lado), `seguros_registro.json` (historial generado→enviado),
`seguros_plantillas/` (PDFs oficiales), `seguros_generados/` (PDFs rellenados).

**Rellenado de PDF (`seguros.rellenar_pdf`, requiere `pypdf`):** cada aseguradora tiene
`mapeo_campos` {campo_lógico → spec o LISTA de specs}: `{'campo': 'NombreAcroForm'}`
(campos de formulario, pypdf `update_page_form_field_values` + NeedAppearances) o
`{'pagina','x','y','fontsize'}` (overlay reportlab fusionado con pypdf; coordenadas PDF
origen abajo-izquierda). `firma_doctor` SIEMPRE por coordenadas (imagen). CLAVE: el
merge final usa `writer.append(reader)` (no add_page) para no perder el /AcroForm.
Sin plantilla mapeada → cae a un PDF genérico propio (reportlab), el flujo nunca se
bloquea. Campos lógicos: paciente_*(nombre, rut_fmt, email, telefono, fecha_nacimiento,
direccion), fecha_emision/fecha_atencion, doctor_*(nombre, rut, especialidad),
clinica_*(nombre, telefono, email, direccion), tratamiento_indicado,
prestacion_{N}_{codigo,descripcion,fecha,valor,cantidad}, total.

**Semilla versionada** `admin/seguros_seed/` (aseguradoras_seed.json + PDFs): se copia
al disco persistente en el primer arranque (`seguros._aplicar_seed()`); después manda
lo del disco. **Mapeadas y verificadas visualmente: Zurich (AcroForm, 130 campos — el
formulario es de Chilena Consolidada/grupo Zurich) y Colmena (overlay por coordenadas,
tabla de 5 filas en pág. 2).** Pendientes: MetLife y BUPA (tienen AcroForm — mapear con
`get_fields()`), Bice Vida/Consorcio/Vida Cámara (planos → overlay, mismo método:
renderizar con pymupdf local, buscar etiquetas con `page.search_for`, iterar).
Los aranceles de ARANCELES/ aún NO se han volcado a `mapeo_prestaciones` (pendiente).

**Endpoints** (`server.py`, bloque "SEGUROS COMPLEMENTARIOS", todos ADMIN_TOKEN salvo
los marcados): `GET /seguro` (página), `/api/seguro/init|precarga|prestaciones`,
`POST /api/seguro/paciente|previsualizar|enviar`, `GET /api/seguro/pdf?token=` (SIN
header: token itsdangerous de 4h propio, porque el iframe no manda headers; secreto
`SEGUROS_SECRET` con fallback a `CONSENT_SECRET`). Admin: `/api/seguro/admin/
aseguradoras|aseguradora/plantilla|aseguradora/campos-acroform|prestaciones|
prestaciones/seed-desde-motivos|mapeo-prestaciones|mapeo-motivos|firma|historial`.
Panel: pestaña "Seguros" en panel.html (patrón remoto, mismas claves localStorage
`stats_url`/`stats_token`).

**Probar local sin producción:** lanzar `server.py` con `PATIENT_INDEX_PATH` apuntando
a una carpeta de prueba + `DENTIDESK_ENABLED=false` (el módulo no usa DentiDesk; los
datos llegan del F2 por query params). El envío real de email requiere SMTP_USER/PASS
(solo Render). Para verificar PDFs visualmente: pymupdf (`fitz`) instalado local (NO va
en requirements.txt, es solo herramienta de desarrollo).

**Envío 1-clic desde la boleta (2026-07-10):** flujo principal de producción. La
secretaria emite la boleta DTE en DentiDesk → F2 → sección colapsable "🛡️ Seguro
complementario" → botón **"📤 Enviar formulario de <aseguradora>"** (nombre = última
aseguradora del paciente; deshabilitado si no tiene → usar "🔁 Elegir/cambiar
aseguradora…", que abre la página web). Al apretarlo: (1) `content.js
buscarBoletaDelDia()` lee las boletas del mes con la SESIÓN del navegador — `POST
/ajax/ajaxConfigIntegracionSii.php` body `accion=sii_consultar_dtes_emitidos&mes=<M>`,
respuesta `resultado.dte_emitidos` (claves MAYÚSCULAS: DESCRIPCION, MONTO, SII_FOLIO,
RUT, FECHA_EMISION, TIPO_DOCUMENTO, ID), paginado 15/página pero la recién emitida
siempre viene en la 1 — filtra por RUT + fecha, excluye "Nota", toma ID más alto;
(2) `POST /api/seguro/preparar-desde-boleta` interpreta la glosa (`seguros.
interpretar_glosa`: alias `glosas_boleta` por prestación, sin tildes/case) y reparte
valores (`filas_desde_boleta`: cada prestación su arancel interno; la que tenga
`absorbe_saldo` — el control/mensualidad — vale total_boleta − resto, modelo de cobro
del usuario); (3) el F2 muestra mini-confirmación (filas+total+email) y al confirmar
(4) `POST /api/seguro/enviar-desde-boleta` genera el PDF oficial y lo emailea (acepta
`doctor` como TEXTO del modal, lo resuelve contra professional_name). Los errores
guían: sin aseguradora (409), glosa sin match (422 → configurar alias en el panel o
usar la página). Los alias y absorbe_saldo se administran en la pestaña Seguros del
panel (card Prestaciones). background.js ganó el mensaje genérico `SEGURO_API`.
**Convención de glosa:** emitir las boletas DESGLOSADAS (ej. "CONTROL MENSUAL SEPT +
RECEMENTACION BRACKET") para que el sistema detecte cada acción — decisión del usuario
2026-07-10.

**Auto-envío (vigilante de boletas, 2026-07-10):** sin que nadie toque el F2. El
content.js corre un `setInterval` de 60s (ventana 08:50–20:00, `tickVigilante`) que lee
los DTE del mes con la sesión del navegador y, por cada boleta NUEVA de hoy (folio no
visto, no nota de crédito), llama `POST /api/seguro/auto-desde-boleta`. Ese endpoint
resuelve TODO server-side desde el RUT (email/nombre de `pacientes.lookup`, doctor del
`doctor_default` de `seguros.get_auto_config`): si es LIMPIO (aseguradora asignada +
glosa reconocida + email válido) genera el PDF y lo envía; si no, `notify.
avisar_recepcion_seguro_no_enviado` manda un correo a la clínica (SMTP_USER) y la boleta
queda para el botón manual del F2. Doble anti-duplicado: folios vistos en
`chrome.storage.local['ddAutoFolios']` (extensión) + `seguros.folio_ya_enviado(folio)`
(backend, por registro con estado enviado). Toggle en el panel ⚙ del F2
(`ddAutoSeguro`, OFF por defecto). Config del auto (activo + doctor_default) también por
`GET/POST /api/seguro/auto-config`. **Requisito operativo:** un navegador con DentiDesk
abierto y sesión iniciada (idealmente el usuario dedicado de recepción); si el PC está
apagado no vigila (pero tampoco se emiten boletas). **Login server-side NO es viable:**
la página de login de DentiDesk tiene reCAPTCHA (verificado 2026-07-10) — por eso la
vigilancia vive en la extensión con sesión ya iniciada, no en Render. **Costo: nulo** —
el sondeo pega a DentiDesk (app ya pagada, misma llamada AJAX de la página), Render solo
se activa ante boleta nueva, interpretación determinista sin API de IA. El registro
distingue `origen` = manual | boleta | auto.

**Pendientes:** mapear MetLife/BUPA/Vida Cámara/Bice Vida/Consorcio; poblar
`mapeo_prestaciones` desde los aranceles (análisis listo en `SEGUROS COMPLEMENTARIOS\
ANALISIS - Desglose control mensual (...).md`); configurar alias de glosa +
absorbe_saldo de las prestaciones reales en el panel; subir firmas reales de los
doctores (imagen + RUT + especialidad, vía pestaña Seguros del panel); plantilla
WhatsApp `seguro_complementario` en la WABA REAL 106738482086473; probar envío de
email real en producción con el paciente de prueba (Alberto, RUT 17.406.985-9);
verificar cómo viene DESCRIPCION cuando la boleta tiene varias líneas de detalle.

---

## Recordatorio de control — recaptación desde F2 (2026-07-21)

Aviso por WhatsApp a pacientes que dejaron de venir, para que agenden su próximo control.
Lo dispara **a mano la asistente dental**, no un scheduler: abre en DentiDesk la cita de la
**última atención** del paciente, aprieta **F2**, y el panel muestra
**"🔔 Recordatorio de control {doctor}"** (etiqueta dinámica: en el PC de la asistente del
Dr. Vial se lee "Recordatorio de control Dr. Vial"). El criterio de a quién le corresponde
control sigue siendo humano.

**Reemplazó a un flujo manual de 3 pasos** (la asistente armaba un Google Sheet
`PACIENTES POR LLAMAR PV` → se lo pasaba a la secretaria → esta mandaba los WhatsApp uno a
uno). Se evaluó automatizar la lectura de ese Sheet con la cuenta de servicio de Drive y
**se descartó a propósito**: el modal de la cita abierta ya trae los tres datos que el
mensaje necesita (nombre, doctor, fecha del último control), así que la planilla sobra y con
ella desaparecen el copiado manual y los celulares mal tipeados. **No revivir el Sheet.**

**Datos:** todos salen de la cita abierta. `content.js leerCita()` ya devuelve
`idAgenda/fecha/doctor/nombre/rut` — no hubo que leer nada nuevo del DOM. El **teléfono** NO
está en el modal: el backend lo saca FRESCO de `getAgendaDay(fecha, force=True)` ubicando la
cita por `IdAgenda` (mismo truco que `asistente_confirmar_cita`).

**Las 3 guardas (`recaptacion.evaluar`)** — son el valor real del sistema, en este orden:
1. `no_molestar` — RUT marcado a mano desde el F2 (botón "🚫 No volver a recordar", reemplaza
   la nota suelta "NO INSISTIR AL PACIENTE" que vivía en la planilla). **Nunca se salta.**
2. `ya_tiene_hora` — `dentidesk.citas_futuras_paciente(rut)` devuelve cita activa futura.
   Detalle en texto legible ("ya tiene hora agendada el sábado 8 de agosto…"). Forzable.
3. `enviado_reciente` — dentro de `dias_minimos_reenvio` (default **90**, editable en el panel).
   Forzable.
Las forzables devuelven **409** con `motivo`/`detalle`/`puede_forzar`; el F2 lo muestra como
advertencia (no error rojo) con un botón **"Enviar igual"** que reintenta con `forzar:true`.

**A QUIÉN se le manda (importante para el texto):** pacientes **del Dr. Vial**, que es
**rehabilitador oral e implantólogo**, NO ortodoncista. Son pacientes de operatoria,
rehabilitación e implantes que ya terminaron su tratamiento y a los que les corresponde el
control periódico. Por eso el mensaje habla de "control de seguimiento" y de que las
alteraciones no dan molestias al comienzo (es la objeción real: el paciente se siente bien).
⚠️ **Si algún día se usa con los tres ortodoncistas, el texto NO sirve** — a esos pacientes
les corresponde control de RETENCIÓN (retenedores que se sueltan, dientes que se mueven), que
es otro mensaje y por lo tanto **otra plantilla en Meta** + un selector en el F2. El código ya
soporta cualquier doctor (la etiqueta del botón se arma sola con `cita.doctor`); lo que está
casado con Vial es el texto de la plantilla.

**Largo del cuerpo — el "Leer más":** WhatsApp colapsa el mensaje pasado cierto largo que
Meta no publica. Medido en vivo (2026-07-21): un cuerpo de **453 caracteres enviados SE
TRUNCA**; **380 se ve completo** (probado con mensajes de texto libre de largo creciente,
botón "🔬 Buscar el límite" en el panel → `/api/whatsapp/test-texto-libre`). El resto de las
plantillas del proyecto va de 139 a 208 crudos. **Regla práctica: quedarse bajo ~320
enviados.** Ojo con la unidad: `/api/whatsapp/plantillas` mide el cuerpo CRUDO (con los
`{{n}}` sin reemplazar) y los valores reales suman ~35 caracteres más.

**Texto aprobado (275 crudo / 310 enviado):**
```
Estimado/a {{1}},

Según lo planificado en su tratamiento dental con el {{2}}, ha llegado el momento de
agendar un control de seguimiento. Su última atención fue el {{3}}.

Estos controles detectan a tiempo lo que aún no da molestias.

Puede agendar con los botones de abajo.
```
Criterios de redacción, por si hay que reescribirlo: "según lo planificado" ancla en el plan
de tratamiento que el paciente sí acordó (no depende de que recuerde una conversación);
la fecha de la última atención va en frase aparte porque es lo que prueba que hay una ficha
real detrás y no un envío masivo; y el cierre de cortesía se omite porque el pie de página
ya firma la clínica.

**Plantilla Meta `recordatorio_control_dr_vial`** (es_CL, categoría **Utility** solicitada,
**Meta la reclasificó a MARKETING** — sin cambio de código, pero implica tope de frecuencia
por usuario (Meta puede NO entregar el mensaje aunque la API responda "aceptado") y más peso
en la calidad del número si la gente bloquea → mandar en tandas chicas), `{{1}}`=nombre
`{{2}}`=doctor `{{3}}`=fecha legible larga ("martes 1 de abril del 2025"). **Meta SÍ acepta
los 3 botones de tipos MEZCLADOS** (verificado 2026-07-21). Orden real con que quedó creada:
**0 = "Agendar Online"** (URL → `ortodonciarichard.cl/#agendar`), **1 = "Llamar por teléfono"**
(+56 2 2217 3499), **2 = "Agendar por WhatsApp"** (quick-reply). Solo el quick-reply acepta
payload (`control:{id_agenda}:{fecha}`) → por eso `_enviar_plantilla()` ganó el parámetro
`boton_indices` (índices CONCRETOS, en vez de asumir que `0..num_botones-1` son todos
quick-reply) y `wa_cloud.IDX_BOTON_AGENDAR_WA = 2` marca su posición. ⚠️ **Si algún día se
reordenan los botones al editar la plantilla, hay que ajustar ese índice** — si no, el
payload se le pone al botón equivocado y el webhook no sabe de qué paciente vino el toque.
Mientras Meta no la apruebe, `notify.enviar_recordatorio_control()` cae a
`conversacion_general` (mismo fallback que usó `consentimiento_informado`).

**Webhook:** `ACCION_AGENDAR_WA = 'Agendar por WhatsApp'` → `webhook_wa._agendar_por_whatsapp()`
responde texto libre (link de agenda + ofrecer coordinar por ahí mismo), marca `respondio`
en el registro y **avisa a recepción por email** (`notify.avisar_recepcion_interes_control`)
— ese aviso es lo que hace que alguien conteste. **No toca DentiDesk** (no hay cita que
actualizar). Los botones URL y de teléfono NO generan evento de webhook.

**Archivos:** `admin/recaptacion.py` (config + registro + guardas, molde `recordatorios_wa.py`;
`recaptacion_config.json` / `recaptacion_registro.json` en el disco persistente vía
`PATIENT_INDEX_PATH`), y cambios en `wa_cloud.py`, `notify.py`, `webhook_wa.py`, `server.py`,
`admin/panel.html` (card "Recordatorios de control" en la pestaña WhatsApp: días mínimos,
historial con marca de quién respondió, lista de no molestar) y `dentidesk-assistant/`
(`content.js` + handler genérico `ASISTENTE_API` en `background.js`, que a diferencia de
`SEGURO_API` devuelve también el `status` HTTP porque el 409 es un caso de negocio).

**Endpoints:** `POST /api/asistente/recordatorio-control` `{id_agenda, fecha, forzar?}` (F2),
`GET/POST /api/recaptacion/config`, `GET /api/recaptacion/historial` (devuelve `envios` +
`no_molestar` juntos, una sola llamada para la card), `POST /api/recaptacion/no-molestar`
`{rut, quitar?}`. Todos con `ADMIN_TOKEN`.

**Verificado en vivo (2026-07-21):** plantilla creada y APROBADA en la WABA real; Meta SÍ
acepta la mezcla quick-reply + CTA; envío a Alberto OK; toque de "Agendar por WhatsApp" →
respuesta al paciente + correo a recepción, ambos confirmados. Falta solo reeditar el cuerpo
con el texto definitivo de arriba (la primera versión salía con "Leer más" por larga).

**Herramientas de diagnóstico que quedaron en el panel** (pestaña WhatsApp, card "🧪 Envío de
prueba"): envío de una plantilla suelta a un teléfono (`/api/whatsapp/test`, ahora incluye
`recordatorio_control_dr_vial`), listado de todas las plantillas con estado/categoría/largo
del cuerpo (`/api/whatsapp/plantillas`), y la sonda del "Leer más". Antes el endpoint de
prueba solo se podía llamar por API teniendo el ADMIN_TOKEN a mano.

**Pendientes:** cargar la extensión actualizada en el PC de la asistente dental (los cambios
de `content.js`/`background.js` NO viajan por Render — hay que copiar la carpeta y recargar
la extensión, con el ADMIN_TOKEN en `config.js`); definir con la clínica el ritmo de envío
(tandas chicas, con alguien disponible para contestar los que respondan).

---

## Compras / Gastos / Stock — app online multiusuario (Fases 1 y 2 COMPLETAS, 2026-07-08)

Sistema para llevar el registro de compras y gastos con seguimiento de stock. App web
propia, servida por el MISMO backend Flask de Render, con login y roles propios (NO usa
el ADMIN_TOKEN del resto del sitio). Datos en **SQLite** en el disco persistente (a
diferencia de los .json del resto del proyecto: aquí hay relaciones reales
compras↔ítems↔productos↔proveedores↔movimientos que justifican una base de datos).

**Acceso:** `https://ortodonciarichard.onrender.com/compras` (login-gated). En local:
`http://localhost:5001/compras`. El frontend es del MISMO origen que la API (sin CORS).

**Roles y permisos (modelo por CAPACIDADES, 2026-07-09):** ya NO es una escala lineal
—los roles nuevos no son subconjuntos limpios. `compras.CAPS` mapea rol→capacidades
(`escanear, stock, compras_ver, reportes, solicitar, registrar, admin`):
- `admin` — todas.
- `registro` — todas menos `admin`.
- `solicitante` — `escanear, stock, compras_ver, solicitar` (ve, escanea salidas, pide compras; NO registra compras).
- `lectura` — `stock, compras_ver, reportes` (solo ver).
- `escaner` — SOLO `escanear` (solo la pestaña Escanear salida; se abre directo ahí).
El backend protege cada endpoint con `_require_compras(cap)`; el frontend muestra/oculta
pestañas según `ME.caps` (mapa `TAB_CAP`). Login por usuario → token de sesión (30 días)
en header `X-Compras-Token`. Contraseñas con PBKDF2-HMAC-SHA256 (200k iter, salt).

### Solicitudes de compra (pendientes + sugerencias por consumo, 2026-07-09)
Pestaña **🛒 Solicitudes** (rol `solicitar`: solicitante/registro/admin). Tabla
`pendientes_compra` (una fila por producto pendiente; upsert si ya existía uno activo).
- **Armar solicitud:** buscador de productos; al agregar uno, el sistema **sugiere una
  cantidad** (`GET /solicitudes/sugerir?producto_id=`). La solicitud crea/actualiza los
  pendientes y **avisa por email a los admins** (`_notificar_solicitud_admins` →
  `notify._enviar_email_recepcion`, best-effort si hay SMTP).
- **Sugerencias del sistema** (`GET /solicitudes/sugerencias`): productos a comprar por
  stock bajo el mínimo o por proyección de quiebre (días de stock restantes < media
  cobertura). Excluye los que ya están pendientes. Ordenado por urgencia.
- **Fórmula de consumo** (`compras.consumo_diario`): rate = total de SALIDAS de stock en
  los últimos 90 días / días transcurridos. `sugerir_cantidad(prod, cobertura=60)` =
  ceil(rate×cobertura − stock). Sin salidas → cae a la última compra o al doble del mínimo.
- **Auto-resolución:** al registrar una compra, `_resolver_pendientes` (dentro de la
  transacción de `crear_compra`) marca `comprado` los pendientes de los productos comprados
  → salen de la lista. Badge de pendientes en la pestaña (contador desde `me`/`_con_caps`).
- Endpoints: `GET/POST /api/compras/solicitudes`, `GET .../sugerencias`, `GET .../sugerir`,
  `POST .../cancelar`.

### Cargos recurrentes con generación automática mensual (2026-07-09)
Antes, "recurrente" era solo una etiqueta de `tipo_gasto` — cada mes había que ingresar
la compra a mano. Ahora hay una tabla **plantilla** `suscripciones` (nombre, monto,
moneda/tipo_cambio, proveedor, categoría, forma de pago, **día del mes de cobro**,
`fecha_inicio`, `fecha_fin` NULL=indefinido, `activa`, `ultima_generada` YYYY-MM) y un
**barrido diario** que genera sola la `compra` del mes cuando corresponde.
- **Crear (`crear_suscripcion`):** si al crearla el día de hoy YA alcanzó el día de
  cobro de este mes, genera de inmediato la primera compra (no espera al barrido de
  mañana); si no, la genera el barrido cuando llegue el día. Guarda `compras.suscripcion_id`
  para enlazar de vuelta ("🔁 generado automático" en el detalle).
- **Día ajustado a meses cortos (`_dia_ajustado`):** día 31 en un mes de 30/28/29 días
  cobra el último día real de ese mes (usa `calendar.monthrange`).
- **Barrido diario (`generar_recurrentes_pendientes`, thread `_loop_recurrentes` en
  `server.py`, 09:00 hora Chile, independiente de si DentiDesk está habilitado):** para
  cada suscripción `activa=1`, si `ultima_generada` != mes actual y ya llegó el día de
  cobro (ajustado), genera la compra y marca `ultima_generada`. Anti-duplicado: nunca
  genera dos veces el mismo mes para la misma suscripción. Si `fecha_fin` ya se pasó, se
  auto-desactiva sin generar más.
- **Cortar (`cortar_suscripcion` / botón en la pestaña):** pone `activa=0` y fija
  `fecha_fin` (hoy por defecto, o una fecha elegida) — el barrido deja de generarla desde
  ese momento. El historial de compras ya generadas NO se toca.
- **Editar:** solo mientras está activa (`actualizar_suscripcion` rechaza editar una ya
  cortada — evita reabrir algo que se cortó a propósito; para eso hay que crear una
  suscripción nueva).
- **Frontend:** en «Nueva compra», al elegir tipo de gasto **Recurrente** con productos
  vacíos (gasto sin ítems), aparecen los campos nuevos: nombre del cargo, día del mes,
  e Indefinido/Hasta fecha. Al guardar, llama a `POST /api/compras/suscripciones` (NO al
  endpoint normal de compras). Nueva pestaña **🔁 Recurrentes** (cap `registrar`): lista
  con próximo cobro calculado (`_proxima_cobranza`), botones Editar/Cortar.
- Endpoints: `GET/POST /api/compras/suscripciones`, `POST .../actualizar`, `POST .../cortar`.
- Verificado en vivo end-to-end: creación con cobro inmediato, listado con próxima
  cobranza correcta, corte (desaparece el próximo cobro, queda "cortado" con fecha),
  y en pruebas de unidad: anti-duplicado en el mismo mes, generación al pasar de mes,
  ajuste de mes corto (31→28 feb), bloqueo de edición tras cortar, USD con tipo de cambio.

### Archivos
```
admin/compras.py     ← capa de datos SQLite (esquema, CRUD, transacciones, reportes).
                       Autocontenido, mismo patrón que stats.py/consentimientos.py.
admin/compras.html   ← SPA (login/setup + 6 pestañas), paleta navy/gold, vanilla JS.
admin/compras.js     ← toda la lógica del frontend (buscadores, modales, escaneo).
admin/print_agent.py ← agente de impresión de etiquetas (corre en el PC de la clínica).
admin/server.py      ← 37 rutas /api/compras/* + sirve /compras y /compras.js.
```
Rutas en `server.py`: bloque "COMPRAS / GASTOS / STOCK". NO están en `RUTAS_SOLO_LOCAL`
(funcionan en producción). `/compras` y `/compras.js` se sirven también en Render.

### Modelo de datos (SQLite, WAL, foreign_keys ON)
`usuarios, sesiones, categorias, proveedores, productos, codigos_producto, compras,
compra_items, movimientos_stock, cola_impresion`. El stock se lleva como columna
denormalizada `productos.stock_actual` + libro mayor `movimientos_stock` (entrada/salida/
ajuste). Cada compra suma stock (movimiento 'entrada' por ítem, transaccional).
Hay **migraciones idempotentes** en `_migrar()` (init_db): agregan columnas nuevas a
bases ya creadas sin perder datos (ALTER TABLE si falta la columna). Ya migradas:
`productos.marca`, `compra_items.marca`, y en `compras`: `moneda, tipo_cambio,
costo_despacho, costo_importacion, total_clp` (con backfill `total_clp=total` para filas
CLP viejas). Si se agregan columnas futuras, sumarlas a `_migrar()`.

### Marca por compra, moneda/USD, despacho e importación (2026-07-09)
- **Marca variable, mismo producto:** un producto (ej. "Guantes M") es ÚNICO (stock e
  historial únicos), pero cada compra guarda con qué **marca** vino (`compra_items.marca`).
  Se prellena la última marca (`productos.marca`) al agregar el ítem. Se ve en el detalle,
  Stock, historial de precios y Excel.
- **Tipo de gasto `recurrente`** (además de fijo/variable): suscripciones mensuales
  (Google Workspace, Render). Su propio color, filtro y tile en Reportes.
- **Compras en dólares:** `moneda` CLP|USD + `tipo_cambio` (CLP por USD, obligatorio si
  USD). Ítems y despacho van en la moneda de la compra; `costo_importacion` SIEMPRE en CLP
  (boleta del courier/aduana). `total` = ítems+despacho (en moneda); `total_clp` =
  total×tipo_cambio + importación. **Los reportes suman `total_clp`** (CASE que cae a
  `total` en filas viejas) para mezclar CLP+USD correcto en un solo informe.
- **Editar compra (`actualizar_compra` / `POST /api/compras/compras/actualizar`, rol
  registro+):** edita la cabecera (NO los ítems) y recalcula total/total_clp desde los
  ítems existentes + costos nuevos. Uso clave: **agregar el costo de importación que llega
  después** (FedEx/DHL). Botón "✏️ Editar costos" en el detalle de la compra.
- **Escáner — auto-descuento sin Enter:** la salida de stock detecta un escáner por la
  VELOCIDAD ENTRE TECLAS (ráfaga: 6+ chars con gap máx <35ms → procesa solo). Tipeo manual
  (gaps grandes) NUNCA auto-envía: exige Enter (evita el bug de "envía solo a los 4 dígitos").
  Un lector USB normal manda Enter automático igual, así que basta escanear.

### Funcionalidades (Fase 1 + Fase 2)
- **Nueva compra multi-ítem**: fecha, proveedor (buscador + crear al vuelo), tipo/nro doc,
  forma de pago, tipo (fijo/variable), categoría, foto factura, N productos con buscador
  (crear al vuelo en modal), cantidad y precio → subtotales y total automáticos.
- **Gasto SIN productos** (arriendo/luz/servicios): monto directo, no toca stock. En
  `crear_compra`, si `items` viene vacío usa `cabecera.total` (rama sin ítems).
- **Foto de factura/boleta**: se sube (downscale client-side a ≤1600px/JPEG para no pasar
  el `MAX_CONTENT_LENGTH` de 3MB) y se guarda como respaldo. **OCR = Fase 3** (enchufable:
  la decisión Gemini free-tier vs Claude API quedó PENDIENTE a propósito, no bloquea nada).
- **Productos/Stock**: lista con stock (coloreado), última compra (fecha/proveedor/precio),
  historial de precios (barras), movimientos, alerta bajo mínimo. Sacar del stock (botón ➖).
- **Escaneo (barras + QR)**: usa la **API nativa `BarcodeDetector`** del navegador
  (Chrome/Android, sin librería ni CDN) + lector USB (escribe en el campo + Enter). Un
  código de CUALQUIER origen (barras/QR fabricante o propio) resuelve al producto —
  **mapeo-al-primer-escaneo**: si el código no existe, ofrece asociarlo a un producto (o
  crear uno) y de ahí en adelante lo reconoce solo. Escanear = salida de stock.
- **Códigos propios**: para productos sin código, `generar_codigo_propio` crea `OR-<id>-<hex>`
  y lo encola para imprimir su etiqueta.
- **Reportes**: tiles (total, n° compras, fijos, variables), barras por mes/categoría/
  proveedor, filtro por fechas, **export a Excel** (openpyxl, una fila por ítem).
- **Historial** de compras con filtros; ver detalle (ítems + foto adjunta); eliminar (admin)
  **revierte el stock** (movimiento 'ajuste', y desacopla los movimientos de la compra
  antes de borrar — si no, la FK `movimientos_stock.compra_id` impide el DELETE).
- **Admin**: categorías (crear/archivar), proveedores (CRUD), usuarios (crear/editar rol/
  estado/password).

### Agente de impresión de etiquetas (`print_agent.py`)
Corre en el **PC siempre-encendido de la clínica** (el del bridge de WhatsApp), con la
etiquetadora térmica USB. Mismo patrón cola+polling que la tablet de consentimientos: el
PC pregunta hacia afuera (sin abrir puertos ni IP fija). Flujo: la app encola etiqueta →
`cola_impresion` → el agente hace polling a `/api/compras/impresion/cola` (auth por
`X-Print-Token` = env `PRINT_TOKEN`) → genera la etiqueta (QR con **segno** + nombre +
código con Pillow) → la imprime (pywin32/win32print, respeta el driver de la térmica) →
`marcar` impreso. Dependencias SOLO en el PC de la clínica (`requests segno pillow pywin32`),
NO en Render. Modos: `--test` (etiqueta de ejemplo a archivo), `--guardar` (PNG en vez de
imprimir), sin flags (loop imprimiendo). Verificado: la etiqueta de ejemplo genera QR
escaneable + texto correctos.

### Config en Render (variables de entorno)
- `COMPRAS_SEED_EMAIL` + `COMPRAS_SEED_PASSWORD` (+ `COMPRAS_SEED_NOMBRE`): siembran el
  PRIMER usuario admin al arrancar si no hay usuarios (evita exponer `/setup` público). Si
  no se setean, la primera visita a `/compras` muestra la pantalla "Configuración inicial"
  que crea el primer admin (solo funciona con 0 usuarios).
- `PRINT_TOKEN`: mismo valor en Render y en el agente del PC (auth del agente).
- `COMPRAS_DB_PATH` y `COMPRAS_FOTOS_DIR`: por defecto caen junto a `PATIENT_INDEX_PATH`
  (disco persistente de Render), así que normalmente NO hay que setearlas.
- `segno==1.6.1` agregado a `requirements.txt` (QR en el backend). SQLite/openpyxl ya estaban.

### Estado y pendientes
- **Fases 1 y 2: COMPLETAS y verificadas end-to-end** (setup, login, roles, compra
  multi-ítem, gasto sin productos, stock+alertas, escaneo/salida, historial, reportes+Excel,
  admin, QR, etiqueta). Falta desplegar en Render (setear las env vars de arriba) y probar
  el agente contra la térmica física.
- **Fase 3 (futura)**: foto/PDF/XML → formulario. Decisión OCR PENDIENTE (Gemini free-tier
  gratis con caveat de datos, vs Claude API privado ~$10-20 CLP/factura). Para facturas
  electrónicas conviene priorizar el **XML del SII** (estructurado, gratis, sin OCR).
  Además: gastos fijos recurrentes (plantillas mensuales).
- **Fase 4 (futura)**: alertas de stock bajo por WhatsApp (reusar Cloud API ya montada),
  comparador de precios (avisar si un proveedor subió un ítem), lectura de XML del SII.

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
- **Fase 1 — Auditar app Meta:** ✅ HECHA. Verificación del negocio aprobada.
- **Fase 2 — Plantillas (templates):** casi completa. Idioma **Spanish (CHL) = `es_CL`**, todas
  categoría **Utilidad**, pie de página fijo "Clínica de Ortodoncia C. Richard", trato
  "Estimado(a) {nombre}".
  - `conversacion_general` ✅ Aprobada — abridora flexible ({{1}}=nombre, {{2}}=motivo libre). Botón [Sí, díganme].
  - `confirmacion_hora` ✅ Aprobada — {{1}}=nombre {{2}}=doctor {{3}}=fecha {{4}}=hora, sin botones.
  - `recordatorio_semana` ✅ Aprobada — botones [Confirmo][Reagendar][Anular].
  - `recordatorio_dia` ✅ Aprobada — botones [Confirmo][Reagendar][Anular].
  - `inasistencia_reagendar` ✅ Aprobada — botón [Reagendar].
  - `primera_consulta` ✅ Aprobada — encabezado VIDEO + botones [Confirmo][Reagendar]. El video de
    encabezado se subió manual (el usuario lo hizo — Chrome MCP no puede subir archivos no
    compartidos con la sesión). **wa_cloud.enviar_primera_consulta() necesita un video_url PUBLICO
    para enviar en runtime** (el archivo subido a Meta fue solo la muestra de aprobación) — pendiente
    alojar el video en el sitio y pasar esa URL.
  - `consentimiento_informado` ⏳ **Enviada a revisión el 2026-07-01** (sin `/loop` de vigilancia —
    revisar el estado a mano en el Administrador de WhatsApp cuando se retome). {{1}}=nombre
    {{2}}=tipo_label (ej. "Consentimiento Informado — Tratamiento de Ortodoncia") {{3}}=link, sin
    botones. Mientras no esté aprobada, `notify._enviar_whatsapp_consentimiento()` cae de vuelta a
    `conversacion_general` automáticamente (fallback ya en producción, sin downtime).
  - Encabezado de ubicación (pin del mapa) PENDIENTE para confirmacion_hora y recordatorio_dia:
    falta el lat/long exacto de la clínica (el sitio usa Maps por dirección, no por coordenadas).
- **Fase 3 — Código (envío): ✅ HECHA y en producción.** `admin/wa_cloud.py` (cliente Cloud API,
  modo mock si `WA_ENABLED != true`) conectado en `_enviar_whatsapp()` de `notify.py`. Probado en
  vivo contra Render con el token de prueba — llegó WhatsApp real al celular de Alberto.
  - `notify.enviar_confirmacion(cita, cfg, canal=None)` — `canal=None` = automático (email con
    WhatsApp de respaldo, usan agendamiento online + barrido); `'email'`/`'whatsapp'` = forzado
    (lo usa el F2, donde la secretaria elige el canal a mano).
  - Endpoint `/api/whatsapp/test` (protegido por ADMIN_TOKEN) para probar envíos sueltos sin
    agendar una cita real — acepta `plantilla` = confirmacion_hora/recordatorio_semana/
    recordatorio_dia/inasistencia_reagendar.
  - `wa_cloud._post()` envuelve SIEMPRE los errores en `WhatsAppCloudError` (red, timeout, JSON
    inválido) — sin esto un error de red se escapaba sin capturar y Flask devolvía su página HTML
    de error 500 en vez de JSON, rompiendo al cliente (F2) que espera parsear la respuesta.
  - Webhook (recibir respuestas/botones del paciente) **PENDIENTE** — no bloquea producción básica,
    pero sin él nadie en el sistema se entera solo cuando el paciente toca Confirmo/Reagendar/Anular.
    Mientras tanto, recepción puede conversar libre con el paciente (dentro de la ventana de 24h)
    desde la **bandeja de Meta Business Suite** (business.facebook.com), sin necesitar código.
- **Fase 4 — Producción (número real):** registrar **+56 9 3355 8189** en la misma WABA (las
  plantillas ya aprobadas quedan disponibles automáticamente, son a nivel de WABA no de número).
  ⚠️ Al registrarlo se DESCONECTA del WhatsApp normal del celular. Generar **token permanente de
  System User** (Configuración empresarial → Usuarios del sistema) — el de 24h usado en pruebas
  cada vez que se regenera invalida el anterior (causó un 401 en una prueba — hay que tener solo
  UN token válido a la vez y que sea el que está en Render). Actualizar `WA_PHONE_NUMBER_ID` y
  `WA_TOKEN` en Render con los valores reales. Vigilar el tier/límite de mensajes (sube con uso+calidad).
- **Fase 5 — Scheduler de recordatorios + pestaña "WhatsApp" en el panel: ✅ HECHA (2026-07-03).**
  Hasta esta fase NO existía ningún disparador automático de `recordatorio_semana`,
  `recordatorio_dia` ni `inasistencia_reagendar` — solo la confirmación al agendar estaba
  automatizada. Ahora:
  - `admin/recordatorios_wa.py` (módulo nuevo, mismo patrón que `confirmaciones.py`): escanea
    DentiDesk y envía cada tipo, con registro anti-duplicados propio.
  - `scheduling.siguiente_dia_habil()` (próximo día L-V) y `scheduling.sumar_dias_habiles(d, n)`
    (d + n días hábiles). **Deliberadamente separados** de `es_habil()` (que rige la disponibilidad
    de agendamiento online). **Los recordatorios ignoran feriados** (2026-07-07, decisión del
    usuario): se envían siempre L-V aunque sea feriado — el usuario ya bloquea feriados en DentiDesk.
    El manejo de feriados (y su sección en el panel) se ELIMINÓ.
  - `wa_cloud.verificar_estado()` — chequeo en vivo contra Meta (sin enviar mensajes) para el
    indicador de estado del panel; detecta tokens vencidos como el 401 de la Fase 4.
  - **`recordatorio_semana` = 4 días hábiles antes** de la cita (2026-07-07, cambiado desde
    "exactamente 7 días"): hoy (L-V) escanea las citas de `hoy + 4 días hábiles`. Ej: martes 7-jul
    avisa las citas del lunes 13-jul; lunes 13-jul avisa las del viernes 17-jul. `recordatorio_dia`
    = próximo día hábil (salta fin de semana). Ambos SOLO envían en día hábil (si el loop cae
    sábado/domingo, no mandan — con guardia `hoy.isoweekday() >= 6`).
  - Config (`activo`/`hora` por tipo) y registro anti-duplicados viven en
    **`admin/wa_recordatorios_config.json`** / **`admin/wa_recordatorios_enviados.json`**
    (gitignored, disco persistente de Render vía `PATIENT_INDEX_PATH` — mismo mecanismo que
    `confirmaciones_enviadas.json`/`patient_index.json`). Toman efecto sin deploy.
  - `_loop_recordatorios()` en `server.py`, mismo esqueleto que `_loop_confirmaciones` (poll 40s).
  - Panel admin → pestaña **"WhatsApp"**: sigue el patrón de Estadísticas/Consentimientos (tarjeta
    "Conexión" con Backend URL + Admin Token en `localStorage`, habla DIRECTO a Render) — no el de
    "Agenda online" (que viaja por git y necesitaría push por cada cambio). Endpoints:
    `GET/POST /api/whatsapp/config`, `GET /api/whatsapp/estado`,
    `POST /api/whatsapp/recordatorios/run` (prueba manual, ignora el toggle `activo` pero respeta
    el registro anti-duplicados).
  - Probado end-to-end contra `admin/server.py` local con Flask test client + preview de
    navegador (carga, guarda, persiste, indicador de estado). Falta probar contra Render real.
- **Fase 6 — Webhook: Confirmo/Anular actualizan DentiDesk al instante (COMPLETA y verificada
  en vivo el 2026-07-06).** Antes de esta fase, tocar un botón de WhatsApp no hacía nada del
  lado del sistema. Probado dos veces end-to-end con la cita de prueba real (IdAgenda
  `13389698`, RUT 17.406.985-9): tocar "Anular" en WhatsApp pasó el `IdStatus` de `2120`
  (No confirmado) a `2122` (Hora Cancelada) automáticamente en DentiDesk, con respuesta al
  paciente y aviso a recepción.
  - `admin/dentidesk.py`: `actualizar_estado_cita(id_agenda, id_status, cfg)` — primer uso de
    `updateAgenda.php` en el proyecto (antes solo existían `createAgenda`/`getAgendaDay`/
    `getAvailableHours`). Requiere Basic Auth además del Token JWT (mismo email/password del
    login) — sin eso da 401.
  - **Tres causas reales encontradas y resueltas para que el webhook entregara eventos:**
    1. Plantillas recién creadas en la WABA quedan en "calidad pendiente" — el envío devuelve
       `message_status:"accepted"` (retenido para evaluación) en vez de entrega inmediata;
       se resuelve solo con el uso (minutos/horas, no días).
    2. La app de Meta debe estar **publicada** ("Publicar" en la barra lateral del App
       Dashboard) — mientras esté "Sin publicar", los eventos reales de webhook (toques de
       botón) nunca se entregan, solo las pruebas manuales desde el panel. Publicar exige
       Categoría, Ícono y URL de Política de Privacidad (creada en
       `https://www.ortodonciarichard.cl/privacidad.html`, enlazada desde el footer del sitio).
    3. **La causa final y más sutil:** configurar la URL/token/campos del webhook a nivel de
       App **no alcanza** — cada WABA debe tener la app suscrita explícitamente vía el edge
       `/{waba_id}/subscribed_apps` de la Graph API. Se diagnosticó con un GET a ese edge
       (devolvía `"data":[]`, vacío) y se arregló con un POST al mismo edge.
  - **Herramientas de diagnóstico agregadas a `server.py` (protegidas por `ADMIN_TOKEN`,
    se dejan a propósito para el futuro — útiles si se agrega otro número/WABA):**
    - `POST /api/whatsapp/test` — envía cualquiera de las 7 plantillas a un teléfono
      arbitrario (`{telefono, plantilla, nombre, doctor, fecha, hora, id_agenda, motivo,
      tipo_label, link}` según la plantilla).
    - `POST /api/whatsapp/test-texto-libre` — mensaje de texto libre (no plantilla), solo
      funciona dentro de la ventana de 24h; sirve para descartar problemas de plantilla vs.
      conectividad/número.
    - `GET/POST /api/whatsapp/subscribed-apps?waba_id=...` — consulta o corrige la
      suscripción de la app a una WABA (el fix de la causa #3 de arriba). Si en el futuro se
      conecta un número/WABA nuevo y el webhook "no hace nada", **este es el primer lugar a
      revisar**.
  - IDs de estado reales (diccionario oficial DentiDesk 16-06-2026, en `scheduling_config.json`
    → `dentidesk`): `id_status_confirmado_semana=40968` ("1 SEMANA Confirmado por WhatsApp",
    para Confirmo tocado desde `recordatorio_semana`), `id_status_confirmado_whatsapp=32180`
    ("Confirmado por WhatsApp", desde `recordatorio_dia`), `id_status_cancelado=2122`
    ("Hora Cancelada", Anular de cualquier origen).
  - `wa_cloud.py`: los botones quick-reply de `recordatorio_semana/dia` (3 botones) e
    `inasistencia_reagendar` (1 botón) ahora llevan un **payload propio** `"{tipo}:{id_agenda}"`
    (tipo=semana/dia/inasistencia) fijado al ENVIAR — así el webhook sabe a qué cita y de qué
    recordatorio vino el toque, sin depender del orden de los botones (la ACCIÓN se identifica
    por `button.text`, que Meta siempre manda igual al texto aprobado). Nueva función
    `enviar_texto_libre()` — primer mensaje NO-plantilla del proyecto (solo válido en la
    ventana de 24h que abre el propio toque del botón).
  - `admin/webhook_wa.py` (módulo nuevo): `procesar_evento()` despacha Confirmo/Anular/Reagendar.
    Anular avisa a recepción al instante (`notify.avisar_recepcion_anulacion`).
- **Reagendar automático vía agenda online (2026-07-06, opción "reusar la agenda online").**
  Al tocar **"Reagendar"**, el webhook (`_reagendar`) le manda al paciente un link a la agenda
  online con el id de su cita vieja codificado en el hash:
  `https://www.ortodonciarichard.cl/#reagendar=<id_agenda>`. La cita vieja **sigue vigente**
  hasta que confirme la nueva (así no queda sin hora si abandona). En el frontend
  (`js/agenda-dentidesk.js`), `_abrirDesdeHash` lee `#reagendar=<id>` → `agenda.reagendaId`, y
  `confirmarReserva` lo manda como `reagenda_id_agenda` en el POST. En el backend
  (`/api/agenda/reservar`), si viene ese id: (a) marca la cita vieja como **"Re-agendado"**
  (`id_status_reagendada=2132` en `scheduling_config.json`) vía `updateAgenda`, (b) envía la
  confirmación con `reagenda=True` → usa el texto/plantilla de reagenda en vez de la normal.
  `notify.enviar_confirmacion(..., reagenda=True)` cambia el asunto/título del email y usa la
  plantilla WhatsApp **`reagenda_confirmada`** (params: nombre, doctor, fecha nueva, hora) en
  vez de `confirmacion_hora`. **Canal:** el reagendamiento usa `canal='ambos'` (email Y
  WhatsApp, porque el paciente vino desde WhatsApp) — a diferencia de una reserva normal que
  es email-primero / WhatsApp-fallback. Si el update de la cita vieja falla, se loguea pero
  NO rompe la reserva nueva (que sí quedó hecha).
  **Pendiente:** el usuario debe crear y aprobar la plantilla `reagenda_confirmada` en Meta.
  - `server.py`: `GET/POST /api/whatsapp/webhook` — el GET es el handshake que exige Meta
    (`WA_VERIFY_TOKEN`); el POST valida `X-Hub-Signature-256` (HMAC-SHA256 con `WA_APP_SECRET`,
    **fail-closed**: sin secret configurado se rechaza todo) antes de procesar nada — esto
    puede anular citas reales, así que la firma NO es opcional.
  - `WA_APP_SECRET` y `WA_VERIFY_TOKEN` configurados en Render; webhook configurado en el
    panel de Meta (WhatsApp → Configuración → Webhook → URL
    `https://ortodonciarichard.onrender.com/api/whatsapp/webhook`, campo `messages` suscrito)
    y la app suscrita a la WABA real (ver causa #3 arriba).
  - ⚠️ **Cuidado al probar localmente:** cualquier test que pase por `webhook_wa.procesar_evento`
    o por `server.py` con `scheduling.load_config()` normal usa las credenciales reales de
    DentiDesk si están activas en `scheduling_secrets.json` local — para probar sin tocar
    producción, forzar `cfg['dentidesk']['enabled'] = False` a mano antes de llamar.

- **Reagendar con motivo/doctor PRECARGADOS y bloqueados (2026-07-08).** Antes de este cambio,
  el link de reagendar abría el wizard COMPLETO: el paciente podía terminar eligiendo un doctor
  o motivo distinto al de su cita original (a pedido del usuario, esto ya no debía pasar).
  Además el motivo original de una cita puede NO estar en la lista de motivos agendables online
  (motivos que la clínica escribe directo en DentiDesk, ej. "Instalación de microtornillos", con
  duración propia que puede variar cita a cita) — verificado en vivo: `getAgendaDay` devuelve el
  **nombre** del motivo (`Reason`) y la **duración** (`duration`), pero NUNCA el `IdReason`
  numérico que exige `createAgenda`. DentiDesk tampoco tiene endpoint de "buscar por id" (solo
  `getAgendaDay` por fecha) ni de listar motivos.
  - **Encadenado id_agenda→fecha:** el payload de los botones Confirmo/Reagendar/Anular pasó de
    `"{tipo}:{id_agenda}"` a `"{tipo}:{id_agenda}:{fecha_iso}"` (`wa_cloud.py` → `notify.py` →
    `recordatorios_wa.py`, que ya conocía la fecha al momento de enviar). `webhook_wa.py` parsea
    con `split(':')` (no `partition`) — compatible con botones viejos ya enviados (sin fecha,
    `partes[2]` queda vacío). El link pasó de `#reagendar=<id>` a `#reagendar=<id>&fecha=<fecha>`.
  - **Resolución de motivo (`dentidesk.id_reason_por_label`):** dado el nombre del motivo tal
    como lo devuelve DentiDesk, busca primero en los motivos agendables online (`cfg['motivos']`,
    ya tienen `id_reason` confirmado) y si no hay match cae a `cfg['motivos_id_reason_extra']`
    (`scheduling_config.json`, tabla plana `"Nombre exacto": IdReason`). **La tabla ya está
    COMPLETA** (186 motivos, poblada 2026-07-08 desde `motivos_consulta.txt` que entregó la
    clínica — el .txt tiene IdReason/Nombre/Duración standard; la duración NO se copió al config,
    se replica la real de cada cita). Verificado en vivo: "Control Pasivo" (motivo fuera del menú
    online) ahora resuelve a IdReason 18162. Si algún día la clínica agrega un motivo nuevo en
    DentiDesk, hay que sumarlo a esta tabla. Si no hay match en ninguna, el backend NO inventa un
    motivo — devuelve error y sugiere WhatsApp.
  - **Flujo:** `GET /api/agenda/reagendar-info?id_agenda=&fecha=` (usa `dentidesk.info_cita`,
    un solo `getAgendaDay(fecha)`) devuelve doctor+motivo+duración si se pudo resolver todo.
    El frontend (`agenda.reagendaExacto=true`) precarga doctor y motivo (de solo lectura, sin
    selector) y salta los pasos de especialidad/profesional/motivo — el paciente solo confirma
    RUT/datos y elige hora. La disponibilidad se pide por **duración** (no por motivo_key):
    `GET /api/agenda/disponibilidad-reagendar?doctor=&duracion=`, vía
    `scheduling.horas_disponibles_libre()` (gemela de `horas_disponibles()` pero sin necesitar
    un `motivo_key` de config — confirmado que `cumple_anticipacion()` nunca usó `motivo_cfg`
    igual, la anticipación mínima es global). Al confirmar, `POST /api/agenda/reservar-reagenda`
    **relee la cita vieja en vivo** (no confía en lo que juntó el frontend al abrir el link) y
    llama a `dentidesk.crear_cita(id_reason=..., duracion_min=...)` (nuevo modo, sin `motivo_key`)
    con el motivo/duración EXACTOS de la cita original.
  - **Cita vieja al reagendar (probado en vivo end-to-end 2026-07-08):** se marca "Re-agendado"
    (2132) vía `dentidesk.actualizar_estado_cita()`. Aplica al flujo nuevo (`reservar-reagenda`)
    y al viejo (`/api/agenda/reservar` con `reagenda_id_agenda`, que sigue como FALLBACK cuando
    `/reagendar-info` no logra resolver doctor/motivo → el frontend cae al wizard completo).
  - ⚠️ **LIMITACIÓN de la API descubierta en vivo (importante): `updateAgenda.php` SOLO cambia el
    IdStatus.** Probado exhaustivamente contra la cita real 13403984: NO mueve la hora (`Hour`/
    `Date` ignorados), NO cambia la duración (`Duration`/`duration`/`Minutes` ignorados) — todos
    devuelven 200 OK pero solo el estado muta. Consecuencias:
    - **No se puede "mover" la cita vieja a las 20:00** (la idea original de liberar el bloque).
      Se intentó incluso con un slot de 20:00 abierto a mano en DentiDesk: igual no mueve.
    - **El estado "Re-agendado" (2132) NO libera el bloque** en DentiDesk (verificado: 2132 →
      9:00 sigue ocupada; solo "Hora Cancelada" 2122 la libera). **Decisión del usuario
      (2026-07-08): mantener la etiqueta "Re-agendado" por sobre liberar el horario** — la cita
      vieja queda marcada pero su bloque NO se reabre para otro paciente. Si en el futuro se
      prioriza liberar el espacio, la única vía por API es marcarla "Hora Cancelada" (2122),
      perdiendo la etiqueta de reagenda (el registro del reagendamiento igual vive en la cita
      nueva). Mover/acortar solo es posible arrastrando a mano en la web de DentiDesk.
  - **Reagenda para el día siguiente → "Confirmado por WhatsApp" (2026-07-08):** si el paciente
    reagenda para el próximo día hábil (`scheduling.es_dia_siguiente_habil`, maneja fin de semana:
    viernes → lunes), la cita NUEVA nace directamente en estado "Confirmado por WhatsApp" (32180)
    en vez de "No confirmado" (2120) — viene interactuando por WhatsApp y eligió una hora
    inminente. `dentidesk.crear_cita()` acepta `id_status=` para esto. Aplica a `reservar-reagenda`
    y al fallback `/reservar` con `reagenda_id_agenda` (solo reagenda; una reserva normal para
    mañana sigue naciendo "No confirmado").
  - **Filtro de horario (`scheduling._dentro_horario`):** la agenda online NUNCA ofrece horas en
    o después del cierre (19:30). Si la clínica abre slots "de overflow" en DentiDesk (ej. 20:00
    para arrastrar citas a mano), `getAvailableHours` los devuelve como libres pero este filtro
    los saca de lo ofrecible online (pedido del usuario). Aplica a `horas_disponibles` y
    `horas_disponibles_libre`.
  - **Regla de almuerzo (`scheduling.restriccion_manana_reagenda`):** una cita de ORTODONCIA de
    60+ min agendada en la mañana (inicio < `corte_pm` 14:00) debe mantenerse en la mañana al
    reagendar (ej. Montaje/Retiro Total/Parcial). Una cita de la tarde SÍ puede pasar a la mañana.
    `reagendar-info` devuelve `solo_manana`; el frontend pasa `solo_am=1` a
    `disponibilidad-reagendar` (filtra horas < corte) y `reservar-reagenda` lo revalida server-side.
  - **Aviso de cita previa en reagenda (fix 2026-07-08):** el aviso "Ya tienes una hora agendada"
    ahora excluye la propia cita que se está reagendando (antes la mostraba, confuso). Requirió
    agregar `id_agenda` a `dentidesk.citas_futuras_paciente()` y filtrar por `reagendaId` en el
    frontend (`irAResumen`).
  - **Duración atípica (createAgenda) — pendiente:** `crear_cita(enviar_duracion=True)` agregaría
    un campo `Duration` al payload de `createAgenda` para replicar una duración atípica en la cita
    NUEVA. Está **DESACTIVADO** (`enviar_duracion=False`) porque el campo no está confirmado en
    `createAgenda` (y en `updateAgenda` se probó que NO existe). Hoy la cita nueva toma la duración
    STANDARD de su IdReason. Verificar el campo en `createAgenda` antes de activar.

### Notas clave
- Ventana de 24h: fuera de ella solo se pueden enviar PLANTILLAS (por eso siempre funcionan,
  incluso para el primer contacto — a diferencia de un mensaje libre).
- El número de prueba solo envía a destinatarios pre-registrados (máx. 5) — el de prueba es
  +56 9 8903 2888 (celular Alberto).
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
