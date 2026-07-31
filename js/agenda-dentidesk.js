/* ═══════════════════════════════════════════════════════════════════════════
   agenda-dentidesk.js — Flujo de agendamiento online (Ortodoncia Richard)

   Flujo de 6 pasos:
     1) Especialidad   (Ortodoncia / Rehabilitación Oral e Implantología)
     2) RUT            (formato XX.XXX.XXX-X + validación dígito verificador)
     3) Datos paciente (precargados si DentiDesk lo reconoce; si no, los ingresa)
     4) Profesional    (solo los de la especialidad elegida)
     5) Motivo         (solo los de la especialidad elegida)
     6) Fecha y hora   -> resumen -> confirmar

   Toda la lógica sensible (DentiDesk, credenciales, ocupación simulada, RUT) vive
   también en el backend Flask. Este archivo dibuja la UI y llama al backend.
═══════════════════════════════════════════════════════════════════════════ */

const AGENDA_API = window.AGENDA_API_BASE || 'http://localhost:5001';

const agenda = {
  config: null,
  sel: {
    especialidad: null, especialidadLabel: '',
    rut: '', rutFmt: '',
    existe: false, datos: { nombres: '', apellidos: '', email: '', telefono: '' },
    doctor: null, doctorNombre: '', doctorFoto: '',
    motivo: null, motivoLabel: '',
    fecha: null, fechaLegible: '', hora: null,
    esNoSoyYo: false, datosOriginales: null, completarDatos: false,
    // Menú de motivos filtrado por el estado del paciente (lo resuelve el
    // backend en /api/agenda/paciente a partir del RUT). null = sin filtro
    // (paciente desconocido, estado vencido o kill-switch MENU_FILTRADO=off).
    motivosPermitidos: null, verTodosMotivos: false,
  },
  dias: [],
  prefetch: {},   // cache de promesas de disponibilidad por doctor|motivo
  linkToken: null,    // token del link pre-cargado que genera el F2 (#cita=<token>)
  linkExacto: false,  // true si /api/agenda/link-info resolvió paciente+doctor+motivo
  linkDatos: null,    // {nombres, email_masked, telefono_masked} enmascarados del token
  reagendaId: null,     // id_agenda de la cita vieja si se llegó por el link de reagendar
  reagendaFecha: null,  // fecha (YYYY-MM-DD) de la cita vieja, codificada en el mismo link
  reagendaExacto: false,   // true si /api/agenda/reagendar-info logró precargar doctor+motivo
  reagendaDuracion: null,  // duracion_min ORIGINAL de la cita (modo reagendaExacto)
  reagendaSoloManana: false, // true si la cita debe mantenerse antes de almuerzo (regla ortodoncia 60+ min AM)
  estudio: null,        // {etapa: 1|2, cita1: {fecha, fechaLegible, hora}} si es Estudio Integral
  filtroMinFecha: null, // 'YYYY-MM-DD': solo mostrar días >= esta fecha (cita 2 del estudio)
};

async function agendaApi(path, opts) {
  const res = await fetch(AGENDA_API + path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error('HTTP ' + res.status), { data });
  return data;
}

/* ── Telemetría del embudo (anónima, para ver dónde abandonan) ────────────── */
function track(paso, ms) {
  try {
    const body = JSON.stringify({ sesion: agenda.sessionId, paso, ms });
    fetch(AGENDA_API + '/api/agenda/evento', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body, keepalive: true,
    }).catch(() => {});
  } catch (e) { /* la telemetría nunca debe romper el flujo */ }
}

/* ── RUT: formato + validación (módulo 11) ───────────────────────────────── */

function limpiarRut(rut) {
  return (rut || '').toUpperCase().replace(/[^0-9K]/g, '');
}
function formatearRut(rut) {
  const l = limpiarRut(rut);
  if (l.length < 2) return l;
  const cuerpo = l.slice(0, -1), dv = l.slice(-1);
  return cuerpo.replace(/\B(?=(\d{3})+(?!\d))/g, '.') + '-' + dv;
}
function rutValido(rut) {
  const l = limpiarRut(rut);
  if (l.length < 2) return false;
  const cuerpo = l.slice(0, -1), dv = l.slice(-1);
  if (!/^\d+$/.test(cuerpo)) return false;
  let suma = 0, factor = 2;
  for (let i = cuerpo.length - 1; i >= 0; i--) {
    suma += parseInt(cuerpo[i], 10) * factor;
    factor = factor === 7 ? 2 : factor + 1;
  }
  const resto = 11 - (suma % 11);
  const dvCalc = resto === 11 ? '0' : resto === 10 ? 'K' : String(resto);
  return dvCalc === dv;
}

/* ── Precarga de config (para que el modal abra instantáneo) ─────────────── */

// Se dispara apenas carga la página (en segundo plano). Cuando el paciente
// hace click en "Agendar", la config ya suele estar lista -> aparece al instante.
let _configPromise = null;
function precargarConfig() {
  if (!_configPromise) {
    _configPromise = agendaApi('/api/agenda/config')
      .then(cfg => { agenda.config = cfg; return cfg; })
      .catch(err => { _configPromise = null; throw err; });  // reintentar al abrir
  }
  return _configPromise;
}

/* ── Apertura / cierre del modal ─────────────────────────────────────────── */

async function abrirAgenda() {
  const modal = document.getElementById('agendaModal');
  modal.classList.add('open');
  document.body.style.overflow = 'hidden';
  agenda.sessionId = Math.random().toString(36).slice(2) + Date.now().toString(36);
  if (!agenda.config) {
    setBody('<div class="agenda-loading"><i class="fas fa-spinner fa-spin"></i> Cargando…</div>');
    try { await precargarConfig(); }
    catch (e) { return pasoError('No pudimos conectar con la agenda online. Te recomendamos agendar por WhatsApp.'); }
  }
  track('abrir');
  // Si ya se precargó doctor+motivo en una apertura anterior de este modal
  // (cerrar y volver a abrir sin recargar la página), no volver a preguntar.
  if (agenda.reagendaExacto) { pasoRut(); return; }
  // Modo link pre-cargado (#cita=<token>, lo genera la secretaria desde el F2):
  // el token resuelve paciente + doctor + motivo SERVER-SIDE, así que el
  // paciente solo confirma que es él y elige hora. Si el token no sirve
  // (vencido, ya usado, inexistente) NO se abre el wizard libre: se muestra el
  // motivo real y se deriva a WhatsApp, igual que en reagenda.
  if (agenda.linkToken && !agenda.linkExacto) {
    try {
      const info = await agendaApi('/api/agenda/link-info?token=' + encodeURIComponent(agenda.linkToken));
      const doc = (agenda.config.doctores || []).find(d => d.key === (info.doctor || {}).key);
      if (info.ok && doc) {
        const esp = (agenda.config.especialidades || []).find(e => e.key === doc.especialidad);
        agenda.linkExacto = true;
        agenda.linkDatos = info.paciente || {};
        agenda.sel.doctor = doc.key;
        agenda.sel.doctorNombre = info.doctor.nombre || doc.name;
        agenda.sel.doctorFoto = info.doctor.foto || doc.photo;
        agenda.sel.especialidad = doc.especialidad;
        agenda.sel.especialidadLabel = esp ? esp.label : '';
        agenda.sel.motivo = info.motivo.key;
        agenda.sel.motivoLabel = info.motivo.label;
        pasoConfirmarLink();
        return;
      }
      return pasoLinkNoDisponible(info.error);
    } catch (err) {
      return pasoLinkNoDisponible((err.data && err.data.error) || '');
    }
  }
  // Modo reagenda (se llegó por el link de WhatsApp): intentar precargar el
  // doctor y motivo EXACTOS de la cita vieja (quedan bloqueados). Si NO se
  // puede (link viejo sin fecha, motivo que no está en la tabla, cita movida
  // de día, etc.), NO abrimos el wizard libre: eso dejaría al paciente elegir
  // otro motivo/doctor y marcaría igual la cita vieja como reagendada (bug
  // real: "Imp essix" terminó como "Control Fijo"). En su lugar derivamos a
  // WhatsApp sin tocar la cita vieja.
  if (agenda.reagendaId) {
    if (agenda.reagendaFecha && !agenda._reagendaInfoIntentado) {
      agenda._reagendaInfoIntentado = true;
      try {
        const info = await agendaApi(`/api/agenda/reagendar-info?id_agenda=${encodeURIComponent(agenda.reagendaId)}&fecha=${encodeURIComponent(agenda.reagendaFecha)}`);
        const doc = info.ok ? (agenda.config.doctores || []).find(d => d.key === info.doctor) : null;
        if (doc) {
          const esp = (agenda.config.especialidades || []).find(e => e.key === doc.especialidad);
          agenda.reagendaExacto = true;
          agenda.reagendaDuracion = info.duracion_min;
          agenda.reagendaSoloManana = !!info.solo_manana;
          agenda.sel.doctor = doc.key;
          agenda.sel.doctorNombre = info.doctor_nombre || doc.name;
          agenda.sel.doctorFoto = info.doctor_foto || doc.photo;
          agenda.sel.especialidad = doc.especialidad;
          agenda.sel.especialidadLabel = esp ? esp.label : '';
          agenda.sel.motivoLabel = info.motivo_label;
          pasoRut();
          return;
        }
      } catch (e) { /* cae al mensaje de reagenda-no-disponible */ }
    }
    return pasoReagendaNoDisponible();
  }
  pasoEspecialidad();
}

function cerrarAgenda() {
  document.getElementById('agendaModal').classList.remove('open');
  document.body.style.overflow = '';
  agenda.sel = {
    especialidad: null, especialidadLabel: '', rut: '', rutFmt: '',
    existe: false, datos: { nombres: '', apellidos: '', email: '', telefono: '' },
    doctor: null, doctorNombre: '', doctorFoto: '', motivo: null, motivoLabel: '',
    fecha: null, fechaLegible: '', hora: null,
    esNoSoyYo: false, datosOriginales: null, completarDatos: false,
    motivosPermitidos: null, verTodosMotivos: false,
  };
  agenda.prefetch = {};   // descartar disponibilidad precargada (puede quedar obsoleta)
  agenda.citasPreviasPromise = null;
  agenda.citaPreviaAck = false;
  agenda.estudio = null;
  agenda.filtroMinFecha = null;
  // Cerrar abandona el reagendamiento: si reabre (p.ej. con el botón normal
  // "Agendar hora online") empieza una reserva limpia. Para volver a entrar
  // en modo reagenda hay que tocar de nuevo el link de WhatsApp (re-dispara
  // _abrirDesdeHash). Sin esto, sel queda vacío pero reagendaExacto seguiría
  // true -> pasoRut sin doctor/motivo precargados = estado roto.
  agenda.reagendaId = null;
  agenda.reagendaFecha = null;
  agenda.reagendaExacto = false;
  agenda.reagendaDuracion = null;
  agenda.reagendaSoloManana = false;
  agenda._reagendaInfoIntentado = false;
  // Cerrar también abandona el link pre-cargado: reabrir con el botón normal
  // empieza una reserva limpia (mismo criterio que reagenda). Para volver a
  // entrar en modo link hay que tocar el link de nuevo (re-dispara el hash).
  agenda.linkToken = null;
  agenda.linkExacto = false;
  agenda.linkDatos = null;
}

function setBody(html) { document.getElementById('agendaBody').innerHTML = html; }
function setPaso(n) {
  document.querySelectorAll('.agenda-step-dot').forEach((d, i) => {
    d.classList.toggle('active', i === n - 1);
    d.classList.toggle('done', i < n - 1);
  });
}

/* ── Paso 1: especialidad ────────────────────────────────────────────────── */

function pasoEspecialidad() {
  setPaso(1);
  const items = agenda.config.especialidades.map(e => `
    <button class="agenda-option" onclick="elegirEspecialidad('${e.key}', this)">
      <span>${e.label}</span><i class="fas fa-chevron-right"></i>
    </button>`).join('');
  setBody(`<h3 class="agenda-q">¿Qué especialidad necesitas?</h3>
           <div class="agenda-options">${items}</div>`);
}
function elegirEspecialidad(key, el) {
  agenda.sel.especialidad = key;
  agenda.sel.especialidadLabel = el.querySelector('span').textContent;
  track('especialidad');
  pasoRut();
}

/* ── Paso 2: RUT ─────────────────────────────────────────────────────────── */

function pasoRut() {
  setPaso(2);
  // En modo reagendaExacto no hay paso de especialidad al que volver (se
  // saltó): doctor y motivo quedan fijos, tal como los tenía la cita vieja.
  const volver = agenda.reagendaExacto ? ''
    : `<button class="agenda-back" onclick="pasoEspecialidad()"><i class="fas fa-arrow-left"></i> Volver</button>`;
  setBody(`${volver}
    <h3 class="agenda-q">Tu RUT</h3>
    <p class="agenda-sub">Lo usamos para reconocerte si ya eres paciente.</p>
    <form class="agenda-form" onsubmit="return continuarRut(event)">
      <input id="agendaRut" inputmode="text" placeholder="12.345.678-9" autocomplete="off"
             oninput="onRutInput(this)" maxlength="12" required>
      <p class="agenda-rut-msg" id="agendaRutMsg"></p>
      <button type="submit" class="btn btn-primary btn-lg agenda-submit" id="agendaRutBtn" disabled>
        Continuar
      </button>
    </form>`);
  setTimeout(() => document.getElementById('agendaRut')?.focus(), 50);
}
function onRutInput(input) {
  input.value = formatearRut(input.value);
  const ok = rutValido(input.value);
  const msg = document.getElementById('agendaRutMsg');
  const btn = document.getElementById('agendaRutBtn');
  const lleno = limpiarRut(input.value).length >= 7;
  if (ok) { msg.textContent = '✓ RUT válido'; msg.className = 'agenda-rut-msg ok'; btn.disabled = false; }
  else if (lleno) { msg.textContent = 'El RUT no es válido, revísalo.'; msg.className = 'agenda-rut-msg err'; btn.disabled = true; }
  else { msg.textContent = ''; msg.className = 'agenda-rut-msg'; btn.disabled = true; }
}
async function continuarRut(e) {
  e.preventDefault();
  const input = document.getElementById('agendaRut');
  if (!rutValido(input.value)) return false;
  agenda.sel.rut = limpiarRut(input.value);
  agenda.sel.rutFmt = formatearRut(input.value);
  const btn = document.getElementById('agendaRutBtn');
  btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Buscando…';
  try {
    const r = await agendaApi('/api/agenda/paciente?rut=' + encodeURIComponent(agenda.sel.rut));
    agenda.sel.existe = r.existe;
    agenda.sel.datos = Object.assign(agenda.sel.datos, r.datos || {});
    // El backend ya resolvió en qué está el paciente y qué motivos le
    // corresponden. null (o ausente) = mostrar el menú completo de siempre.
    agenda.sel.motivosPermitidos = r.motivos_permitidos || null;
    agenda.sel.verTodosMotivos = false;
  } catch (err) { agenda.sel.existe = false; agenda.sel.motivosPermitidos = null; }
  track('rut');
  // En segundo plano (sin bloquear): buscar si ya tiene citas activas futuras.
  // El aviso se muestra antes de confirmar (paso resumen), cuando ya está listo.
  agenda.citaPreviaAck = false;
  agenda.citasPreviasPromise = agendaApi('/api/agenda/citas-futuras?rut=' + encodeURIComponent(agenda.sel.rut))
    .then(r => r.citas || []).catch(() => []);
  pasoDatos();
}

/* ── Paso 3: datos del paciente ──────────────────────────────────────────── */

function pasoDatos() {
  setPaso(3);
  const d = agenda.sel.datos;

  // Paciente RECONOCIDO con email en ficha: mostramos datos enmascarados, no
  // pedimos email (se usa el registrado para que DentiDesk no duplique la ficha).
  if (agenda.sel.existe && d.tiene_email) {
    setBody(`<button class="agenda-back" onclick="pasoRut()"><i class="fas fa-arrow-left"></i> Volver</button>
      <h3 class="agenda-q">¿Eres tú?</h3>
      <p class="agenda-sub">RUT ${agenda.sel.rutFmt}</p>
      <div class="agenda-aviso ok"><i class="fas fa-circle-check"></i> Te reconocimos. Confirma que eres tú para continuar.</div>
      <ul class="agenda-detalle">
        <li><span>Nombre</span><b>${d.nombres || ''} ${d.apellidos || ''}</b></li>
        <li><span>Email</span><b>${d.email_masked || '—'}</b></li>
        <li><span>Teléfono</span><b>${d.telefono_masked || '—'}</b></li>
      </ul>
      <button class="btn btn-primary btn-lg agenda-submit" onclick="confirmarReconocido()">Sí, soy yo · Continuar</button>
      <p class="agenda-mini"><a href="#" onclick="noSoyYo(event)">No soy yo / usar otros datos</a></p>`);
    return;
  }

  // Paciente RECONOCIDO pero SIN email en ficha (paciente antiguo): confirmamos su
  // nombre y le pedimos email/teléfono para enviarle la confirmación. Al agendar se
  // usa su RUT (DentiDesk lo asocia a su ficha) y avisamos a recepción del contacto.
  if (agenda.sel.existe && !d.tiene_email) {
    agenda.sel.completarDatos = true;
    setBody(`<button class="agenda-back" onclick="pasoRut()"><i class="fas fa-arrow-left"></i> Volver</button>
      <h3 class="agenda-q">¡Hola, ${d.nombres || ''}!</h3>
      <p class="agenda-sub">RUT ${agenda.sel.rutFmt}</p>
      <div class="agenda-aviso ok"><i class="fas fa-circle-check"></i> Te reconocimos como <strong>${d.nombres || ''} ${d.apellidos || ''}</strong>. Solo necesitamos tus datos de contacto para enviarte la confirmación.</div>
      <form class="agenda-form" onsubmit="return continuarDatos(event)">
        <input name="nombres"   type="hidden" value="${d.nombres || ''}">
        <input name="apellidos" type="hidden" value="${d.apellidos || ''}">
        <input name="telefono" placeholder="Celular (ej: +56 9 1234 5678)" value="${d.telefono || ''}" required>
        <input name="email" type="email" placeholder="Email" value="" required>
        <button type="submit" class="btn btn-primary btn-lg agenda-submit">Continuar</button>
      </form>
      <p class="agenda-mini"><a href="#" onclick="noSoyYo(event)">No soy yo / usar otros datos</a></p>`);
    return;
  }

  // Paciente NUEVO o "no soy yo": formulario completo (email obligatorio).
  const aviso = agenda.sel.esNoSoyYo
    ? `<div class="agenda-aviso"><i class="fas fa-user-pen"></i> Te reconocemos como <strong>${d.nombres} ${d.apellidos}</strong>. Ingresa tus datos de contacto actualizados — tu hora quedará agendada y notificaremos a la clínica para actualizar tu ficha.</div>`
    : `<div class="agenda-aviso"><i class="fas fa-user-pen"></i> Completa tus datos para confirmar la reserva.</div>`;
  setBody(`<button class="agenda-back" onclick="pasoRut()"><i class="fas fa-arrow-left"></i> Volver</button>
    <h3 class="agenda-q">Tus datos</h3>
    <p class="agenda-sub">RUT ${agenda.sel.rutFmt}</p>
    ${aviso}
    <form class="agenda-form" onsubmit="return continuarDatos(event)">
      <input name="nombres"   placeholder="Nombres" value="${d.nombres || ''}" required>
      <input name="apellidos" placeholder="Apellidos" value="${d.apellidos || ''}" required>
      <input name="telefono" placeholder="Celular (ej: +56 9 1234 5678)" value="${d.telefono || ''}" required>
      <input name="email" type="email" placeholder="Email" value="${d.email || ''}" required>
      <button type="submit" class="btn btn-primary btn-lg agenda-submit">Continuar</button>
    </form>`);
}

/* ── Modo link pre-cargado (#cita=<token>) ───────────────────────────────── */

// El token ya trae paciente + doctor + motivo resueltos en el backend, así que
// acá NO se pide RUT ni datos: solo se confirma identidad (mismo gesto que la
// pantalla "¿Eres tú?" del paciente reconocido) y se salta a elegir hora.
function pasoConfirmarLink() {
  setPaso(3);
  const p = agenda.linkDatos || {};
  const s = agenda.sel;
  const saludo = p.nombres ? `¿Eres tú, ${p.nombres}?` : 'Confirmemos tu hora';
  const contacto = (p.email_masked || p.telefono_masked) ? `
    <ul class="agenda-detalle">
      ${p.email_masked ? `<li><span>Email</span><b>${p.email_masked}</b></li>` : ''}
      ${p.telefono_masked ? `<li><span>Teléfono</span><b>${p.telefono_masked}</b></li>` : ''}
    </ul>` : '';
  setBody(`<h3 class="agenda-q">${saludo}</h3>
    <div class="agenda-aviso ok"><i class="fas fa-circle-check"></i> La clínica te preparó este link. Solo falta que elijas el día y la hora.</div>
    <div class="agenda-resumen">
      <img src="${s.doctorFoto}" alt="">
      <div><strong>${s.doctorNombre}</strong><span>${s.motivoLabel}</span></div>
    </div>
    ${contacto}
    <button class="btn btn-primary btn-lg agenda-submit" onclick="continuarDesdeLink()">Sí, soy yo · Elegir fecha y hora</button>
    <p class="agenda-mini"><a href="#" onclick="noSoyYoLink(event)">No soy yo / agendar de otra forma</a></p>`);
  // El aviso de "ya tienes una hora agendada" también aplica acá; el backend
  // resuelve el RUT desde el token (nunca viaja en la URL).
  agenda.citaPreviaAck = false;
  agenda.citasPreviasPromise = agendaApi('/api/agenda/citas-futuras?link_token=' + encodeURIComponent(agenda.linkToken))
    .then(r => r.citas || []).catch(() => []);
}

function continuarDesdeLink() {
  track('datos'); track('profesional'); track('motivo');
  agenda._horasT0 = Date.now();
  pasoFechaHora();
}

// El link llegó a otra persona (reenviado, celular compartido): se descarta el
// modo link y arranca el wizard normal desde cero.
function noSoyYoLink(e) {
  if (e) e.preventDefault();
  agenda.linkToken = null;
  agenda.linkExacto = false;
  agenda.linkDatos = null;
  agenda.sel.doctor = null; agenda.sel.doctorNombre = ''; agenda.sel.doctorFoto = '';
  agenda.sel.motivo = null; agenda.sel.motivoLabel = '';
  agenda.sel.especialidad = null; agenda.sel.especialidadLabel = '';
  agenda.citasPreviasPromise = null;
  pasoEspecialidad();
}

// Token vencido, ya usado o inexistente. No se abre el wizard libre a ciegas:
// se explica y se ofrecen las dos salidas (agendar normal o WhatsApp).
function pasoLinkNoDisponible(msg) {
  agenda.linkToken = null;
  agenda.linkExacto = false;
  setBody(`<div class="agenda-final err">
    <i class="fas fa-link-slash"></i>
    <h3>Este link ya no está disponible</h3>
    <p>${msg || 'El link que te enviamos venció o ya fue usado.'}</p>
    <button class="btn btn-primary btn-lg" onclick="noSoyYoLink()">Agendar mi hora</button>
    <p class="agenda-mini"><a href="https://wa.me/56933558189?text=Hola,%20el%20link%20para%20agendar%20que%20me%20enviaron%20no%20funciona" target="_blank" rel="noopener">O escríbenos por WhatsApp</a></p>
  </div>`);
}

function confirmarReconocido() {
  // No tocamos email/telefono: el backend usa los registrados (dedup por RUT+email).
  continuarDespuesDeDatos();
}

// Modo reagendaExacto: doctor y motivo ya vienen precargados de la cita vieja
// (no se le preguntan al paciente) -> salta directo a elegir hora.
function continuarDespuesDeDatos() {
  track('datos');
  if (agenda.reagendaExacto) {
    track('profesional');
    track('motivo');
    agenda._horasT0 = Date.now();
    pasoFechaHora();
  } else {
    pasoProfesional();
  }
}

function noSoyYo(e) {
  e.preventDefault();
  agenda.sel.esNoSoyYo = true;
  agenda.sel.completarDatos = false;
  agenda.sel.datosOriginales = { ...agenda.sel.datos };
  agenda.sel.existe = false;
  // Quien agenda NO es el paciente de la ficha: el estado de ese RUT ya no
  // describe a quien está usando el wizard -> vuelve el menú completo.
  agenda.sel.motivosPermitidos = null;
  // Conserva nombre (ya visible y no sensible); limpia contacto para que lo ingrese
  agenda.sel.datos = {
    nombres: agenda.sel.datos.nombres,
    apellidos: agenda.sel.datos.apellidos,
    email: '', telefono: '',
  };
  pasoDatos();
}

function continuarDatos(e) {
  e.preventDefault();
  const f = e.target;
  agenda.sel.datos = {
    nombres: f.nombres.value.trim(), apellidos: f.apellidos.value.trim(),
    telefono: f.telefono.value.trim(), email: f.email.value.trim(),
  };
  continuarDespuesDeDatos();
  return false;
}

/* ── Paso 4: profesional (filtrado por especialidad) ─────────────────────── */

function pasoProfesional() {
  setPaso(4);
  const docs = agenda.config.doctores.filter(d => d.especialidad === agenda.sel.especialidad);
  const cards = docs.map(d => `
    <button class="agenda-doctor" onclick="elegirDoctor('${d.key}', this)">
      <img src="${d.photo}" alt="${d.name}" onerror="this.style.visibility='hidden'">
      <div><strong>${d.name}</strong><span>${d.role || ''}</span></div>
    </button>`).join('');
  setBody(`<button class="agenda-back" onclick="pasoDatos()"><i class="fas fa-arrow-left"></i> Volver</button>
    <h3 class="agenda-q">Elige a tu especialista</h3>
    <p class="agenda-sub">${agenda.sel.especialidadLabel}</p>
    <div class="agenda-doctors">${cards}</div>`);
}
function elegirDoctor(key, el) {
  agenda.sel.doctor = key;
  agenda.sel.doctorNombre = el.querySelector('strong').textContent;
  agenda.sel.doctorFoto = el.querySelector('img').src;
  track('profesional');
  pasoMotivo();
}

/* ── Paso 5: motivo (filtrado por especialidad) ──────────────────────────── */

function pasoMotivo() {
  setPaso(5);
  // Volver al menú de motivos cancela cualquier flujo de estudio a medias.
  agenda.estudio = null;
  agenda.filtroMinFecha = null;
  const motivos = _motivosDelPaso();
  const items = motivos.map(m => `
    <button class="agenda-option" onclick="${m.compuesto ? `elegirMotivoEstudio('${m.key}')` : `elegirMotivo('${m.key}', this)`}">
      <span>${m.label}</span>
      ${m.urgencia ? '<span class="agenda-badge-urg">Urgencia</span>' : ''}
      <i class="fas fa-chevron-right"></i>
    </button>`).join('');
  setBody(`<button class="agenda-back" onclick="pasoProfesional()"><i class="fas fa-arrow-left"></i> Volver</button>
    <div class="agenda-doctor-head">
      <img src="${agenda.sel.doctorFoto}" alt="">
      <div><strong>${agenda.sel.doctorNombre}</strong><span>${agenda.sel.especialidadLabel}</span></div>
    </div>
    <h3 class="agenda-q">¿Cuál es el motivo de tu consulta?</h3>
    <div class="agenda-options">${items}</div>
    ${_escapeMotivosHTML()}`);
  // Precarga SECUENCIAL (no en ráfaga) de los motivos del doctor mientras el
  // paciente lee: deja tibio para que elegir/cambiar de motivo sea instantáneo.
  // Los compuestos (Estudio) se calientan por sus 2 motivos reales.
  const aCalentar = motivos.flatMap(m => m.compuesto ? m.compuesto.map(k => ({ key: k })) : [m]);
  calentarMotivos(agenda.sel.doctor, aCalentar);
}

/* ── Menú de motivos según el estado del paciente ────────────────────────── */

// El backend dice qué motivos le corresponden a este RUT (motivos_permitidos).
// Con esa lista el paciente ve SOLO lo suyo (ej. en tratamiento con frenillos:
// control fijo + urgencia) en vez del menú completo, donde se perdía.
// Sin lista -> menú de siempre, quitando los motivos `solo_filtrado` (los que
// existen únicamente para una categoría concreta, como Control de Contención).
function _motivosDelPaso() {
  const todos = agenda.config.motivos.filter(m => m.especialidad === agenda.sel.especialidad && !m.oculto);
  const permitidas = agenda.sel.motivosPermitidos;
  if (!permitidas || !permitidas.length || agenda.sel.verTodosMotivos) {
    return todos.filter(m => !m.solo_filtrado);
  }
  // Se respeta el ORDEN de motivos_permitidos (el backend lo manda de más
  // probable a menos), no el del config.
  const filtrados = permitidas.map(k => todos.find(m => m.key === k)).filter(Boolean);
  // Defensa: si la config y el estado quedaran desalineados (motivo renombrado
  // o borrado), NUNCA dejar al paciente sin opciones -> menú completo.
  return filtrados.length ? filtrados : todos.filter(m => !m.solo_filtrado);
}

function _escapeMotivosHTML() {
  const p = agenda.sel.motivosPermitidos;
  if (!p || !p.length || agenda.sel.verTodosMotivos) return '';
  if (!_motivosDelPaso().length) return '';
  return `<p class="agenda-mini" style="text-align:center;margin-top:12px">
    <a href="#" onclick="verTodosMotivos(event)">¿Buscas otro tipo de hora? Ver todos los motivos</a></p>`;
}

function verTodosMotivos(e) {
  if (e) e.preventDefault();
  agenda.sel.verTodosMotivos = true;
  pasoMotivo();
}

/* ── Precarga de disponibilidad ──────────────────────────────────────────── */

// Modo reagendaExacto: el motivo original de la cita puede no estar en la
// lista de motivos agendables online, así que la disponibilidad se pide por
// DURACIÓN (endpoint /disponibilidad-reagendar) en vez de por motivo_key.
function _disponibilidadUrl(offset, minDias) {
  const doctor = agenda.sel.doctor;
  if (agenda.reagendaExacto) {
    const soloAm = agenda.reagendaSoloManana ? '&solo_am=1' : '';
    return `/api/agenda/disponibilidad-reagendar?doctor=${doctor}&duracion=${agenda.reagendaDuracion}${soloAm}&offset=${offset}&min_dias=${minDias}`;
  }
  return `/api/agenda/disponibilidad?doctor=${doctor}&motivo=${agenda.sel.motivo}&offset=${offset}&min_dias=${minDias}`;
}

function _dispoKey(doctor, motivo) {
  return agenda.reagendaExacto ? (doctor + '|reagenda|' + agenda.reagendaDuracion) : (doctor + '|' + motivo);
}

function prefetchDisponibilidad(doctor, motivo) {
  const key = _dispoKey(doctor, motivo);
  if (!agenda.prefetch[key]) {
    // min_dias=5: el servidor junta al menos 5 días CON horas en una sola
    // request (escanea lotes en paralelo), en vez de que el cliente pida
    // página tras página en serie (2-3s cada una con doctores de pocos días).
    agenda.prefetch[key] = agendaApi(_disponibilidadUrl(0, 5))
      .catch(err => { delete agenda.prefetch[key]; throw err; });  // permitir reintento
  }
  return agenda.prefetch[key];
}

// Precarga SECUENCIAL (uno a uno) de los motivos de un doctor. Secuencial =
// sin ráfagas que saturen el servidor. Cada solicitud deja tibio el motivo para
// el siguiente clic del paciente (cambiar de motivo se vuelve instantáneo).
async function calentarMotivos(doctor, motivos) {
  for (const m of motivos) {
    if (agenda.prefetch[_dispoKey(doctor, m.key)]) continue;   // ya pedido
    try { await prefetchDisponibilidad(doctor, m.key); } catch (e) { /* sigue */ }
  }
}

function elegirMotivo(key, el) {
  agenda.sel.motivo = key;
  const m = (agenda.config.motivos || []).find(x => x.key === key);
  agenda.sel.motivoLabel = (el && el.querySelector) ? el.querySelector('span').textContent : (m ? m.label : key);
  agenda.sel.motivoUrgencia = !!(m && m.urgencia);
  track('motivo');
  agenda._horasT0 = Date.now();   // para medir cuánto tarda en cargar las horas
  pasoFechaHora();
}

/* ── Estudio Integral de Ortodoncia (motivo compuesto: 2 citas) ──────────── */

function _motivoCfg(key) { return (agenda.config.motivos || []).find(m => m.key === key); }

function elegirMotivoEstudio(key) {
  const comp = _motivoCfg(key);
  const soloExistentes = !comp || comp.solo_pacientes_existentes !== false;
  // Gate: solo pacientes ya registrados (con consulta previa). "No soy yo"
  // significa que quien agenda NO es el paciente de la ficha -> no aplica.
  if (soloExistentes && (!agenda.sel.existe || agenda.sel.esNoSoyYo)) {
    setBody(`<button class="agenda-back" onclick="pasoMotivo()"><i class="fas fa-arrow-left"></i> Volver</button>
      <h3 class="agenda-q">Estudio Integral de Ortodoncia</h3>
      <div class="agenda-urgencia">
        <p>El Estudio Integral está disponible para pacientes que <strong>ya han tenido una consulta</strong> en nuestra clínica.</p>
        <p>Si aún no ha venido, le invitamos a agendar primero una <strong>Primera Consulta</strong>, donde el doctor evaluará su caso e indicará si corresponde realizar el estudio.</p>
      </div>
      <button class="btn btn-primary btn-lg agenda-submit" onclick="elegirMotivo('primera_consulta')">Agendar Primera Consulta</button>
      <p class="agenda-mini"><a href="#" onclick="pasoMotivo();return false;">Volver a los motivos</a></p>`);
    return;
  }
  const sep = (comp && comp.separacion_min_dias) || 14;
  setBody(`<button class="agenda-back" onclick="pasoMotivo()"><i class="fas fa-arrow-left"></i> Volver</button>
    <h3 class="agenda-q">Estudio Integral de Ortodoncia</h3>
    <p class="agenda-sub">Este estudio consta de <strong>2 citas</strong> que agendará a continuación:</p>
    <ul class="agenda-detalle">
      <li><span>1️⃣ Registros para el Estudio</span><b>30 min</b></li>
    </ul>
    <p class="agenda-sub" style="margin:4px 0 12px">Se toman los registros clínicos: escaneo intraoral, radiografías 2D y 3D y fotografías clínicas.</p>
    <ul class="agenda-detalle">
      <li><span>2️⃣ Explicación del Diagnóstico y Plan de Tratamiento</span><b>30 min</b></li>
    </ul>
    <p class="agenda-sub" style="margin:4px 0 12px">Le explicamos a usted y su familia el estado ortodóncico del paciente y las alternativas de tratamiento más adecuadas para su caso.</p>
    <p class="agenda-sub"><i class="far fa-clock"></i> Entre ambas citas debe haber <strong>al menos ${sep === 14 ? '2 semanas' : sep + ' días'}</strong> de distancia, para alcanzar a realizar el estudio.</p>
    <button class="btn btn-primary btn-lg agenda-submit" onclick="estudioComenzar()">Elegir hora de los Registros <i class="fas fa-arrow-right"></i></button>`);
}

function estudioComenzar() {
  const comp = _motivoCfg('estudio_integral') || {};
  const [m1] = comp.compuesto || ['estudio_registros'];
  agenda.estudio = { etapa: 1, cita1: null, sep: comp.separacion_min_dias || 14 };
  agenda.filtroMinFecha = null;
  agenda.sel.motivo = m1;
  agenda.sel.motivoLabel = 'Cita 1 de 2 · Registros para el Estudio';
  agenda.sel.motivoUrgencia = false;
  track('motivo');
  agenda._horasT0 = Date.now();
  agenda.diaSel = 0;
  pasoFechaHora();
}

function estudioHoraElegida() {
  const e = agenda.estudio, s = agenda.sel;
  if (e.etapa === 1) {
    e.cita1 = { fecha: s.fecha, fechaLegible: s.fechaLegible, hora: s.hora };
    // Cita 2: solo fechas >= cita1 + separación mínima.
    const dt = new Date(s.fecha + 'T00:00:00');
    dt.setDate(dt.getDate() + e.sep);
    agenda.filtroMinFecha = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
    e.etapa = 2;
    const comp = _motivoCfg('estudio_integral') || {};
    s.motivo = (comp.compuesto || [])[1] || 'estudio_explicacion';
    s.motivoLabel = 'Cita 2 de 2 · Explicación del Diagnóstico y Plan';
    agenda.diaSel = 0;
    agenda._horasT0 = Date.now();
    pasoFechaHora();
  } else {
    e.cita2 = { fecha: s.fecha, fechaLegible: s.fechaLegible, hora: s.hora };
    pasoResumenEstudio();
  }
}

// Banner de progreso del estudio sobre la grilla de horas (etapa 2 recuerda la cita 1).
function avisoEstudioHTML() {
  const e = agenda.estudio;
  if (!e) return '';
  if (e.etapa === 2 && e.cita1) {
    return `<div class="agenda-urgencia" style="margin-bottom:10px">
      <p>✅ <strong>Cita 1 (Registros):</strong> ${e.cita1.fechaLegible} · ${e.cita1.hora} hrs</p>
      <p>Ahora elija la hora de la <strong>Explicación del Plan</strong> (desde ${e.sep === 14 ? '2 semanas' : e.sep + ' días'} después).</p>
    </div>`;
  }
  return '';
}

function pasoResumenEstudio() {
  const s = agenda.sel, e = agenda.estudio;
  setBody(`<button class="agenda-back" onclick="estudioVolverACita2()"><i class="fas fa-arrow-left"></i> Volver</button>
    <h3 class="agenda-q">Revisa y confirma tu Estudio Integral</h3>
    <div class="agenda-resumen">
      <img src="${s.doctorFoto}" alt="">
      <div>
        <strong>${s.doctorNombre}</strong>
        <span>Estudio Integral de Ortodoncia (2 citas)</span>
        <span><i class="fas fa-calendar"></i> 1️⃣ Registros: ${e.cita1.fechaLegible} · ${e.cita1.hora} hrs</span>
        <span><i class="fas fa-calendar"></i> 2️⃣ Explicación del Plan: ${e.cita2.fechaLegible} · ${e.cita2.hora} hrs</span>
      </div>
    </div>
    <ul class="agenda-detalle">
      <li><span>Paciente</span><b>${s.datos.nombres} ${s.datos.apellidos}</b></li>
      <li><span>RUT</span><b>${s.rutFmt}</b></li>
    </ul>
    ${agenda.config.turnstile_sitekey ? '<div id="agenda-captcha" style="margin:14px 0;display:flex;justify-content:center"></div>' : ''}
    <button class="btn btn-primary btn-lg agenda-submit" id="agendaConfirmBtn" onclick="confirmarReservaEstudio()"${agenda.config.turnstile_sitekey ? ' disabled' : ''}>
      <i class="fas fa-check"></i> Confirmar ambas citas
    </button>
    <p class="agenda-mini">Recibirás un email de confirmación por cada cita, con archivos para agregarlas a tu calendario.</p>`);
  montarCaptcha();
}

function estudioVolverACita2() {
  // Volver desde el resumen: re-elegir la hora de la cita 2 (el filtro sigue activo).
  agenda.estudio.etapa = 2;
  agenda.diaSel = 0;
  pasoFechaHora();
}

async function confirmarReservaEstudio() {
  const btn = document.getElementById('agendaConfirmBtn');
  btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Agendando ambas citas…';
  const s = agenda.sel, e = agenda.estudio;
  try {
    const r = await agendaApi('/api/agenda/reservar-estudio', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        doctor: s.doctor, rut: s.rut,
        fecha1: e.cita1.fecha, hora1: e.cita1.hora,
        fecha2: e.cita2.fecha, hora2: e.cita2.hora,
        nombres: s.datos.nombres, apellidos: s.datos.apellidos,
        email: s.datos.email || '', telefono: s.datos.telefono || '',
        captcha_token: agenda.captchaToken || '',
      }),
    });
    if (r.ok) { track('reservado'); pasoExitoEstudio(r); } else pasoError(r.error || 'No se pudo agendar.');
  } catch (err) {
    pasoError((err.data && err.data.error) || 'Ocurrió un problema al agendar. Intenta por WhatsApp.');
  }
}

function pasoExitoEstudio(r) {
  const s = agenda.sel, e = agenda.estudio;
  setBody(`<div class="agenda-final ok">
    <i class="fas fa-circle-check"></i>
    <h3>¡Tu Estudio Integral quedó agendado!</h3>
    <p>${s.doctorNombre}</p>
    <p>1️⃣ Registros: ${e.cita1.fechaLegible} · ${e.cita1.hora} hrs<br>
       2️⃣ Explicación del Plan: ${e.cita2.fechaLegible} · ${e.cita2.hora} hrs</p>
    <p class="agenda-mini">Te enviamos un email de confirmación por cada cita${r.mock ? ' (modo demo)' : ''}.</p>
    <button class="btn btn-primary" onclick="cerrarAgenda()">Listo</button>
  </div>`);
}

// Aviso destacado para motivos de Urgencia (se muestra sobre la grilla de horas)
function avisoUrgenciaHTML() {
  if (!agenda.sel.motivoUrgencia) return '';
  return `<div class="agenda-urgencia">
    <h4><i class="fas fa-staff-snake"></i> Las urgencias son nuestra prioridad</h4>
    <p>Queremos asegurarnos de que estés bien y resolver tu urgencia a la brevedad.
       Para atenderte lo antes posible, te recomendamos contactarnos directamente:</p>
    <div class="agenda-urgencia-btns">
      <a class="btn-urg btn-urg-call" href="tel:+56222173499"><i class="fas fa-phone"></i> Llamar a la clínica</a>
      <a class="btn-urg btn-urg-wa" href="https://wa.me/56933558189?text=Hola,%20tengo%20una%20urgencia%20de%20ortodoncia" target="_blank" rel="noopener"><i class="fab fa-whatsapp"></i> Escribir por WhatsApp</a>
    </div>
    <p class="agenda-urgencia-note"><i class="fas fa-clock"></i> De lunes a viernes, de 9:00 a 19:00, siempre hay alguien en la clínica para resolver urgencias.</p>
    <p class="agenda-urgencia-or">También puedes reservar una hora online aquí abajo:</p>
  </div>`;
}

/* ── Paso 6: fecha y hora ────────────────────────────────────────────────── */

const TIRA_MIN_DIAS = 5;   // intentar mostrar al menos 5 días en la tira

// "¿Sabías qué?" rotando mientras se espera (frases configurables en el panel).
// Se usa al cargar las horas y al cargar el calendario.
let _sqIdx = 0;
function _sabiasHTML() {
  const frases = (agenda.config && agenda.config.sabias_que) || [];
  if (!frases.length) return '';
  _sqIdx = Math.floor(Math.random() * frases.length);   // arranca en una al azar
  return `<div class="agenda-sabias"><p class="sq-titulo">¿Sabías qué?</p><p class="sq-frase" id="sqFrase">${frases[_sqIdx]}</p></div>`;
}
function _loadingFechaHTML() {
  return `<div class="agenda-loading"><i class="fas fa-spinner fa-spin"></i> Buscando horas disponibles…</div>${_sabiasHTML()}`;
}
function _iniciarSabias() {
  clearInterval(agenda._sqIv);
  const frases = (agenda.config && agenda.config.sabias_que) || [];
  if (frases.length < 2) return;
  let i = _sqIdx;
  agenda._sqIv = setInterval(() => {
    const el = document.getElementById('sqFrase');
    if (!el) { clearInterval(agenda._sqIv); return; }   // ya cargó -> se autolimpia
    i = (i + 1) % frases.length;
    el.style.opacity = '0';
    setTimeout(() => { el.textContent = frases[i]; el.style.opacity = '1'; }, 250);
  }, 3800);
}

async function pasoFechaHora() {
  setPaso(6);
  agenda.dias = [];
  agenda.diaSel = 0;
  agenda.vistaCal = false;
  agenda.calM = null;
  agenda.diasOffset = 0;
  agenda.diasHayMas = false;
  setBody(`<button class="agenda-back" onclick="_volverDesdeFechaHora()"><i class="fas fa-arrow-left"></i> Volver</button>
    <div class="agenda-doctor-head">
      <img src="${agenda.sel.doctorFoto}" alt="">
      <div><strong>${agenda.sel.doctorNombre}</strong><span>${agenda.sel.motivoLabel}</span></div>
    </div>
    ${_loadingFechaHTML()}`);
  _iniciarSabias();
  let r;
  try {
    // Usa la precarga iniciada en pasoMotivo (suele estar lista -> instantáneo).
    r = await prefetchDisponibilidad(agenda.sel.doctor, agenda.sel.motivo);
  } catch (e) { return pasoError('No pudimos cargar la disponibilidad.'); }
  agenda.dias = _filtrarDias(r.dias);
  agenda.diasOffset = r.offset_siguiente || 0;
  agenda.diasHayMas = !!r.hay_mas;
  // Respaldo: si el servidor no alcanzó a juntar TIRA_MIN_DIAS (p.ej. tope de
  // días por request, o el filtro de fecha mínima del estudio descartó días),
  // completar aquí — con min_dias también, para no volver al ping-pong.
  while (agenda.dias.length < TIRA_MIN_DIAS && agenda.diasHayMas) {
    const r2 = await agendaApi(_disponibilidadUrl(agenda.diasOffset, 5));
    agenda.dias = agenda.dias.concat(_filtrarDias(r2.dias));
    agenda.diasOffset = r2.offset_siguiente || agenda.diasOffset;
    agenda.diasHayMas = !!r2.hay_mas;
  }
  if (!agenda.dias.length) {
    return pasoError('No hay horas disponibles en este momento. Escríbenos por WhatsApp y te ayudamos.');
  }
  track('horas', agenda._horasT0 ? Date.now() - agenda._horasT0 : undefined);
  renderFechaHora();
}

// Filtro de fecha mínima (cita 2 del Estudio: >= cita1 + separación).
function _filtrarDias(arr) {
  arr = arr || [];
  return agenda.filtroMinFecha ? arr.filter(d => d.fecha >= agenda.filtroMinFecha) : arr;
}

/* Selector híbrido: tira de días (por defecto) + calendario mensual (opcional).
   Las horas del día elegido se agrupan en Mañana / Tarde. */

const _DOW_ABBR = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb'];
const _MESES = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'];

function _fechaPartes(fecha) {
  const p = (fecha || '').split('-');
  const dt = new Date(+p[0], +p[1] - 1, +p[2]);
  return { abbr: _DOW_ABBR[dt.getDay()], num: +p[2] };
}
function _jornada(h) { return h < '14:00' ? 'AM' : 'PM'; }   // HH:MM con cero -> compara bien

function _horasJornadaHTML(d, diaIdx) {
  const chips = hs => hs.map(h => `<button class="agenda-hora" onclick="elegirHora(${diaIdx}, '${h}')">${h}</button>`).join('');
  const am = d.horas.filter(h => _jornada(h) === 'AM');
  const pm = d.horas.filter(h => _jornada(h) === 'PM');
  let out = '';
  if (am.length) out += `<p class="agenda-jornada"><i class="fas fa-sun"></i> Mañana</p><div class="agenda-horas">${chips(am)}</div>`;
  if (pm.length) out += `<p class="agenda-jornada"><i class="fas fa-cloud-sun"></i> Tarde</p><div class="agenda-horas">${chips(pm)}</div>`;
  return out || '<p class="agenda-sub">No hay horas este día.</p>';
}

// Modo reagendaExacto: no hay paso de motivo al que volver (doctor+motivo
// quedaron fijos con los de la cita vieja) -> "Volver" regresa a los datos.
function _volverDesdeFechaHora() {
  if (agenda.reagendaExacto) { pasoDatos(); return; }
  // Modo link: tampoco hay pasos previos (no se pidió RUT ni motivo) -> vuelve
  // a la pantalla de confirmar identidad.
  if (agenda.linkExacto) { pasoConfirmarLink(); return; }
  pasoMotivo();
}

function _cabeceraFechaHora() {
  return `<button class="agenda-back" onclick="_volverDesdeFechaHora()"><i class="fas fa-arrow-left"></i> Volver</button>
    <div class="agenda-doctor-head">
      <img src="${agenda.sel.doctorFoto}" alt="">
      <div><strong>${agenda.sel.doctorNombre}</strong><span>${agenda.sel.motivoLabel}</span></div>
    </div>
    ${avisoUrgenciaHTML()}
    ${avisoEstudioHTML()}
    ${avisoSoloMananaHTML()}
    <h3 class="agenda-q">Elige día y hora</h3>`;
}

// Aviso cuando la cita reagendada debe mantenerse en la mañana (regla de
// almuerzo para citas largas de ortodoncia).
function avisoSoloMananaHTML() {
  if (!(agenda.reagendaExacto && agenda.reagendaSoloManana)) return '';
  return `<div class="agenda-urgencia" style="margin-bottom:10px">
    <p><i class="fas fa-sun"></i> Por el tipo de cita, solo mostramos horarios de <strong>mañana</strong>.</p>
  </div>`;
}

function renderFechaHora() {
  if (agenda.diaSel == null || agenda.diaSel >= agenda.dias.length) agenda.diaSel = 0;
  if (agenda.vistaCal) { setBody(_cabeceraFechaHora() + renderCalendarioHTML() + _avisoNingunaHoraHTML()); return; }

  const pills = agenda.dias.map((d, i) => {
    const fp = _fechaPartes(d.fecha);
    return `<button class="agenda-daypill ${i === agenda.diaSel ? 'sel' : ''}" onclick="seleccionarDia(${i})">
      <span class="dp-dow">${fp.abbr}</span><span class="dp-num">${fp.num}</span></button>`;
  }).join('');
  const masPill = agenda.diasHayMas
    ? `<button class="agenda-daypill mas" id="agendaVerMas" onclick="cargarMasFechas()"><span class="dp-num"><i class="fas fa-chevron-right"></i></span><span class="dp-dow">más</span></button>`
    : '';
  const d = agenda.dias[agenda.diaSel];
  setBody(_cabeceraFechaHora() + `
    <div class="agenda-tira">${pills}${masPill}</div>
    <p class="agenda-sub2">${d ? d.legible : ''}</p>
    <div class="agenda-horas-wrap">${d ? _horasJornadaHTML(d, agenda.diaSel) : ''}</div>
    <p class="agenda-cal-link"><a href="#" onclick="abrirCalendario(event)"><i class="far fa-calendar"></i> Ver calendario</a></p>
    ${_avisoNingunaHoraHTML()}`);
}

// Salida humana si la grilla no le sirve al paciente (independiente del motivo).
function _avisoNingunaHoraHTML() {
  return `<p class="agenda-mini" style="text-align:center;margin-top:14px;color:#718096">
    Si ninguna de las horas disponibles le acomoda,
    <a href="https://wa.me/56933558189?text=Hola,%20ninguna%20de%20las%20horas%20online%20me%20acomoda,%20%C2%BFme%20pueden%20ayudar%3F" target="_blank" rel="noopener" style="color:#1A2E4A;font-weight:600">escríbanos</a>
    o <a href="tel:+56222173499" style="color:#1A2E4A;font-weight:600">llámenos</a>
    para coordinar una solución a lo que necesita.</p>`;
}

function seleccionarDia(i) { agenda.diaSel = i; renderFechaHora(); }

async function abrirCalendario(e) {
  if (e) e.preventDefault();
  if (agenda.diasHayMas) {
    setBody(_cabeceraFechaHora() + '<div class="agenda-loading"><i class="fas fa-spinner fa-spin"></i> Cargando calendario…</div>' + _sabiasHTML());
    _iniciarSabias();
    try {
      while (agenda.diasHayMas) {
        // min_dias=10 (el máximo): cada request escanea hasta 30 días de una
        // vez en el servidor -> el calendario completo se arma en 1-2 requests
        // en vez de ~7 páginas en serie.
        const r = await agendaApi(_disponibilidadUrl(agenda.diasOffset, 10));
        agenda.diasOffset = r.offset_siguiente || agenda.diasOffset;
        agenda.diasHayMas = !!r.hay_mas;
        const nuevos = _filtrarDias(r.dias);
        if (nuevos.length) agenda.dias = agenda.dias.concat(nuevos);
      }
    } catch (err) { /* seguimos con lo cargado */ }
  }
  // El calendario abre en el mes del día seleccionado.
  const d = agenda.dias[agenda.diaSel] || agenda.dias[0];
  if (d) { const p = d.fecha.split('-').map(Number); agenda.calY = p[0]; agenda.calM = p[1]; }
  agenda.vistaCal = true;
  renderFechaHora();
}
function cerrarCalendario(e) { if (e) e.preventDefault(); agenda.vistaCal = false; renderFechaHora(); }
function seleccionarDiaCal(i) { agenda.diaSel = i; renderFechaHora(); }   // permanece en vista calendario

function _rangoMeses() {
  const fechas = agenda.dias.map(d => d.fecha).sort();
  const f = fechas[0].split('-').map(Number), l = fechas[fechas.length - 1].split('-').map(Number);
  return { lo: f[0] * 12 + (f[1] - 1), hi: l[0] * 12 + (l[1] - 1) };
}
function calNavMes(delta) {
  const r = _rangoMeses();
  let idx = agenda.calY * 12 + (agenda.calM - 1) + delta;
  if (idx < r.lo || idx > r.hi) return;
  agenda.calY = Math.floor(idx / 12); agenda.calM = (idx % 12) + 1;
  renderFechaHora();
}

function renderCalendarioHTML() {
  const idxPorFecha = {};
  agenda.dias.forEach((d, i) => { idxPorFecha[d.fecha] = i; });
  if (!agenda.dias.length) return '';
  if (agenda.calM == null) { const p = agenda.dias[0].fecha.split('-').map(Number); agenda.calY = p[0]; agenda.calM = p[1]; }

  const r = _rangoMeses();
  const cur = agenda.calY * 12 + (agenda.calM - 1);
  const y = agenda.calY, m = agenda.calM;
  const dow = ['L', 'M', 'X', 'J', 'V', 'S', 'D'];
  const startCol = (new Date(y, m - 1, 1).getDay() + 6) % 7;   // lunes = 0
  const diasMes = new Date(y, m, 0).getDate();
  let cells = '';
  for (let k = 0; k < startCol; k++) cells += '<span class="cal-cell empty"></span>';
  for (let dn = 1; dn <= diasMes; dn++) {
    const f = `${y}-${String(m).padStart(2, '0')}-${String(dn).padStart(2, '0')}`;
    if (f in idxPorFecha) {
      const i = idxPorFecha[f];
      cells += `<button class="cal-cell avail ${i === agenda.diaSel ? 'sel' : ''}" onclick="seleccionarDiaCal(${i})">${dn}<span class="cal-dot"></span></button>`;
    } else {
      cells += `<span class="cal-cell off">${dn}</span>`;
    }
  }
  const prev = cur > r.lo
    ? `<button class="cal-nav" onclick="calNavMes(-1)" aria-label="Mes anterior"><i class="fas fa-chevron-left"></i></button>`
    : `<span class="cal-nav off"></span>`;
  const next = cur < r.hi
    ? `<button class="cal-nav" onclick="calNavMes(1)" aria-label="Mes siguiente"><i class="fas fa-chevron-right"></i></button>`
    : `<span class="cal-nav off"></span>`;
  const d = agenda.dias[agenda.diaSel];
  return `<div class="agenda-cal">
      <div class="agenda-cal-head">${prev}<p class="agenda-cal-month">${_MESES[m - 1]} ${y}</p>${next}</div>
      <div class="agenda-cal-dow">${dow.map(x => `<span>${x}</span>`).join('')}</div>
      <div class="agenda-cal-grid">${cells}</div>
    </div>
    <p class="agenda-sub2">${d ? d.legible : ''}</p>
    <div class="agenda-horas-wrap">${d ? _horasJornadaHTML(d, agenda.diaSel) : ''}</div>
    <p class="agenda-cal-link"><a href="#" onclick="cerrarCalendario(event)"><i class="fas fa-list"></i> Ver como lista</a></p>`;
}

async function cargarMasFechas() {
  const btn = document.getElementById('agendaVerMas');
  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Cargando…'; }
  try {
    let cargo = false;
    // min_dias=5: el servidor junta varios dias con horas en una sola request
    // (antes: pagina tras pagina en serie hasta encontrar alguno).
    while (!cargo && agenda.diasHayMas) {
      const r = await agendaApi(_disponibilidadUrl(agenda.diasOffset, 5));
      agenda.diasOffset = r.offset_siguiente || agenda.diasOffset;
      agenda.diasHayMas = !!r.hay_mas;
      const nuevos = _filtrarDias(r.dias);
      if (nuevos.length) { agenda.dias = agenda.dias.concat(nuevos); cargo = true; }
    }
  } catch (e) { /* deja el boton */ }
  renderFechaHora();
}

function elegirHora(diaIdx, hora) {
  agenda.sel.fecha = agenda.dias[diaIdx].fecha;
  agenda.sel.fechaLegible = agenda.dias[diaIdx].legible;
  agenda.sel.hora = hora;
  if (agenda.estudio) return estudioHoraElegida();
  irAResumen();
}

/* ── Aviso de cita previa (evita doble agendamiento) ─────────────────────── */

async function irAResumen() {
  // Si el paciente ya tiene una cita activa futura y no lo ha reconocido, avisar.
  if (!agenda.citaPreviaAck && agenda.citasPreviasPromise) {
    let citas = [];
    setBody('<div class="agenda-loading"><i class="fas fa-spinner fa-spin"></i> Un momento…</div>');
    try { citas = await agenda.citasPreviasPromise; } catch (e) { citas = []; }
    // En reagenda: NO avisar de la propia cita que se está reagendando (sigue
    // activa hasta que se confirme la nueva) — solo de OTRAS citas del paciente.
    if (agenda.reagendaId) {
      citas = citas.filter(c => String(c.id_agenda) !== String(agenda.reagendaId));
    }
    if (citas && citas.length) return pasoAvisoCitaPrevia(citas);
  }
  pasoResumen();
}

function _fechaCitaLegible(f) {
  const p = (f || '').split('-');
  if (p.length !== 3) return f;
  const dias = ['domingo','lunes','martes','miércoles','jueves','viernes','sábado'];
  const meses = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'];
  const dt = new Date(+p[0], +p[1]-1, +p[2]);
  return `${dias[dt.getDay()]} ${+p[2]} de ${meses[+p[1]-1]}`;
}

function pasoAvisoCitaPrevia(citas) {
  const varias = citas.length > 1;
  const items = citas.map(c => `
    <li><span>${_fechaCitaLegible(c.fecha)} · ${c.hora} hrs</span><b>${c.profesional}${c.motivo ? ' — ' + c.motivo : ''}</b></li>`).join('');
  setBody(`
    <div class="agenda-aviso" style="background:#fff7e6;border-color:#C9A84C;color:#7a5b00">
      <i class="fas fa-triangle-exclamation"></i> ${varias ? 'Ya tienes horas agendadas' : 'Ya tienes una hora agendada'}
    </div>
    <p class="agenda-sub">Encontramos ${varias ? 'estas reservas activas' : 'esta reserva activa'} a tu nombre:</p>
    <ul class="agenda-detalle">${items}</ul>
    <p class="agenda-sub" style="margin-top:14px">¿Seguro que quieres agendar una hora <strong>nueva</strong> además de ${varias ? 'estas' : 'esta'}?</p>
    <button class="btn btn-primary btn-lg agenda-submit" onclick="confirmarCitaNueva()">Sí, agendar otra hora</button>
    <p class="agenda-mini"><a href="#" onclick="cerrarAgenda();return false;">No, mantener solo la que tengo</a></p>`);
}

function confirmarCitaNueva() {
  agenda.citaPreviaAck = true;   // no volver a preguntar en esta sesión
  pasoResumen();
}

/* ── Resumen + confirmar ─────────────────────────────────────────────────── */

// En modo link el wizard NUNCA conoce el RUT ni el email del paciente (viven
// server-side detrás del token): se muestra solo lo enmascarado que devolvió
// link-info, sin la fila de RUT.
function _resumenPacienteHTML() {
  const s = agenda.sel;
  if (agenda.linkExacto) {
    const p = agenda.linkDatos || {};
    return p.nombres ? `<li><span>Paciente</span><b>${p.nombres}</b></li>` : '';
  }
  return `<li><span>Paciente</span><b>${s.datos.nombres} ${s.datos.apellidos}</b></li>
      <li><span>RUT</span><b>${s.rutFmt}</b></li>`;
}

function _resumenTelefono() {
  const s = agenda.sel;
  if (agenda.linkExacto) return (agenda.linkDatos || {}).telefono_masked || '—';
  return s.datos.telefono_masked || s.datos.telefono || '—';
}

function pasoResumen() {
  const s = agenda.sel;
  setBody(`<button class="agenda-back" onclick="pasoFechaHora()"><i class="fas fa-arrow-left"></i> Volver</button>
    <h3 class="agenda-q">Revisa y confirma</h3>
    <div class="agenda-resumen">
      <img src="${s.doctorFoto}" alt="">
      <div>
        <strong>${s.doctorNombre}</strong>
        <span>${s.motivoLabel}</span>
        <span><i class="fas fa-calendar"></i> ${s.fechaLegible} · ${s.hora} hrs</span>
      </div>
    </div>
    <ul class="agenda-detalle">
      ${_resumenPacienteHTML()}
      <li><span>Especialidad</span><b>${s.especialidadLabel}</b></li>
      <li><span>Celular</span><b>${_resumenTelefono()}</b></li>
    </ul>
    ${agenda.config.turnstile_sitekey ? '<div id="agenda-captcha" style="margin:14px 0;display:flex;justify-content:center"></div>' : ''}
    <button class="btn btn-primary btn-lg agenda-submit" id="agendaConfirmBtn" onclick="confirmarReserva()"${agenda.config.turnstile_sitekey ? ' disabled' : ''}>
      <i class="fas fa-check"></i> Confirmar hora
    </button>
    <p class="agenda-mini">Recibirás un email de confirmación con un archivo para agregar la cita a tu calendario.</p>`);
  montarCaptcha();
}

/* ── Captcha Cloudflare Turnstile (anti-bot) ─────────────────────────────── */

agenda.captchaToken = '';

function montarCaptcha() {
  const sitekey = agenda.config.turnstile_sitekey;
  if (!sitekey) return;
  agenda.captchaToken = '';
  const render = () => {
    const el = document.getElementById('agenda-captcha');
    if (!el || !window.turnstile) return;
    window.turnstile.render(el, {
      sitekey,
      callback: (tok) => {
        agenda.captchaToken = tok;
        const btn = document.getElementById('agendaConfirmBtn');
        if (btn) btn.disabled = false;
      },
      'expired-callback': () => { agenda.captchaToken = ''; },
    });
  };
  if (window.turnstile) render();
  else {
    // El script puede no haber cargado aún: reintentar brevemente.
    let intentos = 0;
    const iv = setInterval(() => {
      if (window.turnstile || intentos++ > 40) { clearInterval(iv); render(); }
    }, 100);
  }
}

async function confirmarReserva() {
  const btn = document.getElementById('agendaConfirmBtn');
  btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Agendando…';
  const s = agenda.sel;
  try {
    // reagendaExacto: preserva el motivo y la duración ORIGINALES de la cita
    // vieja (endpoint dedicado) -- no el flujo normal, que dejaría al
    // paciente "eligiendo" un motivo del menú online.
    // Modo link: NO se manda rut/nombres/email — el backend los resuelve desde
    // el token (así el link nunca lleva datos del paciente en la URL).
    const r = agenda.linkExacto
      ? await agendaApi('/api/agenda/reservar', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            link_token: agenda.linkToken, fecha: s.fecha, hora: s.hora,
            captcha_token: agenda.captchaToken || '',
          }),
        })
      : agenda.reagendaExacto
      ? await agendaApi('/api/agenda/reservar-reagenda', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            id_agenda: agenda.reagendaId, fecha_original: agenda.reagendaFecha,
            doctor: s.doctor, fecha: s.fecha, hora: s.hora, rut: s.rut,
            nombres: s.datos.nombres, apellidos: s.datos.apellidos,
            email: s.datos.email || '', telefono: s.datos.telefono || '',
            captcha_token: agenda.captchaToken || '',
          }),
        })
      : await agendaApi('/api/agenda/reservar', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            especialidad: s.especialidad, doctor: s.doctor, motivo: s.motivo,
            fecha: s.fecha, hora: s.hora, rut: s.rut,
            nombres: s.datos.nombres, apellidos: s.datos.apellidos,
            email: s.datos.email || '', telefono: s.datos.telefono || '',
            ...(s.esNoSoyYo && {
              es_no_soy_yo: true,
              email_nuevo: s.datos.email || '',
              telefono_nuevo: s.datos.telefono || '',
            }),
            ...(s.completarDatos && { es_completar_datos: true }),
            ...(agenda.reagendaId && { reagenda_id_agenda: agenda.reagendaId }),
            captcha_token: agenda.captchaToken || '',
          }),
        });
    if (r.ok) { track('reservado'); pasoExito(r); } else pasoError(r.error || 'No se pudo agendar.');
  } catch (err) {
    pasoError((err.data && err.data.error) || 'Ocurrió un problema al agendar. Intenta por WhatsApp.');
  }
}

/* ── Estados finales ─────────────────────────────────────────────────────── */

function pasoExito(r) {
  const s = agenda.sel;
  const extraMsg = r.solicitud_cambio
    ? ' También notificamos a la clínica tu solicitud de actualizar tus datos de contacto.'
    : '';
  const titulo = r.reagenda ? '¡Tu hora fue reagendada!' : '¡Tu hora quedó agendada!';
  const introReag = r.reagenda ? 'Tu hora anterior quedó anulada. ' : '';
  setBody(`<div class="agenda-final ok">
    <i class="fas fa-circle-check"></i>
    <h3>${titulo}</h3>
    <p>${s.doctorNombre}<br>${s.fechaLegible} · ${s.hora} hrs</p>
    <p class="agenda-mini">${introReag}Te enviamos un email de confirmación con un archivo para agregar la cita a tu calendario${r.mock ? ' (modo demo)' : ''}.${extraMsg}</p>
    <button class="btn btn-primary" onclick="cerrarAgenda()">Listo</button>
  </div>`);
}

function pasoError(msg) {
  setBody(`<div class="agenda-final err">
    <i class="fas fa-circle-exclamation"></i>
    <h3>Ups…</h3>
    <p>${msg}</p>
    <a class="btn btn-primary" href="https://wa.me/56933558189?text=Hola,%20me%20gustar%C3%ADa%20agendar%20una%20hora" target="_blank" rel="noopener">
      <i class="fab fa-whatsapp"></i> Agendar por WhatsApp
    </a>
  </div>`);
}

// Reagenda que no se pudo preparar automáticamente (link sin fecha, motivo no
// reconocido, cita movida de día, etc.). NO se abre el wizard libre — se deriva
// a WhatsApp para que la clínica reagende con el MISMO motivo, sin riesgo de que
// el paciente termine con un tipo de cita distinto. La cita vieja NO se toca.
function pasoReagendaNoDisponible() {
  const fechaTxt = agenda.reagendaFecha ? ` (mi hora del ${agenda.reagendaFecha})` : '';
  const wa = 'https://wa.me/56933558189?text=' +
    encodeURIComponent(`Hola, quiero reagendar mi hora${fechaTxt}. ¿Me pueden ayudar a coordinar una nueva?`);
  setBody(`<div class="agenda-final ok">
    <i class="fas fa-comments"></i>
    <h3>Reagendemos tu hora juntos</h3>
    <p>Para esta hora preferimos coordinar el nuevo horario contigo directamente, así nos aseguramos de mantener el mismo tipo de atención.</p>
    <a class="btn btn-primary btn-lg" href="${wa}" target="_blank" rel="noopener">
      <i class="fab fa-whatsapp"></i> Escríbenos por WhatsApp
    </a>
    <p class="agenda-mini">Tu hora actual sigue agendada hasta que coordinemos la nueva.</p>
  </div>`);
}

window.abrirAgenda = abrirAgenda;
window.cerrarAgenda = cerrarAgenda;

// Precargar la config + los primeros días de todos los doctores en segundo plano
// (sin bloquear la carga de la página). Así el modal abre instantáneo y la 1a
// consulta de cualquier doctor ya está caliente.
(function () {
  const warm = () => precargarConfig().catch(() => {});
  if ('requestIdleCallback' in window) requestIdleCallback(warm, { timeout: 3000 });
  else setTimeout(warm, 1500);
})();

// Link directo: ortodonciarichard.cl/#reservar (o #agendar) abre el agendamiento
// al instante. Ideal para el sticker de link de historias o el link de la bio.
// #reagendar=<id_agenda>&fecha=<fecha> abre igual, pero marca esta sesión como
// reagendamiento: si viene con fecha, abrirAgenda() intenta precargar doctor
// y motivo de la cita vieja (ver /api/agenda/reagendar-info) y salta directo a
// elegir hora. Al completar la reserva, el backend marca la cita vieja como
// "Re-agendado" (y la mueve fuera de horario para liberar su bloque).
function _abrirDesdeHash() {
  const hash = location.hash || '';
  const reag = hash.match(/^#reagendar=(\d+)/i);
  if (reag) {
    agenda.reagendaId = reag[1];
    const fm = hash.match(/[?&]fecha=(\d{4}-\d{2}-\d{2})/i);
    agenda.reagendaFecha = fm ? fm[1] : null;
    // Entrar (o reentrar, si llega otro link sin haber cerrado) en modo
    // reagenda: forzar que abrirAgenda vuelva a consultar reagendar-info en
    // vez de reusar un doctor/motivo precargado de otra cita.
    agenda.reagendaExacto = false;
    agenda._reagendaInfoIntentado = false;
    abrirAgenda();
    return;
  }
  // #cita=<token>: link pre-cargado que la secretaria generó desde el F2.
  const lk = hash.match(/^#cita=([A-Za-z0-9_-]{8,32})/);
  if (lk) {
    agenda.linkToken = lk[1];
    agenda.linkExacto = false;   // forzar que abrirAgenda resuelva el token
    abrirAgenda();
  } else if (/^#(reservar|agendar)/i.test(hash)) {
    abrirAgenda();
  }
}
window.addEventListener('load', _abrirDesdeHash);
window.addEventListener('hashchange', _abrirDesdeHash);
