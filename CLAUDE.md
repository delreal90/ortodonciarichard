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
admin/notify.py               ← confirmación: WhatsApp (bridge :8080) + .ics, fallback email
admin/server.py               ← rutas Flask: /api/agenda/config|disponibilidad|reservar
                                 y /api/scheduling-config (GET/POST para el panel)
js/agenda-dentidesk.js        ← modal de 4 pasos (motivo→doctor→fecha/hora→datos)
index.html                    ← botón "Agendar hora online" + markup del modal
admin/panel.html              ← sección "Agenda online" para ajustar % de ocupación
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
