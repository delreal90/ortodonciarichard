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
  },
  dias: [],
  prefetch: {},   // cache de promesas de disponibilidad por doctor|motivo
};

async function agendaApi(path, opts) {
  const res = await fetch(AGENDA_API + path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error('HTTP ' + res.status), { data });
  return data;
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
  if (!agenda.config) {
    setBody('<div class="agenda-loading"><i class="fas fa-spinner fa-spin"></i> Cargando…</div>');
    try { await precargarConfig(); }
    catch (e) { return pasoError('No pudimos conectar con la agenda online. Te recomendamos agendar por WhatsApp.'); }
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
  };
  agenda.prefetch = {};   // descartar disponibilidad precargada (puede quedar obsoleta)
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
  pasoRut();
}

/* ── Paso 2: RUT ─────────────────────────────────────────────────────────── */

function pasoRut() {
  setPaso(2);
  setBody(`<button class="agenda-back" onclick="pasoEspecialidad()"><i class="fas fa-arrow-left"></i> Volver</button>
    <h3 class="agenda-q">Tu RUT</h3>
    <p class="agenda-sub">Lo usamos para reconocerte si ya eres paciente.</p>
    <form class="agenda-form" onsubmit="return continuarRut(event)">
      <input id="agendaRut" inputmode="numeric" placeholder="12.345.678-9" autocomplete="off"
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
  } catch (err) { agenda.sel.existe = false; }
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

function confirmarReconocido() {
  // No tocamos email/telefono: el backend usa los registrados (dedup por RUT+email).
  pasoProfesional();
}

function noSoyYo(e) {
  e.preventDefault();
  agenda.sel.esNoSoyYo = true;
  agenda.sel.completarDatos = false;
  agenda.sel.datosOriginales = { ...agenda.sel.datos };
  agenda.sel.existe = false;
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
  pasoProfesional();
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
  pasoMotivo();
}

/* ── Paso 5: motivo (filtrado por especialidad) ──────────────────────────── */

function pasoMotivo() {
  setPaso(5);
  const motivos = agenda.config.motivos.filter(m => m.especialidad === agenda.sel.especialidad);
  const items = motivos.map(m => `
    <button class="agenda-option" onclick="elegirMotivo('${m.key}', this)">
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
    <div class="agenda-options">${items}</div>`);

  // Precarga: mientras el paciente lee y elige el motivo, vamos pidiendo en
  // segundo plano la disponibilidad del doctor para cada motivo. Cuando elija,
  // las horas ya suelen estar listas -> aparecen al instante.
  motivos.forEach(m => prefetchDisponibilidad(agenda.sel.doctor, m.key));
}

/* ── Precarga de disponibilidad ──────────────────────────────────────────── */

function _dispoKey(doctor, motivo) { return doctor + '|' + motivo; }

function prefetchDisponibilidad(doctor, motivo) {
  const key = _dispoKey(doctor, motivo);
  if (!agenda.prefetch[key]) {
    agenda.prefetch[key] = agendaApi(
      `/api/agenda/disponibilidad?doctor=${doctor}&motivo=${motivo}&offset=0`
    ).catch(err => { delete agenda.prefetch[key]; throw err; });  // permitir reintento
  }
  return agenda.prefetch[key];
}
function elegirMotivo(key, el) {
  agenda.sel.motivo = key;
  agenda.sel.motivoLabel = el.querySelector('span').textContent;
  const m = (agenda.config.motivos || []).find(x => x.key === key);
  agenda.sel.motivoUrgencia = !!(m && m.urgencia);
  pasoFechaHora();
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

async function pasoFechaHora() {
  setPaso(6);
  agenda.dias = [];
  agenda.diasOffset = 0;
  agenda.diasHayMas = false;
  setBody(`<button class="agenda-back" onclick="pasoMotivo()"><i class="fas fa-arrow-left"></i> Volver</button>
    <div class="agenda-doctor-head">
      <img src="${agenda.sel.doctorFoto}" alt="">
      <div><strong>${agenda.sel.doctorNombre}</strong><span>${agenda.sel.motivoLabel}</span></div>
    </div>
    <div class="agenda-loading"><i class="fas fa-spinner fa-spin"></i> Buscando horas disponibles…</div>`);
  let r;
  try {
    // Usa la precarga iniciada en pasoMotivo (suele estar lista -> instantáneo).
    r = await prefetchDisponibilidad(agenda.sel.doctor, agenda.sel.motivo);
  } catch (e) { return pasoError('No pudimos cargar la disponibilidad.'); }
  agenda.dias = r.dias || [];
  agenda.diasOffset = r.offset_siguiente || 0;
  agenda.diasHayMas = !!r.hay_mas;
  // Si la primera pagina vino vacia pero hay mas, seguir cargando
  while (!agenda.dias.length && agenda.diasHayMas) {
    const r2 = await agendaApi(`/api/agenda/disponibilidad?doctor=${agenda.sel.doctor}&motivo=${agenda.sel.motivo}&offset=${agenda.diasOffset}`);
    agenda.dias = agenda.dias.concat(r2.dias || []);
    agenda.diasOffset = r2.offset_siguiente || agenda.diasOffset;
    agenda.diasHayMas = !!r2.hay_mas;
  }
  if (!agenda.dias.length) {
    return pasoError('No hay horas disponibles en este momento. Escríbenos por WhatsApp y te ayudamos.');
  }
  renderFechaHora();
}

function renderFechaHora() {
  const diasHtml = agenda.dias.map((d, i) => `
    <div class="agenda-dia">
      <h4>${d.legible}</h4>
      <div class="agenda-horas">
        ${d.horas.map(h => `<button class="agenda-hora" onclick="elegirHora(${i}, '${h}')">${h}</button>`).join('')}
      </div>
    </div>`).join('');
  const masBtn = agenda.diasHayMas
    ? `<button class="agenda-vermas" id="agendaVerMas" onclick="cargarMasFechas()">Ver más fechas <i class="fas fa-chevron-down"></i></button>`
    : '';
  setBody(`<button class="agenda-back" onclick="pasoMotivo()"><i class="fas fa-arrow-left"></i> Volver</button>
    <div class="agenda-doctor-head">
      <img src="${agenda.sel.doctorFoto}" alt="">
      <div><strong>${agenda.sel.doctorNombre}</strong><span>${agenda.sel.motivoLabel}</span></div>
    </div>
    ${avisoUrgenciaHTML()}
    <h3 class="agenda-q">Elige día y hora</h3>
    <div class="agenda-dias">${diasHtml}</div>
    ${masBtn}`);
}

async function cargarMasFechas() {
  const btn = document.getElementById('agendaVerMas');
  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Cargando…'; }
  try {
    let cargo = false;
    // Cargar paginas hasta encontrar al menos un dia con horas o agotar la ventana
    while (!cargo && agenda.diasHayMas) {
      const r = await agendaApi(`/api/agenda/disponibilidad?doctor=${agenda.sel.doctor}&motivo=${agenda.sel.motivo}&offset=${agenda.diasOffset}`);
      agenda.diasOffset = r.offset_siguiente || agenda.diasOffset;
      agenda.diasHayMas = !!r.hay_mas;
      if ((r.dias || []).length) { agenda.dias = agenda.dias.concat(r.dias); cargo = true; }
    }
  } catch (e) { /* deja el boton */ }
  renderFechaHora();
}

function elegirHora(diaIdx, hora) {
  agenda.sel.fecha = agenda.dias[diaIdx].fecha;
  agenda.sel.fechaLegible = agenda.dias[diaIdx].legible;
  agenda.sel.hora = hora;
  pasoResumen();
}

/* ── Resumen + confirmar ─────────────────────────────────────────────────── */

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
      <li><span>Paciente</span><b>${s.datos.nombres} ${s.datos.apellidos}</b></li>
      <li><span>RUT</span><b>${s.rutFmt}</b></li>
      <li><span>Especialidad</span><b>${s.especialidadLabel}</b></li>
      <li><span>Celular</span><b>${s.datos.telefono_masked || s.datos.telefono || '—'}</b></li>
    </ul>
    <button class="btn btn-primary btn-lg agenda-submit" id="agendaConfirmBtn" onclick="confirmarReserva()">
      <i class="fas fa-check"></i> Confirmar hora
    </button>
    <p class="agenda-mini">Recibirás un email de confirmación con un archivo para agregar la cita a tu calendario.</p>`);
}

async function confirmarReserva() {
  const btn = document.getElementById('agendaConfirmBtn');
  btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Agendando…';
  const s = agenda.sel;
  try {
    const r = await agendaApi('/api/agenda/reservar', {
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
      }),
    });
    if (r.ok) pasoExito(r); else pasoError(r.error || 'No se pudo agendar.');
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
  setBody(`<div class="agenda-final ok">
    <i class="fas fa-circle-check"></i>
    <h3>¡Tu hora quedó agendada!</h3>
    <p>${s.doctorNombre}<br>${s.fechaLegible} · ${s.hora} hrs</p>
    <p class="agenda-mini">Te enviamos un email de confirmación con un archivo para agregar la cita a tu calendario${r.mock ? ' (modo demo)' : ''}.${extraMsg}</p>
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

window.abrirAgenda = abrirAgenda;
window.cerrarAgenda = cerrarAgenda;

// Precargar la config en segundo plano (sin bloquear la carga de la página).
// Asi el modal abre instantaneo cuando el paciente hace click en "Agendar".
(function () {
  const warm = () => precargarConfig().catch(() => {});
  if ('requestIdleCallback' in window) requestIdleCallback(warm, { timeout: 3000 });
  else setTimeout(warm, 1500);
})();
