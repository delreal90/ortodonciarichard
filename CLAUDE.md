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

---

## Pendientes

1. **Fotos y nombres del staff** — reemplazar placeholders de secretarias, asistentes y laboratorio
2. **Fotos adicionales de la clínica** — recepción, 8 boxes, sala diagnóstico, laboratorio, esterilización, rayos
3. **DNS en nic.cl** — agregar registros A y CNAME sin borrar registros MX del correo Gmail
4. **GitHub Pages custom domain** — Settings → Pages → Custom domain → `ortodonciarichard.cl`
5. **Integración DentiDesk API** — cuando app.dentidesk.cl entregue acceso API, reemplazar botón WhatsApp en `#agenda` (`TODO: DentiDesk` en `index.html`)
6. **Casos de Instagram** — usar fotos de casos publicados en @ortodonciarichard para la sección de tratamientos

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
