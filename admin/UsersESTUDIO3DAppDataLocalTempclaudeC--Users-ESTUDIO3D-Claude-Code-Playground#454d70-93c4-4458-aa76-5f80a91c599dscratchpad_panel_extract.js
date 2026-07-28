
// ── Estado ───────────────────────────────────────────
let equipoData = [], casosData = [], faqData = [];
let currentFaqTab = '', currentEquipoTab = '';

// ── Navegación ───────────────────────────────────────
function show(sec) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('nav a').forEach(a => a.classList.remove('active'));
  document.getElementById('sec-' + sec).classList.add('active');
  document.getElementById('nav-' + sec).classList.add('active');
  if (sec === 'fotos') loadFotos();
  if (sec === 'info') loadInfo();
  if (sec === 'equipo') loadEquipo();
  if (sec === 'casos') loadCasos();
  if (sec === 'faq') loadFaq();
  if (sec === 'cv') loadCV();
  if (sec === 'agenda') loadAgenda();
  if (sec === 'estadisticas') initStats();
  if (sec === 'consentimientos') initConsentimientos();
  if (sec === 'whatsapp') initWhatsapp();
  if (sec === 'seguros') initSeguros();
  if (sec === 'controldental') initControlDental();
  if (sec === 'satisfaccion') initSatisfaccion();
}

// ══════════════════════════════════════════════════════
// REMOTO — helpers compartidos por las 6 pestañas "remotas" (hablan directo
// con el backend en Render usando el admin token guardado en localStorage):
// Estadísticas, Consentimientos, WhatsApp, Seguros, Control dental y
// Satisfacción. Antes cada una reimplementaba por su cuenta el mismo juego de
// funciones (leer los inputs, armar los headers, manejar el 403 y el error de
// red); ahora la lógica vive UNA sola vez acá y cada pestaña arma sus propios
// `_xUrl/_xToken/...` como llamadas de una línea a estas cuatro funciones,
// pasando el id de SUS inputs (cada pestaña conserva los suyos en el HTML).
// ══════════════════════════════════════════════════════

// Las 6 pestañas comparten las MISMAS claves de localStorage
// ('stats_token'/'stats_url'), a propósito: así el token que la secretaria
// guarda en una pestaña sirve en las otras cinco sin reingresarlo. Antes
// WhatsApp usaba sus propias claves (wa_token/wa_url) — cambiar el token en
// Estadísticas no se enteraba WhatsApp, y parecía que el token "no servía".
// (initWhatsapp() todavía migra lo que hubiera quedado guardado bajo esas
// claves viejas, ver esa función más abajo — no se toca esa migración.)
function remotoUrl(idInputUrl) {
  return document.getElementById(idInputUrl).value.trim().replace(/\/$/, '');
}
function remotoToken(idInputToken) {
  return document.getElementById(idInputToken).value.trim();
}
function remotoHeaders(idInputToken, json = true) {
  const h = { 'X-Admin-Token': remotoToken(idInputToken) };
  if (json) h['Content-Type'] = 'application/json';
  return h;
}
// fetch() ya armado con la URL completa + el header del token. Devuelve el
// Response CRUDO (no el JSON parseado) para que cada pestaña decida si
// necesita revisar `r.status === 403` antes de leer el body — ese chequeo de
// "token incorrecto" es el que comparten las 6, no lo que hacen con la
// respuesta después.
function remotoFetch(idInputUrl, idInputToken, ruta, opciones = {}) {
  const { method = 'GET', body = null, json = true } = opciones;
  const opts = { method, headers: remotoHeaders(idInputToken, json) };
  if (body != null) opts.body = JSON.stringify(body);
  return fetch(remotoUrl(idInputUrl) + ruta, opts);
}
// initX() de cada pestaña: restaura token/URL guardados y, si hay token,
// dispara la carga inicial.
function remotoInit(idInputUrl, idInputToken, alCargar) {
  const t = localStorage.getItem('stats_token') || '';
  const u = localStorage.getItem('stats_url') || 'https://ortodonciarichard.onrender.com';
  document.getElementById(idInputToken).value = t;
  document.getElementById(idInputUrl).value = u;
  if (t) alCargar();
}

// ══════════════════════════════════════════════════════
// ESTADÍSTICAS DE AGENDAMIENTO
// ══════════════════════════════════════════════════════
function _statsUrl()   { return remotoUrl('stats-url'); }
function _statsToken() { return remotoToken('stats-token'); }

function initStats() {
  remotoInit('stats-url', 'stats-token', loadStats);
}

async function loadStats() {
  const cont = document.getElementById('stats-resultado');
  const token = _statsToken();
  if (!token) { toast('Ingresa el admin token', false); return; }
  localStorage.setItem('stats_token', token);
  localStorage.setItem('stats_url', _statsUrl());
  cont.innerHTML = '<div class="card">Cargando…</div>';
  let d;
  try {
    const r = await remotoFetch('stats-url', 'stats-token', '/api/agenda/stats', { json: false });
    if (r.status === 403) { cont.innerHTML = '<div class="card" style="color:#e53e3e">Token incorrecto.</div>'; return; }
    d = await r.json();
  } catch (e) {
    cont.innerHTML = '<div class="card" style="color:#e53e3e">No se pudo conectar con el backend.</div>';
    return;
  }
  if (!d.ok) { cont.innerHTML = '<div class="card" style="color:#e53e3e">Error al cargar las estadísticas.</div>'; return; }
  renderStats(d);
  loadUltimasCitas();
}

async function loadUltimasCitas() {
  const cont = document.getElementById('stats-ultimas');
  if (!cont) return;
  cont.innerHTML = 'Cargando…';
  try {
    const r = await remotoFetch('stats-url', 'stats-token', '/api/agenda/stats/citas?n=20', { json: false });
    const d = await r.json();
    if (!d.ok) { cont.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">Error al cargar.</p>'; return; }
    renderUltimasCitas(d.citas);
  } catch (e) {
    cont.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">No se pudo conectar.</p>';
  }
}

function renderUltimasCitas(citas) {
  const cont = document.getElementById('stats-ultimas');
  if (!citas || !citas.length) { cont.innerHTML = '<p style="color:#718096;font-size:.85rem">Sin reservas aún.</p>'; return; }
  // El ts se guarda en hora de Chile; mostramos "YYYY-MM-DD HH:MM" (sin
  // segundos ni offset), tomando solo los primeros 16 caracteres del ISO.
  const fmtTs = ts => (ts || '').slice(0, 16).replace('T', ' ');
  const filas = citas.map(c => `
    <tr>
      <td style="padding:6px 8px;font-size:.82rem">${fmtTs(c.ts)}</td>
      <td style="padding:6px 8px;font-size:.82rem">${_esc(c.paciente_nombre || '—')} <span style="color:#a0aec0">(${c.paciente_conocido ? 'conocido' : 'nuevo'})</span></td>
      <td style="padding:6px 8px;font-size:.82rem">${_esc(c.fecha || '—')} ${_esc(c.hora || '')}</td>
      <td style="padding:6px 8px;font-size:.82rem">${_esc(c.doctor_nombre || '—')}</td>
      <td style="padding:6px 8px;font-size:.82rem">${_esc(c.motivo_label || '—')}</td>
      <td style="padding:6px 8px"><button class="btn" style="padding:4px 10px;font-size:.78rem;color:#e53e3e;border-color:#e53e3e" onclick="eliminarCita('${_esc(c.ts)}', this)">Eliminar</button></td>
    </tr>`).join('');
  cont.innerHTML = `
    <table style="width:100%;border-collapse:collapse">
      <thead><tr style="border-bottom:2px solid #edf2f7;text-align:left">
        <th style="padding:6px 8px;font-size:.78rem;color:#718096">Registrado</th>
        <th style="padding:6px 8px;font-size:.78rem;color:#718096">Paciente</th>
        <th style="padding:6px 8px;font-size:.78rem;color:#718096">Cita</th>
        <th style="padding:6px 8px;font-size:.78rem;color:#718096">Doctor</th>
        <th style="padding:6px 8px;font-size:.78rem;color:#718096">Motivo</th>
        <th></th>
      </tr></thead>
      <tbody>${filas}</tbody>
    </table>`;
}

async function eliminarCita(ts, btn) {
  if (!confirm('¿Eliminar esta reserva del registro de estadísticas? No afecta la agenda real ni DentiDesk, solo las estadísticas.')) return;
  btn.disabled = true; btn.textContent = 'Eliminando…';
  try {
    const r = await remotoFetch('stats-url', 'stats-token', '/api/agenda/stats/citas', { method: 'DELETE', body: { ts } });
    const d = await r.json();
    if (!d.ok || !d.eliminados) { toast('No se pudo eliminar', false); btn.disabled = false; btn.textContent = 'Eliminar'; return; }
    toast('Eliminada del registro');
    loadStats();
  } catch (e) {
    toast('No se pudo conectar', false);
    btn.disabled = false; btn.textContent = 'Eliminar';
  }
}

// ── Base de pacientes (resembrar desde el Excel de DentiDesk) ──
async function importarPacientes() {
  const fileEl = document.getElementById('pac-archivo');
  const reemplazar = document.getElementById('pac-reemplazar').checked;
  const btn = document.getElementById('pac-btn-importar');
  const out = document.getElementById('pac-resultado');
  const token = _statsToken();
  if (!token) { toast('Ingresa el admin token', false); return; }
  if (!fileEl.files.length) { toast('Elige el archivo .xlsx primero', false); return; }

  const form = new FormData();
  form.append('file', fileEl.files[0]);
  form.append('reemplazar', reemplazar ? 'true' : 'false');

  btn.disabled = true;
  btn.textContent = 'Importando…';
  out.innerHTML = '<span style="color:#718096">Importando… puede tardar, el archivo pesa varios MB.</span>';
  try {
    // OJO: sin Content-Type a mano — el navegador lo pone solo (con el boundary
    // del multipart). remotoFetch() no sirve acá porque siempre manda JSON
    // (JSON.stringify reventaría un FormData), así que se arma el fetch a mano.
    const r = await fetch(_statsUrl() + '/api/pacientes/importar', {
      method: 'POST',
      headers: { 'X-Admin-Token': token },
      body: form,
    });
    const d = await r.json();
    if (d.ok) {
      out.innerHTML = `<span style="color:#2f855a">✅ ${d.total.toLocaleString('es-CL')} pacientes en la base, ${d.nuevos.toLocaleString('es-CL')} nuevos.</span>`;
      toast('✅ Base de pacientes actualizada');
      fileEl.value = '';
    } else {
      out.innerHTML = `<span style="color:#e53e3e">❌ ${_esc(d.error || 'Error al importar')}</span>`;
    }
  } catch (e) {
    out.innerHTML = `<span style="color:#e53e3e">No se pudo conectar con el backend: ${_esc(e.message)}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Importar';
  }
}

function _barras(items, color) {
  if (!items || !items.length) return '<p style="color:#718096;font-size:.85rem">Sin datos aún.</p>';
  const max = Math.max(...items.map(i => i.total), 1);
  return items.map(i => `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
      <div style="width:140px;font-size:.82rem;color:#2D3748;text-align:right;flex-shrink:0">${i.label}</div>
      <div style="flex:1;background:#edf2f7;border-radius:4px;overflow:hidden">
        <div style="width:${Math.round(i.total/max*100)}%;min-width:${i.total?'2px':'0'};background:${color};height:18px"></div>
      </div>
      <div style="width:32px;font-size:.82rem;color:#1A2E4A;font-weight:600">${i.total}</div>
    </div>`).join('');
}

function _embudoHTML(f) {
  if (!f || !f.total_sesiones) return '<div class="card"><h3>Embudo de agendamiento</h3><p style="color:#718096;font-size:.85rem">Aún no hay visitas registradas en el flujo.</p></div>';
  const navy = '#1A2E4A', gold = '#C9A84C';
  const filas = f.funnel.map((p, i) => {
    const drop = i > 0 ? 100 - p.pct_anterior : 0;
    return `<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
      <div style="width:150px;font-size:.82rem;color:#2D3748;text-align:right;flex-shrink:0">${p.label}</div>
      <div style="flex:1;background:#edf2f7;border-radius:4px;overflow:hidden">
        <div style="width:${p.pct_inicio}%;min-width:${p.sesiones?'2px':'0'};background:${navy};height:20px;display:flex;align-items:center;padding-left:6px;color:#fff;font-size:.72rem">${p.pct_inicio}%</div>
      </div>
      <div style="width:44px;font-size:.82rem;color:#1A2E4A;font-weight:600">${p.sesiones}</div>
      <div style="width:64px;font-size:.74rem;color:${drop>=40?'#e53e3e':'#a0aec0'}">${i>0?('−'+drop+'%'):''}</div>
    </div>`;
  }).join('');
  const lat = f.latencia_horas_ms_prom;
  const latTxt = f.latencia_muestras ? `${(lat/1000).toFixed(1)}s prom · ${(f.latencia_horas_ms_mediana/1000).toFixed(1)}s mediana (${f.latencia_muestras} cargas)` : 'sin datos aún';
  return `
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px">
      ${_statCard('Visitas al flujo', f.total_sesiones, navy)}
      ${_statCard('Llegaron a reservar', f.reservaron, '#2f855a')}
      ${_statCard('Conversión', f.conversion_pct + '%', gold)}
    </div>
    <div class="card" style="margin-bottom:18px">
      <h3 style="margin-bottom:6px">Embudo de agendamiento</h3>
      <p style="font-size:.8rem;color:#718096;margin-bottom:14px">% respecto al inicio · la última columna marca cuánto se cae respecto al paso anterior (rojo = caída grande).</p>
      ${filas}
    </div>
    <div class="card" style="margin-bottom:18px">
      <h3 style="margin-bottom:6px">⏱️ Tiempo de carga de horas</h3>
      <p style="font-size:1.1rem;color:#1A2E4A;font-weight:600">${latTxt}</p>
      <p style="font-size:.78rem;color:#718096">Cuánto espera el paciente entre elegir el motivo y ver las horas.</p>
    </div>
    <div class="card" style="margin-bottom:18px"><h3 style="margin-bottom:14px">¿Dónde abandonan? (último paso de quienes no reservaron)</h3>${_barras(f.abandono, '#e53e3e')}</div>`;
}

function renderStats(d) {
  const cont = document.getElementById('stats-resultado');
  const navy = '#1A2E4A', gold = '#C9A84C';
  cont.innerHTML = `
    <h3 style="margin:4px 0 14px;color:#1A2E4A">Recorrido del paciente</h3>
    ${_embudoHTML(d.funnel)}
    <h3 style="margin:24px 0 14px;color:#1A2E4A">Reservas concretadas</h3>
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px">
      ${_statCard('Total reservas', d.total, navy)}
      ${_statCard('Pacientes conocidos', d.conocidos, '#2f855a')}
      ${_statCard('Pacientes nuevos', d.nuevos, gold)}
    </div>
    <div class="card" style="margin-bottom:18px"><h3 style="margin-bottom:14px">Motivos más pedidos</h3>${_barras(d.por_motivo, navy)}</div>
    <div class="card" style="margin-bottom:18px"><h3 style="margin-bottom:14px">Por doctor</h3>${_barras(d.por_doctor, navy)}</div>
    <div class="card" style="margin-bottom:18px"><h3 style="margin-bottom:14px">Por especialidad</h3>${_barras(d.por_especialidad, gold)}</div>
    <div class="card" style="margin-bottom:18px"><h3 style="margin-bottom:4px">Día en que agendan</h3><p style="font-size:.78rem;color:#718096;margin-bottom:12px">Día de la semana en que el paciente hizo la reserva online.</p>${_barras(d.por_dia_semana, navy)}</div>
    <div class="card" style="margin-bottom:18px"><h3 style="margin-bottom:4px">Hora en que agendan</h3><p style="font-size:.78rem;color:#718096;margin-bottom:12px">Hora del día en que el paciente hizo la reserva online.</p>${_barras(d.por_hora, gold)}</div>
    <div class="card"><h3 style="margin-bottom:14px">Reservas últimos 30 días</h3>${_barras(d.timeline_30d.filter(x=>x.total>0).map(x=>({label:x.fecha.slice(5),total:x.total})), navy)}</div>`;
}

// ══════════════════════════════════════════════════════
// CONSENTIMIENTOS INFORMADOS
// ══════════════════════════════════════════════════════
let consentimientosData = [], consentFiltroActivo = '';

function _consentUrl()   { return remotoUrl('consent-url'); }
function _consentToken() { return remotoToken('consent-token'); }

function initConsentimientos() {
  // Comparte token/URL con Estadísticas: mismo ADMIN_TOKEN, mismo backend.
  remotoInit('consent-url', 'consent-token', loadConsentimientos);
}

async function loadConsentimientos() {
  const cont = document.getElementById('consent-resultado');
  const token = _consentToken();
  if (!token) { toast('Ingresa el admin token', false); return; }
  localStorage.setItem('stats_token', token);
  localStorage.setItem('stats_url', _consentUrl());
  cont.innerHTML = '<div class="card">Cargando…</div>';
  let d;
  try {
    const r = await remotoFetch('consent-url', 'consent-token', '/api/consentimientos', { json: false });
    if (r.status === 403) { cont.innerHTML = '<div class="card" style="color:#e53e3e">Token incorrecto.</div>'; return; }
    d = await r.json();
  } catch (e) {
    cont.innerHTML = '<div class="card" style="color:#e53e3e">No se pudo conectar con el backend.</div>';
    return;
  }
  if (!d.ok) { cont.innerHTML = '<div class="card" style="color:#e53e3e">Error al cargar los consentimientos.</div>'; return; }
  consentimientosData = d.items || [];
  renderConsentimientos();
}

function filtrarConsentimientos(estado, btn) {
  consentFiltroActivo = estado;
  document.querySelectorAll('#consent-filtros button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderConsentimientos();
}

const CONSENT_ESTADO_LABEL = { enviado: 'Enviado', firmado: 'Firmado', subido: 'Subido a DentiDesk' };
const CONSENT_ESTADO_COLOR = { enviado: '#a0aec0', firmado: '#C9A84C', subido: '#38a169' };
const CONSENT_CANAL_LABEL  = { mail: 'Mail', whatsapp: 'WhatsApp', tablet: 'Tablet' };
const CONSENT_TIPO_LABEL   = { ortodoncia: 'Ortodoncia', rehabilitacion: 'Rehabilitación oral' };

function _rutFmt(rut) {
  if (!rut || rut.length < 2) return rut || '—';
  const cuerpo = rut.slice(0, -1), dv = rut.slice(-1);
  return cuerpo.replace(/\B(?=(\d{3})+(?!\d))/g, '.') + '-' + dv;
}

function _fechaCorta(iso) {
  if (!iso) return '—';
  const [f, h] = iso.split('T');
  const [y, m, d] = f.split('-');
  return `${d}-${m}-${y}${h ? ' ' + h.slice(0, 5) : ''}`;
}

function renderConsentimientos() {
  const cont = document.getElementById('consent-resultado');
  const items = consentFiltroActivo ? consentimientosData.filter(i => i.estado === consentFiltroActivo) : consentimientosData;
  if (!items.length) {
    cont.innerHTML = '<div class="card"><p style="color:#718096;font-size:.85rem">No hay consentimientos que coincidan con este filtro.</p></div>';
    return;
  }
  cont.innerHTML = `<div class="card" style="padding:0;overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:.85rem;min-width:820px">
      <thead><tr style="background:#f8fafc;text-align:left">
        <th style="padding:10px 14px">RUT</th>
        <th style="padding:10px 14px">Documento</th>
        <th style="padding:10px 14px">Canal</th>
        <th style="padding:10px 14px">Estado</th>
        <th style="padding:10px 14px">Enviado</th>
        <th style="padding:10px 14px">Firmado</th>
        <th style="padding:10px 14px">Drive</th>
        <th style="padding:10px 14px">Subir a DentiDesk</th>
      </tr></thead>
      <tbody>
        ${items.map(i => `
          <tr style="border-top:1px solid #e2e8f0">
            <td style="padding:10px 14px">${_esc(_rutFmt(i.rut))}</td>
            <td style="padding:10px 14px">${_esc(CONSENT_TIPO_LABEL[i.tipo] || i.tipo)}</td>
            <td style="padding:10px 14px">${_esc(CONSENT_CANAL_LABEL[i.canal] || i.canal)}</td>
            <td style="padding:10px 14px"><span class="tag" style="border-color:${CONSENT_ESTADO_COLOR[i.estado] || '#a0aec0'};color:${CONSENT_ESTADO_COLOR[i.estado] || '#a0aec0'}">${_esc(CONSENT_ESTADO_LABEL[i.estado] || i.estado)}</span></td>
            <td style="padding:10px 14px;color:#718096">${_esc(_fechaCorta(i.creado))}</td>
            <td style="padding:10px 14px;color:#718096">${i.firmado ? _esc(_fechaCorta(i.firmado)) : '—'}</td>
            <td style="padding:10px 14px">${_celdaDrive(i)}</td>
            <td style="padding:8px 14px;white-space:normal">${_accionesConsent(i)}</td>
          </tr>`).join('')}
      </tbody>
    </table>
  </div>
  <p style="font-size:.78rem;color:#718096;margin-top:10px">
    <strong>Abrir en DentiDesk</strong> te lleva al paciente (busca por RUT). Entra a la pestaña <em>Informes</em>, click en <em>Subir</em> y elige el PDF.
    El nombre del archivo aparece al pasar el mouse sobre el botón. Luego marca <strong>Ya lo subí</strong> para llevar la cuenta.
  </p>`;
}

const DENTIDESK_BASE = 'https://app.dentidesk.cl';

// Escapa texto para insertarlo con seguridad en HTML/atributos (anti-XSS).
// Todos los valores que vienen del backend deben pasar por aquí antes de ir a
// innerHTML — así, aunque un valor malicioso llegara al registro, no se ejecuta.
function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function _pdfNombre(i) {
  // El PDF se guarda como RUT_tipo_fecha.pdf; si tenemos pdf_path, mostramos el basename.
  if (i.pdf_path) { const p = i.pdf_path.replace(/\\\\/g,'/').split('/'); return p[p.length-1]; }
  return `${i.rut}_${i.tipo}.pdf`;
}

function _celdaDrive(i) {
  const fileId = (i.drive_file_id || '').replace(/[^a-zA-Z0-9_-]/g, '');
  if (fileId) {
    return `<button class="btn btn-sm" style="background:#e6f4ea;color:#1e7e34;white-space:nowrap"
      onclick="abrirEnDrive('${fileId}')">✅ Abrir en Drive</button>`;
  }
  if (i.respaldo_drive === false) return '⚠️';
  return '—';
}

function _accionesConsent(i) {
  const idLimpio = (i.id || '').replace(/[^a-zA-Z0-9]/g,'');    // uuid hex

  if (i.estado === 'enviado') {
    // Aún no firmado: se puede borrar el registro (no hay documento legal que preservar).
    return `<span style="color:#a0aec0">Aún sin firmar</span>
      <button class="btn btn-sm" style="background:#fed7d7;color:#c53030;margin-left:6px"
        onclick="borrarConsent('${idLimpio}')">Borrar</button>`;
  }

  // A partir de aquí, 'firmado' o 'subido': ya existe un documento firmado —
  // el registro NUNCA se borra desde este botón (lo valida también el backend).
  const rutLimpio = (i.rut || '').replace(/[^0-9kK]/g,'');       // solo dígitos/K
  const reenviarBtn = `<button class="btn btn-sm" id="btn-reenviar-${idLimpio}" style="background:#e2e8f0;color:#2d3748;margin-left:6px"
      onclick="reenviarCopia('${idLimpio}')">Reenviar copia</button>`;

  if (i.estado === 'subido') {
    return `<span style="color:#38a169;font-weight:600">✓ Subido</span>${reenviarBtn}`;
  }
  return `
    <button class="btn btn-primary btn-sm" title="Archivo a elegir: ${_esc(_pdfNombre(i))}"
      onclick="abrirEnDentidesk('${rutLimpio}')">Abrir en DentiDesk</button>
    <button class="btn btn-sm" style="background:#e2e8f0;color:#2d3748;margin-left:6px"
      onclick="marcarConsentSubido('${idLimpio}')">Ya lo subí</button>${reenviarBtn}`;
}

function abrirEnDentidesk(rut) {
  window.open(`${DENTIDESK_BASE}/pacientes.php?rut=${encodeURIComponent(rut)}`, '_blank');
}

function abrirEnDrive(fileId) {
  window.open(`https://drive.google.com/file/d/${encodeURIComponent(fileId)}/view`, '_blank');
}

async function reenviarCopia(id) {
  const btn = document.getElementById('btn-reenviar-' + id);
  if (btn) { btn.disabled = true; btn.textContent = 'Enviando...'; }
  try {
    const r = await remotoFetch('consent-url', 'consent-token', '/api/consentimiento/reenviar-copia', { method: 'POST', body: { id } });
    const j = await r.json();
    if (j.ok) {
      toast('✅ Copia reenviada a ' + (j.email_enmascarado || 'el paciente'));
    } else {
      toast('❌ ' + (j.error || 'No se pudo reenviar'), false);
    }
  } catch (e) {
    toast('❌ Error de conexión', false);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Reenviar copia'; }
  }
}

async function borrarConsent(id) {
  if (!confirm('¿Borrar este registro de consentimiento (aún no firmado)? Esta acción no se puede deshacer.')) return;
  try {
    const r = await remotoFetch('consent-url', 'consent-token', '/api/consentimiento/borrar', { method: 'POST', body: { id } });
    const j = await r.json();
    if (j.ok) {
      toast('🗑️ Consentimiento borrado');
      consentimientosData = consentimientosData.filter(c => c.id !== id);
      renderConsentimientos();
    } else {
      toast('❌ ' + (j.error || 'No se pudo borrar'), false);
    }
  } catch (e) {
    toast('❌ Error de conexión', false);
  }
}

async function marcarConsentSubido(id) {
  try {
    const r = await remotoFetch('consent-url', 'consent-token', '/api/consentimiento/marcar-subido', { method: 'POST', body: { id } });
    const j = await r.json();
    if (j.ok) {
      toast('✅ Marcado como subido');
      const item = consentimientosData.find(c => c.id === id);
      if (item) item.estado = 'subido';
      renderConsentimientos();
    } else {
      toast('❌ ' + (j.error || 'No se pudo marcar'), false);
    }
  } catch (e) {
    toast('❌ Error de conexión', false);
  }
}

function _statCard(label, valor, color) {
  return `<div class="card" style="flex:1;min-width:160px;text-align:center;border-top:3px solid ${color}">
    <div style="font-size:2rem;font-weight:700;color:${color}">${valor}</div>
    <div style="font-size:.82rem;color:#718096">${label}</div>
  </div>`;
}

// ══════════════════════════════════════════════════════
// WHATSAPP — recordatorios automáticos
// ══════════════════════════════════════════════════════
// _waUrl/_waToken/_waHeaders son delegados de una línea a remotoUrl/remotoToken/
// remotoHeaders (ver el bloque "REMOTO" antes de Estadísticas) — la lógica de
// leer los inputs y armar los headers vive una sola vez ahí.
function _waUrl()   { return remotoUrl('wa-url'); }
function _waToken() { return remotoToken('wa-token'); }
function _waHeaders(json = true) { return remotoHeaders('wa-token', json); }

function initWhatsapp() {
  // ⚠️ MISMAS claves que las otras 5 pestañas remotas (Estadísticas,
  // Consentimientos, Seguros, Control dental, Satisfacción). Esta pestaña usaba
  // 'wa_token'/'wa_url' propias — consecuencia de haberla copiado sin unificar:
  // el admin cambiaba el token en Estadísticas y acá seguía el viejo, así que
  // había que reingresarlo aparte y parecía que el token "no servía".
  // Se migra lo que hubiera guardado bajo las claves viejas, una sola vez.
  // (No usa remotoInit() porque remotoInit no conoce las claves viejas wa_*.)
  const t = localStorage.getItem('stats_token') || localStorage.getItem('wa_token') || '';
  const u = localStorage.getItem('stats_url') || localStorage.getItem('wa_url')
            || 'https://ortodonciarichard.onrender.com';
  document.getElementById('wa-token').value = t;
  document.getElementById('wa-url').value = u;
  if (t) loadWhatsapp();
}

async function loadWhatsapp() {
  const estadoEl = document.getElementById('wa-estado');
  const token = _waToken();
  if (!token) { toast('Ingresa el admin token', false); return; }
  localStorage.setItem('stats_token', token);   // clave compartida por todas las pestañas remotas
  localStorage.setItem('stats_url', _waUrl());
  estadoEl.innerHTML = '<h3 style="margin-bottom:10px">Estado del sistema</h3><p style="font-size:.85rem;color:#718096">Cargando…</p>';

  // Diferencia con las otras pestañas: acá se piden 2 endpoints en paralelo y
  // se revisa el 403 en cualquiera de las dos respuestas antes de seguir
  // (config + estado de conexión con Meta). Se deja así (no se fuerza a un
  // solo remotoFetch) porque el chequeo combinado es genuino, no copy-paste.
  let cfgData, estData;
  try {
    const [rCfg, rEstado] = await Promise.all([
      remotoFetch('wa-url', 'wa-token', '/api/whatsapp/config', { json: false }),
      remotoFetch('wa-url', 'wa-token', '/api/whatsapp/estado', { json: false }),
    ]);
    if (rCfg.status === 403 || rEstado.status === 403) {
      estadoEl.innerHTML = '<h3 style="margin-bottom:10px">Estado del sistema</h3><p style="font-size:.85rem;color:#e53e3e">Token incorrecto.</p>';
      return;
    }
    cfgData = await rCfg.json();
    estData = await rEstado.json();
  } catch (e) {
    estadoEl.innerHTML = '<h3 style="margin-bottom:10px">Estado del sistema</h3><p style="font-size:.85rem;color:#e53e3e">No se pudo conectar con el backend.</p>';
    return;
  }
  if (!cfgData.ok) { toast('Error al cargar la configuración', false); return; }

  const c = cfgData.config;
  document.getElementById('wa-semana-activo').checked = !!c.recordatorio_semana.activo;
  document.getElementById('wa-semana-hora').value = c.recordatorio_semana.hora || '09:00';
  document.getElementById('wa-dia-activo').checked = !!c.recordatorio_dia.activo;
  document.getElementById('wa-dia-hora').value = c.recordatorio_dia.hora || '09:00';
  document.getElementById('wa-inasistencia-activo').checked = !!c.inasistencia_reagendar.activo;
  document.getElementById('wa-inasistencia-hora').value = c.inasistencia_reagendar.hora || '12:00';

  renderWaEstado(estData);
  loadRecaptacion();
}

// ── Recordatorios de control (recaptación) ────────────
async function loadRecaptacionConfig() {
  const url = _waUrl();
  try {
    const r = await fetch(url + '/api/recaptacion/config', { headers: _waHeaders() });
    const d = await r.json();
    if (d.ok) document.getElementById('recap-dias-minimos').value = d.config.dias_minimos_reenvio;
  } catch (e) { /* silencioso: el bloque de historial ya muestra el error de conexión */ }
}

async function guardarRecapConfig() {
  const url = _waUrl();
  const dias = parseInt(document.getElementById('recap-dias-minimos').value, 10);
  if (!dias || dias < 1) { toast('Ingresa un número de días válido', false); return; }
  try {
    const r = await fetch(url + '/api/recaptacion/config', {
      method: 'POST', headers: _waHeaders(), body: JSON.stringify({ dias_minimos_reenvio: dias }),
    });
    const d = await r.json();
    toast(d.ok ? '✅ Configuración guardada' : '❌ ' + (d.error || 'Error'), d.ok);
  } catch (e) {
    toast('❌ No se pudo conectar con el backend', false);
  }
}

// Envío de prueba de una plantilla suelta. El backend responde 200 aunque Meta
// después no entregue el mensaje (p.ej. el tope de frecuencia de las plantillas
// de marketing), así que "enviado" acá significa "Meta lo aceptó", no "llegó".
async function enviarWaTest() {
  const url  = _waUrl();
  const tel  = document.getElementById('wa-test-tel').value.trim();
  const tpl  = document.getElementById('wa-test-plantilla').value;
  const out  = document.getElementById('wa-test-res');
  if (!tel) { out.innerHTML = '<span style="color:#e53e3e">Ingresa un celular.</span>'; return; }
  out.textContent = 'Enviando…';
  try {
    const r = await fetch(url + '/api/whatsapp/test', {
      method: 'POST', headers: _waHeaders(),
      body: JSON.stringify({
        telefono: tel, plantilla: tpl,
        nombre: 'Alberto', doctor: 'Dr. Patricio Vial',
        fecha: 'martes 1 de abril del 2025', hora: '10:30',
      }),
    });
    const d = await r.json();
    out.innerHTML = d.ok
      ? '<span style="color:#2f855a">✅ Meta aceptó el envío. Revisa el celular.</span>'
      : `<span style="color:#e53e3e">${_esc(d.error || 'Error desconocido')}</span>`;
  } catch (e) {
    out.innerHTML = `<span style="color:#e53e3e">No se pudo conectar: ${_esc(e.message)}</span>`;
  }
}

// Lista las plantillas de la WABA con el largo de su cuerpo. Sirve para saber
// hasta dónde se puede estirar un texto sin que WhatsApp lo colapse con
// "Leer más": las que ya funcionan sin truncarse son el límite empírico.
// Sonda del "Leer más": manda varios mensajes de TEXTO LIBRE de largo creciente
// para ver a partir de cuántos caracteres WhatsApp colapsa el cuerpo. Meta no
// publica ese umbral y editar una plantilla aprobada cuesta una revisión, así
// que medirlo por texto libre sale gratis y no toca ninguna plantilla.
// OJO: solo funciona dentro de la ventana de 24h (el destinatario tiene que
// haber escrito o tocado un botón hace menos de un día). El texto libre no
// lleva el pie ni los botones de una plantilla, así que el umbral real de una
// plantilla puede ser algo menor — esto da el orden de magnitud, no el número
// exacto al carácter.
const LARGOS_SONDA = [180, 230, 280, 330, 380];

async function probarLargos() {
  const url = _waUrl();
  const tel = document.getElementById('wa-test-tel').value.trim();
  const out = document.getElementById('wa-plantillas');
  if (!tel) { out.innerHTML = '<span style="color:#e53e3e">Ingresa un celular.</span>'; return; }
  if (!confirm(`Se enviarán ${LARGOS_SONDA.length} mensajes de prueba a ${tel}. ¿Continuar?`)) return;

  // Relleno con frases reales del recordatorio para que las líneas corten como
  // en el mensaje de verdad (un texto artificial tipo "aaa..." envolvería
  // distinto y falsearía la prueba).
  const base = 'Su último control con el Dr. Patricio Vial fue el martes 1 de abril del 2025 y ya corresponde agendar el siguiente. Para mantener su tratamiento al día es importante seguir los controles que el doctor le indicó. Puede agendar respondiendo este mensaje o llamándonos por teléfono. Estamos aquí para atender todas sus consultas y resolver cualquier duda que tenga.';
  const filas = [];
  for (const n of LARGOS_SONDA) {
    const cabecera = `[PRUEBA ${n}] `;
    let cuerpo = base;
    while (cuerpo.length < n) cuerpo += ' ' + base;
    const texto = (cabecera + cuerpo).slice(0, n);
    try {
      const r = await fetch(url + '/api/whatsapp/test-texto-libre', {
        method: 'POST', headers: _waHeaders(),
        body: JSON.stringify({ telefono: tel, texto }),
      });
      const d = await r.json();
      filas.push(`${n} caracteres: ${d.ok ? '✅ enviado' : '❌ ' + _esc(d.error || 'error')}`);
    } catch (e) {
      filas.push(`${n} caracteres: ❌ ${_esc(e.message)}`);
    }
  }
  out.innerHTML = `<p>${filas.join('<br>')}</p>
    <p style="color:#718096;margin-top:6px">Mira el celular: cada mensaje empieza con su largo entre corchetes.
    El primero que aparezca con <b>"Leer más"</b> marca el límite. Si ninguno se trunca, el tope está más arriba de 380.</p>`;
}

async function verPlantillas() {
  const url = _waUrl();
  const out = document.getElementById('wa-plantillas');
  out.textContent = 'Consultando a Meta…';
  try {
    const r = await fetch(url + '/api/whatsapp/plantillas', { headers: _waHeaders() });
    const d = await r.json();
    if (!d.ok) { out.innerHTML = `<span style="color:#e53e3e">${_esc(d.error || 'Error')}</span>`; return; }
    if (!d.plantillas.length) { out.textContent = 'La WABA no tiene plantillas.'; return; }
    out.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:.82rem">
      <tr style="text-align:left;color:#718096">
        <th style="padding:4px 6px">Plantilla</th><th style="padding:4px 6px">Largo</th>
        <th style="padding:4px 6px">Botones</th><th style="padding:4px 6px">Categoría</th>
        <th style="padding:4px 6px">Estado</th></tr>
      ${d.plantillas.map(p => `<tr style="border-top:1px solid #edf2f7">
        <td style="padding:4px 6px">${_esc(p.nombre)}</td>
        <td style="padding:4px 6px;font-weight:600">${p.largo_cuerpo}</td>
        <td style="padding:4px 6px">${p.botones}</td>
        <td style="padding:4px 6px">${_esc(p.categoria || '')}</td>
        <td style="padding:4px 6px">${_esc(p.estado || '')}</td></tr>`).join('')}
    </table>
    <p style="color:#718096;margin-top:6px">Largo del cuerpo con los {{n}} sin reemplazar: al enviarse, los datos reales lo alargan un poco más.</p>`;
  } catch (e) {
    out.innerHTML = `<span style="color:#e53e3e">No se pudo conectar: ${_esc(e.message)}</span>`;
  }
}

async function loadRecaptacion() {
  const url = _waUrl();
  const histEl = document.getElementById('recap-historial');
  const nmEl = document.getElementById('recap-no-molestar');
  histEl.innerHTML = 'Cargando…';
  nmEl.innerHTML = 'Cargando…';
  loadRecaptacionConfig();
  loadRecapProgramados();
  let d;
  try {
    const r = await fetch(url + '/api/recaptacion/historial', { headers: _waHeaders() });
    if (r.status === 403) {
      histEl.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">Token incorrecto.</p>';
      nmEl.innerHTML = '';
      return;
    }
    d = await r.json();
  } catch (e) {
    histEl.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">No se pudo conectar con el backend.</p>';
    nmEl.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">No se pudo conectar con el backend.</p>';
    return;
  }
  if (!d.ok) {
    histEl.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">Error al cargar el historial.</p>';
    nmEl.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">Error al cargar la lista.</p>';
    return;
  }
  renderRecapHistorial(d.envios || []);
  renderRecapNoMolestar(d.no_molestar || []);
}

// ── Recordatorios de control programados (fecha futura, se envían a las 10:00) ──
const RECAP_PROG_ESTADO_LABEL = { pendiente: 'Pendiente', enviado: 'Enviado', anulado: 'Anulado', omitido: 'Omitido' };
const RECAP_PROG_ESTADO_COLOR = { pendiente: '#C9A84C', enviado: '#38a169', anulado: '#a0aec0', omitido: '#e53e3e' };

async function loadRecapProgramados() {
  const url = _waUrl();
  const cont = document.getElementById('recap-programados');
  cont.innerHTML = 'Cargando…';
  try {
    const r = await fetch(url + '/api/recaptacion/programados', { headers: _waHeaders() });
    if (r.status === 403) { cont.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">Token incorrecto.</p>'; return; }
    const d = await r.json();
    if (!d.ok) { cont.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">Error al cargar los programados.</p>'; return; }
    renderRecapProgramados(d.programados || []);
  } catch (e) {
    cont.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">No se pudo conectar con el backend.</p>';
  }
}

function renderRecapProgramados(items) {
  const cont = document.getElementById('recap-programados');
  if (!items.length) {
    cont.innerHTML = '<p style="color:#718096;font-size:.85rem">No hay recordatorios programados.</p>';
    return;
  }
  // Pendientes primero y bien visibles (son lo accionable); el resto queda como historial apagado.
  const pendientes = items.filter(p => p.estado === 'pendiente');
  const otros = items.filter(p => p.estado !== 'pendiente');

  const filaPendiente = p => `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:10px 12px;margin-bottom:8px;background:#fffbeb;border:1px solid #f0d999;border-radius:6px">
      <div>
        <strong style="font-size:.88rem">${_esc(p.nombre || '—')}</strong>
        <span style="font-size:.78rem;color:#718096"> · ${_esc(_rutFmt(p.rut))}</span><br>
        <span style="font-size:.8rem;color:#4A5568;text-transform:capitalize">${_esc(p.doctor || '—')}</span>
        <span style="font-size:.8rem;color:#718096"> · se envía el ${_esc(_fechaCorta(p.fecha_programada))}</span>
        <span style="font-size:.76rem;color:#a0aec0"> · último control ${_esc(_fechaCorta(p.fecha_cita))}</span>
      </div>
      <button class="btn btn-sm" style="background:#fff;border-color:#e53e3e;color:#e53e3e" onclick="anularRecapProgramado('${_esc(p.id)}')">Anular</button>
    </div>`;

  const filaOtro = p => {
    const color = RECAP_PROG_ESTADO_COLOR[p.estado] || '#a0aec0';
    const label = RECAP_PROG_ESTADO_LABEL[p.estado] || p.estado;
    // "omitido" es el caso que hay que entender de un vistazo: llegó la fecha y el
    // sistema NO envió porque el paciente ya había agendado por su cuenta (u otro motivo).
    const motivo = p.estado === 'omitido' && p.motivo_omision
      ? `<div style="font-size:.78rem;color:#e53e3e;margin-top:2px">No se envió: ${_esc(p.motivo_omision)}</div>` : '';
    return `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:8px 12px;border-top:1px solid #e2e8f0;opacity:.8">
        <div>
          <span style="font-size:.85rem">${_esc(p.nombre || '—')}</span>
          <span style="font-size:.78rem;color:#718096"> · ${_esc(_rutFmt(p.rut))} · ${_esc(p.doctor || '—')} · programado para ${_esc(_fechaCorta(p.fecha_programada))}</span>
          ${motivo}
        </div>
        <span class="tag" style="border-color:${color};color:${color}">${_esc(label)}</span>
      </div>`;
  };

  cont.innerHTML = `
    ${pendientes.length ? pendientes.map(filaPendiente).join('') : '<p style="color:#718096;font-size:.85rem;margin-bottom:8px">Sin pendientes.</p>'}
    ${otros.length ? `<div style="margin-top:6px">${otros.map(filaOtro).join('')}</div>` : ''}`;
}

async function anularRecapProgramado(id) {
  if (!confirm('¿Anular este recordatorio programado? No se enviará.')) return;
  const url = _waUrl();
  try {
    const r = await fetch(url + '/api/recaptacion/programados/anular', {
      method: 'POST', headers: _waHeaders(), body: JSON.stringify({ id }),
    });
    const d = await r.json();
    if (d.ok) {
      toast('✅ Recordatorio anulado');
      loadRecapProgramados();
    } else {
      toast('❌ ' + (d.error || 'No se pudo anular'), false);
    }
  } catch (e) {
    toast('❌ Error de conexión', false);
  }
}

function renderRecapHistorial(envios) {
  const cont = document.getElementById('recap-historial');
  if (!envios.length) {
    cont.innerHTML = '<p style="color:#718096;font-size:.85rem">Aún no se ha enviado ningún recordatorio de control.</p>';
    return;
  }
  const filas = envios.slice(0, 100);
  cont.innerHTML = `<div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:.85rem;min-width:600px">
      <thead><tr style="background:#f8fafc;text-align:left">
        <th style="padding:10px 14px">Paciente</th>
        <th style="padding:10px 14px">Doctor</th>
        <th style="padding:10px 14px">Fecha de envío</th>
        <th style="padding:10px 14px">Respondió</th>
      </tr></thead>
      <tbody>
        ${filas.map(e => `
          <tr style="border-top:1px solid #e2e8f0">
            <td style="padding:10px 14px">${_esc(e.nombre || '—')}<br><span style="font-size:.78rem;color:#718096">${_esc(_rutFmt(e.rut))}</span></td>
            <td style="padding:10px 14px;text-transform:capitalize">${_esc(e.doctor || '—')}</td>
            <td style="padding:10px 14px;color:#718096">${_esc(_fechaCorta(e.fecha_envio))}</td>
            <td style="padding:10px 14px">${e.respondio
              ? '<span class="tag" style="border-color:#38a169;color:#38a169;font-weight:600">✅ Respondió</span>'
              : '<span class="tag" style="border-color:#e53e3e;color:#e53e3e">Sin respuesta</span>'}</td>
          </tr>`).join('')}
      </tbody>
    </table>
  </div>`;
}

function renderRecapNoMolestar(ruts) {
  const cont = document.getElementById('recap-no-molestar');
  if (!ruts.length) {
    cont.innerHTML = '<p style="color:#718096;font-size:.85rem">Nadie en la lista.</p>';
    return;
  }
  cont.innerHTML = ruts.map(rut => {
    const rutLimpio = String(rut).replace(/[^a-zA-Z0-9]/g, '_');
    return `<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-top:1px solid #e2e8f0">
      <span style="font-size:.88rem">${_esc(_rutFmt(rut))}</span>
      <button class="btn btn-sm" id="btn-quitar-nm-${_esc(rutLimpio)}" style="background:#e2e8f0;color:#2d3748"
        onclick="quitarNoMolestar('${_esc(rut)}')">Quitar de la lista</button>
    </div>`;
  }).join('');
}

async function quitarNoMolestar(rut) {
  const url = _waUrl();
  const rutLimpio = String(rut).replace(/[^a-zA-Z0-9]/g, '_');
  const btn = document.getElementById('btn-quitar-nm-' + rutLimpio);
  if (btn) { btn.disabled = true; btn.textContent = 'Quitando...'; }
  try {
    const r = await fetch(url + '/api/recaptacion/no-molestar', {
      method: 'POST', headers: _waHeaders(), body: JSON.stringify({ rut, quitar: true }),
    });
    const d = await r.json();
    if (d.ok) {
      toast('✅ Sacado de la lista de no molestar');
      loadRecaptacion();
    } else {
      toast('❌ ' + (d.error || 'No se pudo quitar'), false);
      if (btn) { btn.disabled = false; btn.textContent = 'Quitar de la lista'; }
    }
  } catch (e) {
    toast('❌ Error de conexión', false);
    if (btn) { btn.disabled = false; btn.textContent = 'Quitar de la lista'; }
  }
}

function renderWaEstado(d) {
  const el = document.getElementById('wa-estado');
  let color, texto;
  if (d.conectado) {
    color = '#38a169';
    texto = `<strong style="color:${color}">● Conectado</strong> — ${_esc(d.numero || 'número activo')}${d.calidad ? ' · calidad ' + _esc(d.calidad) : ''}`;
  } else if (d.configurado) {
    color = '#e53e3e';
    texto = `<strong style="color:${color}">● Error</strong> — ${_esc(d.error || 'no se pudo conectar con Meta')}`;
  } else {
    color = '#C9A84C';
    texto = `<strong style="color:${color}">● No configurado</strong> — ${_esc(d.error || 'faltan las variables WA_ENABLED / WA_TOKEN / WA_PHONE_NUMBER_ID en Render')}`;
  }
  const fmt = ts => ts ? new Date(ts).toLocaleString('es-CL') : 'nunca';
  el.innerHTML = `
    <h3 style="margin-bottom:10px">Estado del sistema</h3>
    <p style="font-size:.9rem;margin-bottom:10px">${texto}</p>
    <p style="font-size:.78rem;color:#718096">Último envío — semana: ${fmt(d.ultimo_envio_semana)} · día: ${fmt(d.ultimo_envio_dia)} · inasistencia: ${fmt(d.ultimo_envio_inasistencia)}</p>`;
}

async function saveWhatsapp() {
  const url = _waUrl();
  const body = {
    recordatorio_semana:    { activo: document.getElementById('wa-semana-activo').checked, hora: document.getElementById('wa-semana-hora').value },
    recordatorio_dia:       { activo: document.getElementById('wa-dia-activo').checked, hora: document.getElementById('wa-dia-hora').value },
    inasistencia_reagendar: { activo: document.getElementById('wa-inasistencia-activo').checked, hora: document.getElementById('wa-inasistencia-hora').value },
  };
  try {
    const r = await fetch(url + '/api/whatsapp/config', { method: 'POST', headers: _waHeaders(), body: JSON.stringify(body) });
    const d = await r.json();
    toast(d.ok ? '✅ Configuración guardada' : '❌ ' + (d.error || 'Error'), d.ok);
    if (d.ok) loadWhatsapp();
  } catch (e) {
    toast('❌ No se pudo conectar con el backend', false);
  }
}

// ══════════════════════════════════════════════════════
// AGENDA ONLINE — % de ocupación por doctor/franja
// ══════════════════════════════════════════════════════
const FRANJAS = [
  ['dia_0_5',   'Hoy a 5 días'],
  ['dia_6_10',  'Días 6 a 10'],
  ['dia_11_20', 'Días 11 a 20'],
  ['dia_21_30', 'Días 21 a 30'],
  ['dia_31_60', 'Días 31 a 60'],
];
let AGENDA_CFG = null;

async function loadAgenda() {
  AGENDA_CFG = await api('/api/scheduling-config');
  const estado = document.getElementById('agenda-estado');
  // El mensaje de "modo demo" solo aparece si NO hay credenciales (no conectado).
  if (AGENDA_CFG.dentidesk_enabled) {
    estado.style.display = 'none';
    estado.innerHTML = '';
  } else {
    estado.style.display = '';
    estado.innerHTML = '<strong style="color:#C9A84C">● Modo demo</strong> — aún sin credenciales de DentiDesk. El flujo funciona con datos simulados.';
  }

  // Anticipación mínima global
  const anti = (AGENDA_CFG.reglas && AGENDA_CFG.reglas.anticipacion_minima_horas) || 16;
  document.getElementById('agenda-global').innerHTML = `
    <h3>Anticipación mínima</h3>
    <div style="display:flex;align-items:center;gap:10px">
      <span style="flex:1;font-size:.9rem">Horas mínimas de anticipación para reservar (todos los motivos, incluidas urgencias)</span>
      <input type="number" min="0" max="168" id="ag-anticipacion" value="${anti}" style="width:80px;padding:6px;border:1px solid #cbd5e0;border-radius:6px"> hrs
    </div>`;

  // Motivos y especialidades
  renderMotivoPanel();

  // Frases "¿Sabías qué?" (rotan mientras el paciente espera las horas)
  const frases = (AGENDA_CFG.sabias_que || []).join('\n');
  document.getElementById('agenda-sabias').innerHTML = `
    <h3 style="margin-bottom:6px">"¿Sabías qué?" mientras carga</h3>
    <p style="font-size:.82rem;color:#718096;margin-bottom:10px">Una frase por línea. Rotan mientras el paciente espera las horas disponibles. Déjalo vacío para ocultarlo.</p>
    <textarea id="ag-sabias" rows="6" style="width:100%;padding:8px 10px;border:1px solid #cbd5e0;border-radius:6px;font-size:.88rem;line-height:1.5;resize:vertical">${frases.replace(/</g,'&lt;')}</textarea>`;

  const cont = document.getElementById('agenda-doctores');
  cont.innerHTML = Object.entries(AGENDA_CFG.doctores).map(([id, d]) => `
    <div class="card" data-doc="${id}">
      <h3 style="text-transform:capitalize">
        <label style="font-weight:400;font-size:.85rem;float:right">
          <input type="checkbox" class="ag-atiende" ${d.atiende ? 'checked' : ''}> atiende online
        </label>
        ${id}
      </h3>
      <h4 style="font-size:.8rem;text-transform:uppercase;letter-spacing:.5px;color:#4A5568;margin:6px 0">Ocupación aparente mínima</h4>
      ${FRANJAS.map(([key, label]) => `
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
          <span style="flex:1;font-size:.88rem">${label}</span>
          <input type="number" min="0" max="100" class="ag-val" data-franja="${key}" value="${d.ocupacion[key]}" style="width:72px;padding:6px;border:1px solid #cbd5e0;border-radius:6px"> % ocupado
        </div>`).join('')}
    </div>`).join('');
}

function _slugify(s) {
  return (s||'').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g,'')
    .replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,'').slice(0,40) || ('m_'+Date.now());
}

function renderMotivoPanel() {
  const esps   = AGENDA_CFG.especialidades || [];
  const motivos = AGENDA_CFG.motivos || [];

  const espOpts = esps.map(e=>`<option value="${e.key}">${e.label}</option>`).join('');
  const espRows = esps.map(e=>`
    <tr data-espkey="${e.key}">
      <td style="padding:6px 8px"><code style="font-size:.82rem">${e.key}</code></td>
      <td style="padding:6px 8px"><input class="esp-label" value="${e.label}" style="width:100%;padding:4px 6px;border:1px solid #cbd5e0;border-radius:4px"></td>
      <td style="padding:6px 8px;text-align:center"><button onclick="eliminarEsp('${e.key}')" style="background:none;border:none;color:#e53e3e;cursor:pointer;font-size:1rem">✕</button></td>
    </tr>`).join('');

  const motRows = motivos.map(m=>`
    <tr data-motkey="${m.key}">
      <td style="padding:6px 8px"><input class="mot-label" value="${(m.label||'').replace(/"/g,'&quot;')}" style="width:100%;min-width:160px;padding:4px 6px;border:1px solid #cbd5e0;border-radius:4px"></td>
      <td style="padding:6px 8px"><select class="mot-esp" style="padding:4px 6px;border:1px solid #cbd5e0;border-radius:4px">${esps.map(e=>`<option value="${e.key}"${m.especialidad===e.key?' selected':''}>${e.label}</option>`).join('')}</select></td>
      <td style="padding:6px 8px"><input type="number" class="mot-dur" min="5" max="180" step="5" value="${m.duracion_min||15}" style="width:64px;padding:4px 6px;border:1px solid #cbd5e0;border-radius:4px"> min</td>
      <td style="padding:6px 8px;text-align:center"><input type="checkbox" class="mot-urg" ${m.urgencia?'checked':''}></td>
      <td style="padding:6px 8px;text-align:center"><input type="checkbox" class="mot-notif" ${m.notificar_agenda?'checked':''}></td>
      <td style="padding:6px 8px"><input class="mot-id" value="${m.id_reason||''}" placeholder="ID DentiDesk" style="width:90px;padding:4px 6px;border:1px solid #cbd5e0;border-radius:4px;font-size:.82rem"></td>
      <td style="padding:6px 8px;text-align:center"><button onclick="eliminarMotivo('${m.key}')" style="background:none;border:none;color:#e53e3e;cursor:pointer;font-size:1rem">✕</button></td>
    </tr>`).join('');

  document.getElementById('agenda-motivos').innerHTML = `
    <h3 style="margin-bottom:14px">Motivos de consulta</h3>

    <h4 style="font-size:.85rem;text-transform:uppercase;letter-spacing:.5px;color:#4A5568;margin-bottom:6px">Especialidades</h4>
    <div style="overflow-x:auto;margin-bottom:8px">
      <table style="width:100%;border-collapse:collapse" id="ag-esp-tbody-wrap">
        <thead><tr style="background:#f8fafc">
          <th style="padding:6px 8px;text-align:left;font-size:.8rem;color:#718096">Key</th>
          <th style="padding:6px 8px;text-align:left;font-size:.8rem;color:#718096">Etiqueta visible</th>
          <th></th>
        </tr></thead>
        <tbody id="ag-esp-tbody">${espRows}</tbody>
      </table>
    </div>
    <button onclick="agregarEsp()" class="btn btn-sm" style="font-size:.82rem;padding:4px 12px;margin-bottom:18px">+ Especialidad</button>

    <h4 style="font-size:.85rem;text-transform:uppercase;letter-spacing:.5px;color:#4A5568;margin-bottom:6px">Motivos</h4>
    <div style="overflow-x:auto;margin-bottom:8px">
      <table style="width:100%;border-collapse:collapse">
        <thead><tr style="background:#f8fafc">
          <th style="padding:6px 8px;text-align:left;font-size:.8rem;color:#718096">Etiqueta</th>
          <th style="padding:6px 8px;text-align:left;font-size:.8rem;color:#718096">Especialidad</th>
          <th style="padding:6px 8px;text-align:left;font-size:.8rem;color:#718096">Duración</th>
          <th style="padding:6px 8px;text-align:center;font-size:.8rem;color:#718096">Urgencia</th>
          <th style="padding:6px 8px;text-align:center;font-size:.8rem;color:#718096">Avisar a recepción</th>
          <th style="padding:6px 8px;text-align:left;font-size:.8rem;color:#718096">ID DentiDesk</th>
          <th></th>
        </tr></thead>
        <tbody id="ag-mot-tbody">${motRows}</tbody>
      </table>
    </div>
    <button onclick="agregarMotivo()" class="btn btn-sm" style="font-size:.82rem;padding:4px 12px">+ Motivo</button>`;
}

function agregarEsp() {
  const key = prompt('Key interno (sin espacios, ej: implantologia):');
  if (!key) return;
  const label = prompt('Etiqueta visible para el paciente:');
  if (!label) return;
  const tr = document.createElement('tr');
  tr.dataset.espkey = key.trim().toLowerCase().replace(/\s+/g,'_');
  tr.innerHTML = `
    <td style="padding:6px 8px"><code style="font-size:.82rem">${tr.dataset.espkey}</code></td>
    <td style="padding:6px 8px"><input class="esp-label" value="${label}" style="width:100%;padding:4px 6px;border:1px solid #cbd5e0;border-radius:4px"></td>
    <td style="padding:6px 8px;text-align:center"><button onclick="eliminarEsp('${tr.dataset.espkey}')" style="background:none;border:none;color:#e53e3e;cursor:pointer;font-size:1rem">✕</button></td>`;
  document.getElementById('ag-esp-tbody').appendChild(tr);
}
function eliminarEsp(key) {
  if (!confirm(`¿Eliminar especialidad "${key}"? Asegúrate de que no haya motivos que la usen.`)) return;
  document.querySelector(`#ag-esp-tbody tr[data-espkey="${key}"]`)?.remove();
}

function agregarMotivo() {
  const esps = AGENDA_CFG.especialidades || [];
  const espOpts = esps.map(e=>`<option value="${e.key}">${e.label}</option>`).join('');
  const key = 'motivo_' + Date.now();
  const tr = document.createElement('tr');
  tr.dataset.motkey = key;
  tr.innerHTML = `
    <td style="padding:6px 8px"><input class="mot-label" value="" placeholder="Etiqueta" style="width:100%;min-width:160px;padding:4px 6px;border:1px solid #cbd5e0;border-radius:4px"></td>
    <td style="padding:6px 8px"><select class="mot-esp" style="padding:4px 6px;border:1px solid #cbd5e0;border-radius:4px">${espOpts}</select></td>
    <td style="padding:6px 8px"><input type="number" class="mot-dur" min="5" max="180" step="5" value="15" style="width:64px;padding:4px 6px;border:1px solid #cbd5e0;border-radius:4px"> min</td>
    <td style="padding:6px 8px;text-align:center"><input type="checkbox" class="mot-urg"></td>
    <td style="padding:6px 8px;text-align:center"><input type="checkbox" class="mot-notif"></td>
    <td style="padding:6px 8px"><input class="mot-id" value="" placeholder="ID DentiDesk" style="width:90px;padding:4px 6px;border:1px solid #cbd5e0;border-radius:4px;font-size:.82rem"></td>
    <td style="padding:6px 8px;text-align:center"><button onclick="this.closest('tr').remove()" style="background:none;border:none;color:#e53e3e;cursor:pointer;font-size:1rem">✕</button></td>`;
  document.getElementById('ag-mot-tbody').appendChild(tr);
}
function eliminarMotivo(key) {
  if (!confirm('¿Eliminar este motivo?')) return;
  document.querySelector(`#ag-mot-tbody tr[data-motkey="${key}"]`)?.remove();
}

async function saveAgenda() {
  // Doctores
  const doctores = {};
  document.querySelectorAll('#agenda-doctores .card').forEach(card => {
    const id = card.dataset.doc;
    const ocupacion = {};
    card.querySelectorAll('.ag-val').forEach(inp => { ocupacion[inp.dataset.franja] = +inp.value; });
    doctores[id] = { atiende: card.querySelector('.ag-atiende').checked, ocupacion };
  });

  // Especialidades
  const especialidades = [];
  document.querySelectorAll('#ag-esp-tbody tr[data-espkey]').forEach(tr => {
    const key   = tr.dataset.espkey;
    const label = tr.querySelector('.esp-label')?.value.trim();
    if (key && label) especialidades.push({ key, label });
  });

  // Motivos
  const motivos = [];
  document.querySelectorAll('#ag-mot-tbody tr[data-motkey]').forEach(tr => {
    const label = tr.querySelector('.mot-label')?.value.trim();
    if (!label) return;
    const rawKey = tr.dataset.motkey;
    const key = rawKey.startsWith('motivo_') ? _slugify(label) + '_' + rawKey.split('_').pop() : rawKey;
    motivos.push({
      key,
      label,
      especialidad: tr.querySelector('.mot-esp')?.value || 'ortodoncia',
      duracion_min: +(tr.querySelector('.mot-dur')?.value || 15),
      urgencia:     tr.querySelector('.mot-urg')?.checked || false,
      notificar_agenda: tr.querySelector('.mot-notif')?.checked || false,
      id_reason:    tr.querySelector('.mot-id')?.value.trim() || '',
    });
  });

  const anticipacion = +document.getElementById('ag-anticipacion').value;
  const sabias_que = (document.getElementById('ag-sabias')?.value || '')
    .split('\n').map(s => s.trim()).filter(Boolean);
  const r = await api('/api/scheduling-config', 'POST', {
    doctores, anticipacion_minima_horas: anticipacion, motivos, especialidades, sabias_que,
  });
  if (r.ok) {
    AGENDA_CFG = await api('/api/scheduling-config');
    renderMotivoPanel();
  }
  toast(r.ok ? 'Configuración guardada ✓' : 'Error al guardar', r.ok);
}

// ── Toast ────────────────────────────────────────────
function toast(msg, ok=true) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + (ok ? 'ok' : 'err');
  setTimeout(() => t.classList.remove('show'), 3000);
}

// ── Modal ────────────────────────────────────────────
function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

// ── API helper ───────────────────────────────────────
async function api(url, method='GET', body=null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(url, opts);
  return r.json();
}

// ══════════════════════════════════════════════════════
// 1. FOTOS
// ══════════════════════════════════════════════════════
const DOCTORES = [
  { nombre: 'Dr. Octavio Del Real S.', archivo: 'dr-octavio-del-real.jpeg' },
  { nombre: 'Dr. Rodrigo Oyonarte W.', archivo: 'dr-rodrigo-oyonarte.jpeg' },
  { nombre: 'Dr. Alberto Del Real V.', archivo: 'dr-alberto-del-real.jpeg' },
  { nombre: 'Dr. Patricio Vial U.',    archivo: 'dr-patricio-vial.jpeg' },
];
const CLINICA_FOTOS = [
  { nombre: 'Principal', archivo: 'clinica-1.jpg' },
  { nombre: 'Recepción', archivo: 'clinica-recepcion.jpg' },
  { nombre: 'Box clínico', archivo: 'clinica-box.jpg' },
  { nombre: 'Laboratorio', archivo: 'clinica-laboratorio.jpg' },
  { nombre: 'Rayos X', archivo: 'clinica-rayos.jpg' },
  { nombre: 'Esterilización', archivo: 'clinica-esterilizacion.jpg' },
];

function loadFotos() {
  renderPhotoGrid('fotos-doctores', DOCTORES);
  galeriaCargar();
}

function renderPhotoGrid(containerId, items) {
  const container = document.getElementById(containerId);
  container.innerHTML = items.map(item => `
    <div class="photo-item">
      <img src="/images/${item.archivo}?t=${Date.now()}" alt="${item.nombre}"
           onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
      <div class="no-photo" style="display:none"><i class="fa fa-image"></i></div>
      <p>${item.nombre}</p>
      <label class="upload-btn">
        📁 Cambiar foto
        <input type="file" accept="image/*" style="display:none"
               onchange="uploadFoto(this, '${item.archivo}')">
      </label>
    </div>
  `).join('');
}

async function uploadFoto(input, target) {
  const file = input.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  fd.append('target', target);
  const r = await fetch('/api/upload', { method: 'POST', body: fd });
  const data = await r.json();
  if (data.ok) { toast('✅ Foto actualizada'); loadFotos(); }
  else toast('❌ Error: ' + data.error, false);
}

// ── Galería clínica ─────────────────────────────────────────────────────────

let galeriaSlides = [];
let galeriaDrag = null;

async function galeriaCargar() {
  const slides = await api('/api/galeria');
  galeriaSlides = slides;
  galeriaRender();
}

function galeriaRender() {
  const lista = document.getElementById('galeria-lista');
  lista.innerHTML = galeriaSlides.map((s, i) => `
    <div class="galeria-item" draggable="true" data-idx="${i}"
         ondragstart="galeriaDragStart(event,${i})"
         ondragover="galeriaDragOver(event,${i})"
         ondrop="galeriaDrop(event,${i})"
         ondragend="galeriaDragEnd()">
      <div style="display:flex;align-items:center;gap:12px;flex:1;min-width:0">
        <i class="fas fa-grip-vertical" style="color:#a0aec0;cursor:grab;flex-shrink:0"></i>
        <img src="/${s.src}?t=${Date.now()}" alt="${s.caption}"
             style="width:72px;height:52px;object-fit:cover;border-radius:6px;flex-shrink:0"
             onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2272%22 height=%2252%22><rect width=%2272%22 height=%2252%22 fill=%22%23e2e8f0%22/></svg>'">
        <span style="font-size:.9rem;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${s.caption}</span>
      </div>
      <div style="display:flex;gap:6px;flex-shrink:0">
        <button class="btn btn-sm" onclick="galeriaMover(${i},-1)" title="Subir" ${i===0?'disabled':''}>↑</button>
        <button class="btn btn-sm" onclick="galeriaMover(${i},1)" title="Bajar" ${i===galeriaSlides.length-1?'disabled':''}>↓</button>
        <button class="btn btn-sm btn-gold" onclick="galeriaRenombrar(${i})" title="Renombrar">✎</button>
        <button class="btn btn-danger btn-sm" onclick="galeriaEliminar(${i})">✕</button>
      </div>
    </div>
  `).join('');
}

// Drag and drop
function galeriaDragStart(e, idx) {
  galeriaDrag = idx;
  e.dataTransfer.effectAllowed = 'move';
}
function galeriaDragOver(e, idx) {
  e.preventDefault();
  document.querySelectorAll('.galeria-item').forEach((el,i) =>
    el.style.opacity = i === idx && idx !== galeriaDrag ? '0.5' : '1');
}
function galeriaDrop(e, targetIdx) {
  e.preventDefault();
  if (galeriaDrag === null || galeriaDrag === targetIdx) return;
  const orden = galeriaSlides.map((_,i) => i);
  orden.splice(targetIdx, 0, orden.splice(galeriaDrag, 1)[0]);
  galeriaGuardarOrden(orden);
}
function galeriaDragEnd() {
  galeriaDrag = null;
  document.querySelectorAll('.galeria-item').forEach(el => el.style.opacity = '1');
}

// Botones ↑↓
function galeriaMover(idx, dir) {
  const newIdx = idx + dir;
  if (newIdx < 0 || newIdx >= galeriaSlides.length) return;
  const orden = galeriaSlides.map((_,i) => i);
  orden.splice(newIdx, 0, orden.splice(idx, 1)[0]);
  galeriaGuardarOrden(orden);
}

async function galeriaGuardarOrden(orden) {
  const r = await api('/api/galeria/reordenar', 'POST', { orden });
  if (r.ok) { galeriaSlides = r.slides; galeriaRender(); toast('✅ Orden guardado'); }
  else toast('❌ ' + r.error, false);
}

async function galeriaRenombrar(idx) {
  const actual = galeriaSlides[idx].caption;
  const nuevo = prompt('Nuevo nombre para esta foto:', actual);
  if (!nuevo || nuevo.trim() === actual) return;
  const r = await api('/api/galeria/renombrar', 'POST', { idx, caption: nuevo.trim() });
  if (r.ok) { galeriaSlides = r.slides; galeriaRender(); toast('✅ Nombre actualizado'); }
  else toast('❌ ' + r.error, false);
}

async function galeriaEliminar(idx) {
  if (!confirm(`¿Eliminar "${galeriaSlides[idx].caption}" del carrusel?`)) return;
  const r = await api('/api/galeria/eliminar', 'POST', { idx });
  if (r.ok) { galeriaSlides = r.slides; galeriaRender(); toast('✅ Foto eliminada'); }
  else toast('❌ ' + r.error, false);
}

async function galeriaAgregar(input) {
  const file = input.files[0];
  if (!file) return;
  const caption = prompt('Nombre de esta foto (ej: Sala de esterilización):');
  if (!caption) return;
  const fd = new FormData();
  fd.append('file', file);
  fd.append('caption', caption);
  toast('⏳ Subiendo foto…');
  const r = await fetch('/api/galeria/agregar', { method: 'POST', body: fd });
  const data = await r.json();
  if (data.ok) { galeriaSlides = data.slides; galeriaRender(); toast('✅ Foto agregada al carrusel'); }
  else toast('❌ ' + data.error, false);
  input.value = '';
}

// ══════════════════════════════════════════════════════
// 2. INFO
// ══════════════════════════════════════════════════════
async function loadInfo() {
  const data = await api('/api/info');
  document.getElementById('info-telefono').value = data.telefono || '';
  document.getElementById('info-horario').value = data.horario || '';
}

async function saveInfo() {
  const data = {
    telefono: document.getElementById('info-telefono').value,
    horario: document.getElementById('info-horario').value,
  };
  const r = await api('/api/info', 'POST', data);
  toast(r.ok ? '✅ Info guardada' : '❌ ' + r.error, r.ok);
}

// ══════════════════════════════════════════════════════
// 3. EQUIPO
// ══════════════════════════════════════════════════════
async function loadEquipo() {
  equipoData = await api('/api/equipo');
  const tabs = [...new Set(equipoData.map(m => m.tab))];
  currentEquipoTab = tabs[0];

  document.getElementById('equipo-tabs').innerHTML = tabs.map(t => `
    <button class="${t===currentEquipoTab?'active':''}" onclick="filterEquipo('${t}')">${t.charAt(0).toUpperCase()+t.slice(1)}</button>
  `).join('');
  renderEquipo();
}

function filterEquipo(tab) {
  currentEquipoTab = tab;
  document.querySelectorAll('#equipo-tabs button').forEach(b => b.classList.toggle('active', b.textContent.toLowerCase()===tab));
  renderEquipo();
}

// Miembros que se estan mostrando ahora. Los botones referencian su POSICION en
// esta lista (data-i), no su nombre: el nombre lo escribe una persona y no sirve
// como identificador dentro de HTML.
let equipoVisible = [];

function renderEquipo() {
  equipoVisible = equipoData.filter(m => m.tab === currentEquipoTab);
  // ⚠️ Todo valor que venga del backend pasa por _esc(). Antes el nombre se
  // interpolaba crudo dentro de onclick="...": un apellido con apostrofe (O'Higgins)
  // rompia la fila, y un nombre armado a proposito ejecutaba JS en la sesion del
  // admin, que tiene el ADMIN_TOKEN guardado en localStorage.
  document.getElementById('equipo-lista').innerHTML = equipoVisible.map((m, i) => `
    <div class="card" style="display:flex;gap:20px;align-items:flex-start">
      <div style="flex-shrink:0">
        ${m.foto
          ? `<img src="/${encodeURI(String(m.foto))}?t=${Date.now()}" style="width:80px;height:80px;object-fit:cover;border-radius:50%;border:2px solid #e2e8f0">`
          : `<div style="width:80px;height:80px;background:var(--bg);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:1.5rem;color:#a0aec0;border:2px solid #e2e8f0">${_esc(m.placeholder||'?')}</div>`
        }
        <label class="upload-btn" style="display:block;text-align:center;margin-top:8px;font-size:.75rem">
          📁 Foto
          <input type="file" accept="image/*" style="display:none" data-eq-foto="${i}">
        </label>
      </div>
      <div style="flex:1">
        <label>Nombre</label>
        <input type="text" id="eq-nombre-${i}" value="${_esc(m.nombre)}">
        <label>Rol</label>
        <input type="text" id="eq-rol-${i}" value="${_esc(m.rol)}">
        <div style="display:flex;gap:8px">
          <button class="btn btn-primary btn-sm" data-eq-save="${i}">Guardar</button>
          <button class="btn btn-danger btn-sm" data-eq-del="${i}">🗑️ Eliminar</button>
        </div>
      </div>
    </div>
  `).join('');
}

// Un solo listener delegado para toda la lista (no se acumulan al re-renderizar).
document.addEventListener('click', ev => {
  const save = ev.target.closest('[data-eq-save]');
  if (save) return saveEquipoMember(+save.dataset.eqSave);
  const del = ev.target.closest('[data-eq-del]');
  if (del) return eliminarMiembro(+del.dataset.eqDel);
});
document.addEventListener('change', ev => {
  const foto = ev.target.closest('[data-eq-foto]');
  if (foto) {
    const m = equipoVisible[+foto.dataset.eqFoto];
    if (m) uploadFotoStaff(foto, m.nombre, m.tab);
  }
});

async function saveEquipoMember(i) {
  const m = equipoVisible[i];
  if (!m) return;
  const data = {
    tab: currentEquipoTab,
    nombre_actual: m.nombre,
    nombre_nuevo: document.getElementById('eq-nombre-'+i).value,
    rol_nuevo: document.getElementById('eq-rol-'+i).value,
  };
  const r = await api('/api/equipo', 'POST', data);
  if (r.ok) { toast('✅ Guardado'); loadEquipo(); }
  else toast('❌ ' + r.error, false);
}

async function uploadFotoStaff(input, nombre, tab) {
  const file = input.files[0];
  if (!file) return;
  const ext = file.name.split('.').pop();
  const archivo = 'staff-' + nombre.toLowerCase().replace(/\s+/g,'-').replace(/[^a-z0-9-]/g,'') + '.' + ext;
  const fd = new FormData();
  fd.append('file', file);
  fd.append('target', archivo);
  const r1 = await fetch('/api/upload', { method: 'POST', body: fd });
  const d1 = await r1.json();
  if (!d1.ok) { toast('❌ Error subiendo foto', false); return; }
  const r2 = await api('/api/equipo', 'POST', { tab, nombre_actual: nombre, foto_nueva: d1.path });
  if (r2.ok) { toast('✅ Foto actualizada'); loadEquipo(); }
  else toast('❌ ' + r2.error, false);
}

// ══════════════════════════════════════════════════════
// 4. CASOS
// ══════════════════════════════════════════════════════
async function loadCasos() {
  casosData = await api('/api/casos');
  document.getElementById('casos-grid').innerHTML = casosData.map(c => `
    <div class="caso-card">
      <img src="/${c.foto}?t=${Date.now()}" onerror="this.src=''">
      <div class="caso-card-body">
        <h4>${c.titulo}</h4>
        <p>${c.descripcion.substring(0,80)}${c.descripcion.length>80?'...':''}</p>
        <div class="actions">
          <button class="btn btn-sm" style="background:#e2e8f0" onclick="editarCaso('${c.titulo.replace(/'/g,"\\'")}')">✏️</button>
          <button class="btn btn-danger btn-sm" onclick="eliminarCaso('${c.titulo.replace(/'/g,"\\'")}')">🗑️</button>
        </div>
      </div>
    </div>
  `).join('');
}

async function agregarCaso() {
  const file = document.getElementById('caso-file').files[0];
  let foto = document.getElementById('caso-foto').value;
  if (file) {
    const fd = new FormData();
    fd.append('file', file);
    const archivo = 'ejemplo-' + document.getElementById('caso-titulo').value.toLowerCase().replace(/\s+/g,'-').replace(/[^a-z0-9-]/g,'') + '.' + file.name.split('.').pop();
    fd.append('target', archivo);
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    const d = await r.json();
    if (d.ok) foto = d.path;
  }
  const r = await api('/api/casos', 'POST', {
    accion: 'agregar',
    titulo: document.getElementById('caso-titulo').value,
    descripcion: document.getElementById('caso-desc').value,
    foto,
  });
  if (r.ok) { toast('✅ Caso agregado'); closeModal('modal-caso-nuevo'); loadCasos(); }
  else toast('❌ ' + r.error, false);
}

async function eliminarCaso(titulo) {
  if (!confirm(`¿Eliminar el caso "${titulo}"?`)) return;
  const r = await api('/api/casos', 'POST', { accion: 'eliminar', titulo });
  if (r.ok) { toast('✅ Caso eliminado'); loadCasos(); }
}

function editarCaso(titulo) {
  const caso = casosData.find(c => c.titulo === titulo);
  if (!caso) return;
  const nuevo_titulo = prompt('Título:', caso.titulo);
  if (nuevo_titulo === null) return;
  const nueva_desc = prompt('Descripción:', caso.descripcion);
  if (nueva_desc === null) return;
  api('/api/casos', 'POST', {
    accion: 'editar',
    titulo_actual: titulo,
    titulo: nuevo_titulo,
    descripcion: nueva_desc,
  }).then(r => {
    if (r.ok) { toast('✅ Caso actualizado'); loadCasos(); }
    else toast('❌ ' + r.error, false);
  });
}

// ══════════════════════════════════════════════════════
// AGREGAR / ELIMINAR MIEMBROS DEL EQUIPO
// ══════════════════════════════════════════════════════

function toggleCVFields(tab) {
  const aviso = document.getElementById('cv-aviso');
  aviso.style.display = tab === 'especialistas' ? 'block' : 'none';
}

async function agregarMiembro() {
  const file = document.getElementById('nuevo-foto').files[0];
  let foto = null;
  if (file) {
    const ext = file.name.split('.').pop();
    const nombre = document.getElementById('nuevo-nombre').value;
    const archivo = 'staff-' + nombre.toLowerCase().replace(/\s+/g,'-').replace(/[^a-z0-9-]/g,'') + '.' + ext;
    const fd = new FormData();
    fd.append('file', file);
    fd.append('target', archivo);
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    const d = await r.json();
    if (d.ok) foto = d.path;
  }
  const r = await api('/api/equipo/agregar', 'POST', {
    tab: document.getElementById('nuevo-tab').value,
    nombre: document.getElementById('nuevo-nombre').value,
    rol: document.getElementById('nuevo-rol').value,
    foto,
  });
  const tab = document.getElementById('nuevo-tab').value;
  if (r.ok) {
    toast('✅ Miembro agregado');
    closeModal('modal-agregar-miembro');
    loadEquipo();
    if (tab === 'especialistas') {
      doctoresData = await api('/api/doctores'); // refrescar CV
    }
  } else toast('❌ ' + r.error, false);
}

async function eliminarMiembro(i) {
  const m = equipoVisible[i];
  if (!m) return;
  const nombre = m.nombre;
  if (!confirm(`¿Eliminar a "${nombre}" del equipo?${currentEquipoTab === 'especialistas' ? '\n\nTambién se eliminará su CV.' : ''}`)) return;
  const r = await api('/api/equipo/eliminar', 'POST', { tab: currentEquipoTab, nombre });
  if (r.ok) {
    toast('✅ Eliminado');
    loadEquipo();
    if (currentEquipoTab === 'especialistas') {
      doctoresData = await api('/api/doctores'); // refrescar CV
      // Si el CV editor está abierto en ese doctor, cambiar a otro
      if (!doctoresData[currentDoctorId]) {
        currentDoctorId = Object.keys(doctoresData)[0] || '';
      }
    }
  } else toast('❌ ' + r.error, false);
}

// ══════════════════════════════════════════════════════
// CV DOCTORES
// ══════════════════════════════════════════════════════
let doctoresData = {};
let currentDoctorId = '';

async function loadCV() {
  doctoresData = await api('/api/doctores');
  const ids = Object.keys(doctoresData);
  currentDoctorId = ids[0];
  document.getElementById('cv-tabs').innerHTML = ids.map(id => `
    <button class="${id===currentDoctorId?'active':''}" onclick="selectDoctor('${id}')">${doctoresData[id].name.replace('Dr. ','Dr. ')}</button>
  `).join('');
  renderCV();
}

function selectDoctor(id) {
  currentDoctorId = id;
  document.querySelectorAll('#cv-tabs button').forEach((b,i) => {
    b.classList.toggle('active', Object.keys(doctoresData)[i] === id);
  });
  renderCV();
}

function renderCV() {
  const d = doctoresData[currentDoctorId];
  if (!d) return;
  document.getElementById('cv-editor').innerHTML = `
    <div class="card">
      <div style="display:flex;gap:20px;align-items:flex-start;margin-bottom:20px">
        <img src="/${d.photo}?t=${Date.now()}" style="width:90px;height:90px;object-fit:cover;border-radius:50%;border:2px solid #e2e8f0">
        <div style="flex:1">
          <label>Nombre</label>
          <input type="text" id="cv-name" value="${d.name}">
          <label>Rol / Especialidad</label>
          <input type="text" id="cv-role" value="${d.role}">
        </div>
      </div>

      <label>Biografía</label>
      <textarea id="cv-bio" style="min-height:100px">${d.bio}</textarea>

      <label>Membresías <span style="font-weight:400;color:#718096">(una por línea)</span></label>
      <textarea id="cv-memberships" style="min-height:70px">${d.memberships.join('\n')}</textarea>

      <label>Formación académica <span style="font-weight:400;color:#718096">(una por línea)</span></label>
      <textarea id="cv-education" style="min-height:100px">${d.education.join('\n')}</textarea>

      <label>Actividades y especialidades <span style="font-weight:400;color:#718096">(una por línea)</span></label>
      <textarea id="cv-specialties" style="min-height:100px">${d.specialties.join('\n')}</textarea>

      <div style="display:flex;gap:10px;margin-top:8px">
        <button class="btn btn-primary" onclick="saveCV()">Guardar CV</button>
      </div>
    </div>
  `;
}

async function saveCV() {
  const updates = {
    name:        document.getElementById('cv-name').value,
    role:        document.getElementById('cv-role').value,
    bio:         document.getElementById('cv-bio').value,
    memberships: document.getElementById('cv-memberships').value.split('\n').filter(s=>s.trim()),
    education:   document.getElementById('cv-education').value.split('\n').filter(s=>s.trim()),
    specialties: document.getElementById('cv-specialties').value.split('\n').filter(s=>s.trim()),
  };
  // Guardar cada campo
  let ok = true;
  for (const [campo, valor] of Object.entries(updates)) {
    const r = await api('/api/doctores', 'POST', { id: currentDoctorId, campo, valor });
    if (!r.ok) { ok = false; toast('❌ Error guardando ' + campo, false); break; }
  }
  if (ok) {
    toast('✅ CV guardado');
    doctoresData[currentDoctorId] = { ...doctoresData[currentDoctorId], ...updates };
  }
}

// ══════════════════════════════════════════════════════
// 5. FAQ
// ══════════════════════════════════════════════════════
const FAQ_TAB_LABELS = {
  'tab-general': 'General', 'tab-tratamiento': 'Tratamiento',
  'tab-alineadores': 'Alineadores', 'tab-retencion': 'Retención',
  'tab-cirugia': 'Cirugía', 'tab-urgencias': 'Urgencias',
};

async function loadFaq() {
  faqData = await api('/api/faq');
  const tabs = [...new Set(faqData.map(f => f.tab))];
  currentFaqTab = tabs[0] || 'tab-general';

  document.getElementById('faq-tabs').innerHTML = Object.entries(FAQ_TAB_LABELS).map(([id,label]) => `
    <button class="${id===currentFaqTab?'active':''}" onclick="filterFaq('${id}')">${label}</button>
  `).join('');
  renderFaq();
}

function filterFaq(tab) {
  currentFaqTab = tab;
  document.querySelectorAll('#faq-tabs button').forEach(b => {
    b.classList.toggle('active', b.textContent === FAQ_TAB_LABELS[tab]);
  });
  renderFaq();
}

function renderFaq() {
  const items = faqData.filter(f => f.tab === currentFaqTab);
  document.getElementById('faq-lista').innerHTML = items.length
    ? items.map(f => `
      <li>
        <div>
          <div class="q">${f.pregunta}</div>
          <div class="a">${f.respuesta.substring(0,120)}${f.respuesta.length>120?'...':''}</div>
        </div>
        <div class="faq-actions">
          <button class="btn btn-sm" style="background:#e2e8f0" onclick='abrirEditFaq(${JSON.stringify(f.pregunta)}, ${JSON.stringify(f.respuesta)})'>✏️</button>
          <button class="btn btn-danger btn-sm" onclick='eliminarFaq(${JSON.stringify(f.pregunta)})'>🗑️</button>
        </div>
      </li>
    `).join('')
    : '<li style="justify-content:center;color:#a0aec0">No hay preguntas en esta categoría</li>';
}

function abrirEditFaq(pregunta, respuesta) {
  document.getElementById('edit-faq-original').value = pregunta;
  document.getElementById('edit-faq-preg').value = pregunta;
  document.getElementById('edit-faq-resp').value = respuesta;
  openModal('modal-editar-faq');
}

async function guardarEditFaq() {
  const r = await api('/api/faq', 'POST', {
    accion: 'editar',
    pregunta_actual: document.getElementById('edit-faq-original').value,
    pregunta_nueva: document.getElementById('edit-faq-preg').value,
    respuesta: document.getElementById('edit-faq-resp').value,
  });
  if (r.ok) { toast('✅ Pregunta actualizada'); closeModal('modal-editar-faq'); loadFaq(); }
  else toast('❌ ' + r.error, false);
}

async function agregarFaq() {
  const r = await api('/api/faq', 'POST', {
    accion: 'agregar',
    tab: document.getElementById('faq-tab-nuevo').value,
    pregunta: document.getElementById('faq-preg-nuevo').value,
    respuesta: document.getElementById('faq-resp-nuevo').value,
  });
  if (r.ok) { toast('✅ Pregunta agregada'); closeModal('modal-faq-nuevo'); loadFaq(); }
  else toast('❌ ' + r.error, false);
}

async function eliminarFaq(pregunta) {
  if (!confirm('¿Eliminar esta pregunta?')) return;
  const r = await api('/api/faq', 'POST', { accion: 'eliminar', pregunta });
  if (r.ok) { toast('✅ Pregunta eliminada'); loadFaq(); }
}

// ══════════════════════════════════════════════════════
// 6. PUBLICAR
// ══════════════════════════════════════════════════════
async function publicar() {
  const msg = document.getElementById('pub-msg').value || 'Actualización desde panel admin';
  toast('⏳ Publicando...', true);
  const r = await api('/api/publicar', 'POST', { mensaje: msg });
  toast(r.ok ? '🚀 ' + r.detalle : '❌ ' + r.error, r.ok);
}

// ══════════════════════════════════════════════════════
// SEGUROS — historial, aseguradoras, prestaciones, sugerencias, firmas
// ══════════════════════════════════════════════════════
let SEG_ASEGURADORAS = [], SEG_PRESTACIONES = [], SEG_MAPEO = {}, SEG_MAPEO_MOTIVOS = {},
    SEG_FIRMAS = [], SEG_HISTORIAL = [], SEG_HIST_FILTRO = '';

function initSeguros() {
  // Comparte token/URL con Estadísticas/Consentimientos/WhatsApp: mismo ADMIN_TOKEN, mismo backend.
  const t = localStorage.getItem('stats_token') || '';
  const u = localStorage.getItem('stats_url') || 'https://ortodonciarichard.onrender.com';
  document.getElementById('seg-token').value = t;
  document.getElementById('seg-url').value = u;
  if (t) loadSeguros();
}

function _segUrl() { return document.getElementById('seg-url').value.trim().replace(/\/$/, ''); }
function _segToken() { return document.getElementById('seg-token').value.trim(); }
function _segHeaders(json = true) {
  const h = { 'X-Admin-Token': _segToken() };
  if (json) h['Content-Type'] = 'application/json';
  return h;
}
async function _segFetch(path, method = 'GET', body = null) {
  const opts = { method, headers: _segHeaders() };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(_segUrl() + path, opts);
  return r.json();
}

async function loadSeguros() {
  const token = _segToken();
  if (!token) { toast('Ingresa el admin token', false); return; }
  localStorage.setItem('stats_token', token);
  localStorage.setItem('stats_url', _segUrl());
  ['seg-historial','seg-aseguradoras','seg-prestaciones','seg-motivos','seg-firmas'].forEach(id => {
    document.getElementById(id).innerHTML = 'Cargando…';
  });
  try {
    const [rAseg, rPrest, rMotivos, rFirma, rHist] = await Promise.all([
      fetch(_segUrl() + '/api/seguro/admin/aseguradoras', { headers: _segHeaders(false) }),
      fetch(_segUrl() + '/api/seguro/admin/prestaciones', { headers: _segHeaders(false) }),
      fetch(_segUrl() + '/api/seguro/admin/mapeo-motivos', { headers: _segHeaders(false) }),
      fetch(_segUrl() + '/api/seguro/admin/firma', { headers: _segHeaders(false) }),
      fetch(_segUrl() + '/api/seguro/admin/historial', { headers: _segHeaders(false) }),
    ]);
    if ([rAseg, rPrest, rMotivos, rFirma, rHist].some(r => r.status === 403)) {
      toast('Token incorrecto', false);
      return;
    }
    const [dAseg, dPrest, dMotivos, dFirma, dHist] = await Promise.all(
      [rAseg, rPrest, rMotivos, rFirma, rHist].map(r => r.json())
    );
    SEG_ASEGURADORAS  = dAseg.ok    ? (dAseg.aseguradoras || [])    : [];
    SEG_PRESTACIONES  = dPrest.ok   ? (dPrest.prestaciones || [])   : [];
    SEG_MAPEO         = dPrest.ok   ? (dPrest.mapeo || {})          : {};
    SEG_MAPEO_MOTIVOS = dMotivos.ok ? (dMotivos.mapeo || {})        : {};
    SEG_FIRMAS        = dFirma.ok   ? (dFirma.firmas || [])         : [];
    SEG_HISTORIAL     = dHist.ok    ? (dHist.items || [])           : [];
  } catch (e) {
    toast('No se pudo conectar con el backend', false);
    return;
  }
  renderSegAseguradoras();
  renderSegPrestaciones();
  renderSegMotivos();
  renderSegFirmas();
  renderSegHistorial();
}

// ── 1. Historial ─────────────────────────────────────
function filtrarSegHistorial(estado, btn) {
  SEG_HIST_FILTRO = estado;
  document.querySelectorAll('#seg-hist-filtros button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderSegHistorial();
}

async function loadSegHistorial() {
  try {
    const r = await fetch(_segUrl() + '/api/seguro/admin/historial', { headers: _segHeaders(false) });
    const d = await r.json();
    if (d.ok) { SEG_HISTORIAL = d.items || []; renderSegHistorial(); toast('✅ Historial actualizado'); }
    else toast('❌ ' + (d.error || 'Error'), false);
  } catch (e) { toast('❌ No se pudo conectar', false); }
}

function _segTotal(i) {
  return (i.prestaciones || []).reduce((s, p) => s + (+p.valor || 0), 0);
}

function renderSegHistorial() {
  const cont = document.getElementById('seg-historial');
  const items = SEG_HIST_FILTRO ? SEG_HISTORIAL.filter(i => i.estado === SEG_HIST_FILTRO) : SEG_HISTORIAL;
  if (!items.length) {
    cont.innerHTML = '<p style="color:#718096;font-size:.85rem">Sin formularios que coincidan.</p>';
    return;
  }
  cont.innerHTML = `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:.85rem">
    <thead><tr style="background:#f8fafc;text-align:left">
      <th style="padding:8px 10px">Creado</th><th style="padding:8px 10px">RUT</th>
      <th style="padding:8px 10px">Aseguradora</th><th style="padding:8px 10px">Doctor</th>
      <th style="padding:8px 10px">Total</th><th style="padding:8px 10px">Estado</th>
      <th style="padding:8px 10px">Canal</th>
    </tr></thead>
    <tbody>${items.map(i => `
      <tr style="border-top:1px solid #e2e8f0">
        <td style="padding:8px 10px;color:#718096">${_esc(_fechaCorta(i.creado))}</td>
        <td style="padding:8px 10px">${_esc(_rutFmt(i.rut))}</td>
        <td style="padding:8px 10px">${_esc(i.aseguradora || '—')}</td>
        <td style="padding:8px 10px">${_esc(i.doctor || '—')}</td>
        <td style="padding:8px 10px">$${_segTotal(i).toLocaleString('es-CL')}</td>
        <td style="padding:8px 10px"><span class="tag" style="border-color:${i.estado === 'enviado' ? '#38a169' : '#a0aec0'};color:${i.estado === 'enviado' ? '#38a169' : '#a0aec0'}">${_esc(i.estado === 'enviado' ? 'Enviado' : 'Generado')}</span></td>
        <td style="padding:8px 10px">${_esc(i.canal || '—')}</td>
      </tr>`).join('')}</tbody>
  </table></div>`;
}

// ── 2. Aseguradoras ──────────────────────────────────
async function loadSegAseguradoras() {
  try {
    const r = await fetch(_segUrl() + '/api/seguro/admin/aseguradoras', { headers: _segHeaders(false) });
    const d = await r.json();
    if (d.ok) { SEG_ASEGURADORAS = d.aseguradoras || []; renderSegAseguradoras(); renderSegPrestaciones(); }
  } catch (e) {}
}

function renderSegAseguradoras() {
  const cont = document.getElementById('seg-aseguradoras');
  if (!SEG_ASEGURADORAS.length) {
    cont.innerHTML = '<p style="color:#718096;font-size:.85rem">Sin aseguradoras creadas aún.</p>';
    return;
  }
  cont.innerHTML = `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:.85rem">
    <thead><tr style="background:#f8fafc;text-align:left">
      <th style="padding:8px 10px">Nombre</th><th style="padding:8px 10px">Key</th>
      <th style="padding:8px 10px">Activa</th><th style="padding:8px 10px">Plantilla</th>
      <th style="padding:8px 10px">Tipo</th><th style="padding:8px 10px"></th>
    </tr></thead>
    <tbody>
    ${SEG_ASEGURADORAS.map(a => `
      <tr style="border-top:1px solid #e2e8f0">
        <td style="padding:8px 10px">${_esc(a.nombre)}</td>
        <td style="padding:8px 10px"><code>${_esc(a.key)}</code></td>
        <td style="padding:8px 10px"><input type="checkbox" ${a.activa ? 'checked' : ''} onchange="toggleAseguradoraActiva('${a.key}', this.checked)"></td>
        <td style="padding:8px 10px">${a.plantilla_pdf ? '✅' : '—'}
          <label class="upload-btn" style="margin-left:6px;padding:3px 8px;font-size:.75rem">📁
            <input type="file" accept="application/pdf" style="display:none" onchange="subirPlantillaAseguradora(this,'${a.key}')">
          </label>
        </td>
        <td style="padding:8px 10px">${_esc(a.tipo_plantilla || '—')}</td>
        <td style="padding:8px 10px"><button class="btn btn-sm" style="background:#e2e8f0" onclick="verCamposAcroform('${a.key}')">Ver campos</button></td>
      </tr>
      <tr id="seg-campos-${a.key}" style="display:none"><td colspan="6" style="padding:8px 10px">
        <pre style="background:#1a2535;color:#e2e8f0;padding:10px;border-radius:8px;font-size:.78rem;max-height:240px;overflow:auto;white-space:pre-wrap"></pre>
      </td></tr>
    `).join('')}
    </tbody>
  </table></div>`;
}

async function crearAseguradora() {
  const key = document.getElementById('seg-aseg-key').value.trim().toLowerCase().replace(/[^a-z0-9_]/g, '_');
  const nombre = document.getElementById('seg-aseg-nombre').value.trim();
  if (!key || !nombre) { toast('Completa key y nombre', false); return; }
  const r = await _segFetch('/api/seguro/admin/aseguradoras', 'POST', { key, nombre, activa: true });
  if (r.ok) {
    toast('✅ Aseguradora guardada');
    document.getElementById('seg-aseg-key').value = '';
    document.getElementById('seg-aseg-nombre').value = '';
    loadSegAseguradoras();
  } else toast('❌ ' + (r.error || 'Error'), false);
}

async function toggleAseguradoraActiva(key, activa) {
  const a = SEG_ASEGURADORAS.find(x => x.key === key);
  const r = await _segFetch('/api/seguro/admin/aseguradoras', 'POST', { key, nombre: a ? a.nombre : key, activa });
  toast(r.ok ? '✅ Actualizado' : '❌ ' + (r.error || 'Error'), r.ok);
  if (r.ok && a) a.activa = activa;
}

async function subirPlantillaAseguradora(input, key) {
  const file = input.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  fd.append('key', key);
  try {
    const r = await fetch(_segUrl() + '/api/seguro/admin/aseguradora/plantilla', {
      method: 'POST', headers: { 'X-Admin-Token': _segToken() }, body: fd,
    });
    const d = await r.json();
    if (d.ok) { toast('✅ Plantilla subida'); loadSegAseguradoras(); }
    else toast('❌ ' + (d.error || 'Error'), false);
  } catch (e) { toast('❌ No se pudo conectar', false); }
}

async function verCamposAcroform(key) {
  const row = document.getElementById('seg-campos-' + key);
  if (!row) return;
  if (row.style.display !== 'none') { row.style.display = 'none'; return; }
  row.style.display = '';
  const pre = row.querySelector('pre');
  pre.textContent = 'Cargando…';
  try {
    const r = await fetch(_segUrl() + '/api/seguro/admin/aseguradora/campos-acroform?aseguradora=' + encodeURIComponent(key), { headers: _segHeaders(false) });
    const d = await r.json();
    pre.textContent = d.ok ? JSON.stringify(d.campos, null, 2) : ('Error: ' + (d.error || ''));
  } catch (e) { pre.textContent = 'No se pudo conectar.'; }
}

// ── 3. Prestaciones + mapeo por aseguradora ──────────
async function loadSegPrestaciones() {
  try {
    const r = await fetch(_segUrl() + '/api/seguro/admin/prestaciones', { headers: _segHeaders(false) });
    const d = await r.json();
    if (d.ok) {
      SEG_PRESTACIONES = d.prestaciones || [];
      SEG_MAPEO = d.mapeo || {};
      renderSegPrestaciones();
      renderSegMotivos();
    }
  } catch (e) {}
}

function _segItemRow(item) {
  return `<div style="display:flex;gap:6px;margin-bottom:6px">
    <input type="text" class="seg-item-codigo" value="${_esc(item.codigo || '')}" placeholder="Código" style="width:100px;padding:4px 6px;border:1px solid #cbd5e0;border-radius:4px">
    <input type="text" class="seg-item-desc" value="${_esc(item.descripcion || '')}" placeholder="Descripción" style="flex:1;padding:4px 6px;border:1px solid #cbd5e0;border-radius:4px">
    <button onclick="this.parentElement.remove()" style="background:none;border:none;color:#e53e3e;cursor:pointer">✕</button>
  </div>`;
}

function renderSegPrestaciones() {
  const cont = document.getElementById('seg-prestaciones');
  if (!SEG_PRESTACIONES.length) {
    cont.innerHTML = '<p style="color:#718096;font-size:.85rem">Sin prestaciones aún. Usa "Sembrar desde motivos" o agrega una nueva abajo.</p>';
    return;
  }
  cont.innerHTML = SEG_PRESTACIONES.map(p => {
    const mapeo = SEG_MAPEO[p.id] || {};
    const asegRows = SEG_ASEGURADORAS.map(a => {
      const items = mapeo[a.key] || [];
      return `
        <div style="margin-bottom:10px;padding:10px;background:#f8fafc;border-radius:8px">
          <strong style="font-size:.82rem">${_esc(a.nombre)}</strong>
          <div class="seg-items-list" data-prest="${p.id}" data-aseg="${a.key}">
            ${items.map(it => _segItemRow(it)).join('')}
          </div>
          <div style="display:flex;gap:8px;margin-top:6px">
            <button class="btn btn-sm" style="background:#e2e8f0" onclick="segAgregarItem('${p.id}','${a.key}')">+ ítem</button>
            <button class="btn btn-sm btn-primary" onclick="segGuardarMapeo('${p.id}','${a.key}')">Guardar mapeo</button>
          </div>
        </div>`;
    }).join('');
    return `
      <details style="margin-bottom:10px;border:1px solid #e2e8f0;border-radius:10px;padding:10px 14px" data-prestid="${p.id}">
        <summary style="cursor:pointer;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <input type="text" class="seg-prest-nombre" value="${_esc(p.nombre)}" style="flex:2;min-width:160px;padding:5px 8px;border:1px solid #cbd5e0;border-radius:6px" onclick="event.stopPropagation()">
          <input type="text" class="seg-prest-precio" value="${p.precio_arancel ?? 0}" style="width:100px;padding:5px 8px;border:1px solid #cbd5e0;border-radius:6px" onclick="event.stopPropagation()">
          <label style="font-weight:400;font-size:.82rem" onclick="event.stopPropagation()"><input type="checkbox" class="seg-prest-activa" ${p.activa !== false ? 'checked' : ''}> activa</label>
          <button class="btn btn-sm btn-primary" onclick="event.stopPropagation();guardarPrestacion(${p.id})">Guardar</button>
        </summary>
        <div style="margin-top:12px">
          <div class="row" style="margin-bottom:12px">
            <div style="flex:2;min-width:220px">
              <label title="Textos con que esta prestación aparece en la glosa de las boletas DTE (para el envío 1-clic desde el F2). Separa alternativas con coma; no distingue mayúsculas ni tildes">Alias en glosa de boleta</label>
              <input type="text" class="seg-prest-glosas" value="${_esc((p.glosas_boleta || []).join(', '))}" placeholder="ej: CONTROL MENSUAL, CONTROL DE ORTODONCIA" title="Textos con que esta prestación aparece en la glosa de las boletas DTE (para el envío 1-clic desde el F2). Separa alternativas con coma; no distingue mayúsculas ni tildes" style="width:100%;padding:5px 8px;border:1px solid #cbd5e0;border-radius:6px">
            </div>
            <div style="display:flex;align-items:flex-end;padding-bottom:8px">
              <label style="font-weight:400" title="El valor de esta prestación se calcula como el total de la boleta menos las demás prestaciones detectadas (úsalo SOLO en los controles/mensualidades)"><input type="checkbox" class="seg-prest-absorbe" ${p.absorbe_saldo ? 'checked' : ''}> Absorbe saldo</label>
            </div>
          </div>
          <h5 style="font-size:.8rem;color:#718096;margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px">Mapeo de códigos por aseguradora</h5>
          ${asegRows || '<p style="font-size:.82rem;color:#718096">No hay aseguradoras creadas aún.</p>'}
        </div>
      </details>`;
  }).join('');
}

async function seedPrestaciones() {
  const r = await _segFetch('/api/seguro/admin/prestaciones/seed-desde-motivos', 'POST', {});
  if (r.ok) { toast(`✅ ${r.creados || 0} prestaciones creadas`); loadSegPrestaciones(); }
  else toast('❌ ' + (r.error || 'Error'), false);
}

function _segParseGlosas(str) {
  return (str || '').split(',').map(s => s.trim()).filter(Boolean);
}

async function crearPrestacion() {
  const nombre = document.getElementById('seg-prest-nombre').value.trim();
  const precio = +document.getElementById('seg-prest-precio').value || 0;
  const glosas_boleta = _segParseGlosas(document.getElementById('seg-prest-glosas').value);
  const absorbe_saldo = document.getElementById('seg-prest-absorbe').checked;
  if (!nombre) { toast('Ingresa el nombre', false); return; }
  const r = await _segFetch('/api/seguro/admin/prestaciones', 'POST', { nombre, precio_arancel: precio, activa: true, glosas_boleta, absorbe_saldo });
  if (r.ok) {
    toast('✅ Prestación creada');
    document.getElementById('seg-prest-nombre').value = '';
    document.getElementById('seg-prest-precio').value = '';
    document.getElementById('seg-prest-glosas').value = '';
    document.getElementById('seg-prest-absorbe').checked = false;
    loadSegPrestaciones();
  } else toast('❌ ' + (r.error || 'Error'), false);
}

async function guardarPrestacion(id) {
  const det = document.querySelector(`details[data-prestid="${id}"]`);
  if (!det) return;
  const nombre = det.querySelector('.seg-prest-nombre').value.trim();
  const precio = +det.querySelector('.seg-prest-precio').value || 0;
  const activa = det.querySelector('.seg-prest-activa').checked;
  const glosas_boleta = _segParseGlosas(det.querySelector('.seg-prest-glosas').value);
  const absorbe_saldo = det.querySelector('.seg-prest-absorbe').checked;
  const r = await _segFetch('/api/seguro/admin/prestaciones', 'POST', { id, nombre, precio_arancel: precio, activa, glosas_boleta, absorbe_saldo });
  if (r.ok) { toast('✅ Prestación guardada'); loadSegPrestaciones(); }
  else toast('❌ ' + (r.error || 'Error'), false);
}

function segAgregarItem(prestId, asegKey) {
  const list = document.querySelector(`.seg-items-list[data-prest="${prestId}"][data-aseg="${asegKey}"]`);
  if (!list) return;
  const div = document.createElement('div');
  div.innerHTML = _segItemRow({});
  list.appendChild(div.firstElementChild);
}

async function segGuardarMapeo(prestId, asegKey) {
  const list = document.querySelector(`.seg-items-list[data-prest="${prestId}"][data-aseg="${asegKey}"]`);
  const items = [...list.children].map(row => ({
    codigo: row.querySelector('.seg-item-codigo').value.trim(),
    descripcion: row.querySelector('.seg-item-desc').value.trim(),
  })).filter(it => it.codigo || it.descripcion);
  const r = await _segFetch('/api/seguro/admin/mapeo-prestaciones', 'POST', { prest_id: prestId, aseguradora: asegKey, items });
  if (r.ok) {
    toast('✅ Mapeo guardado');
    if (!SEG_MAPEO[prestId]) SEG_MAPEO[prestId] = {};
    SEG_MAPEO[prestId][asegKey] = items;
  } else toast('❌ ' + (r.error || 'Error'), false);
}

// ── 4. Sugerencias por motivo ─────────────────────────
function _segPrestNombre(id) {
  const p = SEG_PRESTACIONES.find(x => String(x.id) === String(id));
  return p ? p.nombre : ('#' + id);
}

function renderSegMotivos() {
  const cont = document.getElementById('seg-motivos');
  const entries = Object.entries(SEG_MAPEO_MOTIVOS || {});
  if (!entries.length) {
    cont.innerHTML = '<p style="color:#718096;font-size:.85rem">Sin sugerencias configuradas aún.</p>';
  } else {
    cont.innerHTML = `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:.85rem">
      <thead><tr style="background:#f8fafc;text-align:left"><th style="padding:8px 10px">Motivo</th><th style="padding:8px 10px">Prestaciones sugeridas</th><th></th></tr></thead>
      <tbody>${entries.map(([motivo, ids]) => `
        <tr style="border-top:1px solid #e2e8f0">
          <td style="padding:8px 10px">${_esc(motivo)}</td>
          <td style="padding:8px 10px">${(ids || []).map(id => _esc(_segPrestNombre(id))).join(', ') || '—'}</td>
          <td style="padding:8px 10px"><button class="btn btn-sm" style="background:#e2e8f0" onclick='editarMapeoMotivo(${JSON.stringify(motivo)})'>Editar</button></td>
        </tr>`).join('')}</tbody>
    </table></div>`;
  }
  const sel = document.getElementById('seg-mot-prestaciones');
  if (sel) sel.innerHTML = SEG_PRESTACIONES.map(p => `<option value="${p.id}">${_esc(p.nombre)}</option>`).join('');
}

function editarMapeoMotivo(motivo) {
  document.getElementById('seg-mot-motivo').value = motivo;
  const ids = (SEG_MAPEO_MOTIVOS[motivo] || []).map(String);
  const sel = document.getElementById('seg-mot-prestaciones');
  [...sel.options].forEach(o => { o.selected = ids.includes(o.value); });
  sel.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function guardarMapeoMotivo() {
  const motivo = document.getElementById('seg-mot-motivo').value.trim();
  if (!motivo) { toast('Ingresa el motivo', false); return; }
  const sel = document.getElementById('seg-mot-prestaciones');
  const prestaciones = [...sel.selectedOptions].map(o => +o.value);
  const r = await _segFetch('/api/seguro/admin/mapeo-motivos', 'POST', { motivo, prestaciones });
  if (r.ok) {
    toast('✅ Sugerencia guardada');
    SEG_MAPEO_MOTIVOS[motivo] = prestaciones;
    renderSegMotivos();
  } else toast('❌ ' + (r.error || 'Error'), false);
}

// ── 5. Firmas de doctores ─────────────────────────────
async function loadSegFirmas() {
  try {
    const r = await fetch(_segUrl() + '/api/seguro/admin/firma', { headers: _segHeaders(false) });
    const d = await r.json();
    if (d.ok) { SEG_FIRMAS = d.firmas || []; renderSegFirmas(); }
  } catch (e) {}
}

function renderSegFirmas() {
  const cont = document.getElementById('seg-firmas');
  if (!SEG_FIRMAS.length) {
    cont.innerHTML = '<p style="color:#718096;font-size:.85rem">Sin firmas cargadas aún.</p>';
    return;
  }
  cont.innerHTML = `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:.85rem">
    <thead><tr style="background:#f8fafc;text-align:left">
      <th style="padding:8px 10px">Doctor</th><th style="padding:8px 10px">Nombre visible</th>
      <th style="padding:8px 10px">RUT</th><th style="padding:8px 10px">Especialidad</th>
      <th style="padding:8px 10px">Firma</th>
    </tr></thead>
    <tbody>${SEG_FIRMAS.map(f => `
      <tr style="border-top:1px solid #e2e8f0">
        <td style="padding:8px 10px;text-transform:capitalize">${_esc(f.key)}</td>
        <td style="padding:8px 10px">${_esc(f.nombre_visible || '—')}</td>
        <td style="padding:8px 10px">${_esc(_rutFmt(f.rut))}</td>
        <td style="padding:8px 10px">${_esc(f.especialidad || '—')}</td>
        <td style="padding:8px 10px">${f.imagen ? '✅' : '—'}</td>
      </tr>`).join('')}</tbody>
  </table></div>`;
}

async function subirFirma() {
  const doctor = document.getElementById('seg-firma-doctor').value;
  const file = document.getElementById('seg-firma-file').files[0];
  if (!file) { toast('Elige una imagen', false); return; }
  const fd = new FormData();
  fd.append('file', file);
  fd.append('doctor', doctor);
  fd.append('nombre_visible', document.getElementById('seg-firma-nombre').value);
  fd.append('rut', document.getElementById('seg-firma-rut').value);
  fd.append('especialidad', document.getElementById('seg-firma-especialidad').value);
  try {
    const r = await fetch(_segUrl() + '/api/seguro/admin/firma', { method: 'POST', headers: { 'X-Admin-Token': _segToken() }, body: fd });
    const d = await r.json();
    if (d.ok) { toast('✅ Firma subida'); document.getElementById('seg-firma-file').value = ''; loadSegFirmas(); }
    else toast('❌ ' + (d.error || 'Error'), false);
  } catch (e) { toast('❌ No se pudo conectar', false); }
}

// ══════════════════════════════════════════════════════
// CONTROL DENTAL — recordatorio automático por email (control_dental.py)
// ══════════════════════════════════════════════════════
let CD_INSCRITOS = [], CD_FILTRO = '';

function initControlDental() {
  // Comparte token/URL con Estadísticas/Consentimientos/WhatsApp/Seguros: mismo
  // ADMIN_TOKEN, mismo backend (patrón "remoto" — habla directo a Render).
  const t = localStorage.getItem('stats_token') || '';
  const u = localStorage.getItem('stats_url') || 'https://ortodonciarichard.onrender.com';
  document.getElementById('cd-token').value = t;
  document.getElementById('cd-url').value = u;
  if (t) loadControlDental();
}

function _cdUrl() { return document.getElementById('cd-url').value.trim().replace(/\/$/, ''); }
function _cdToken() { return document.getElementById('cd-token').value.trim(); }
function _cdHeaders(json = true) {
  const h = { 'X-Admin-Token': _cdToken() };
  if (json) h['Content-Type'] = 'application/json';
  return h;
}
async function _cdFetch(path, method = 'GET', body = null) {
  const opts = { method, headers: _cdHeaders() };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(_cdUrl() + path, opts);
  return r.json();
}

async function loadControlDental() {
  const token = _cdToken();
  if (!token) { toast('Ingresa el admin token', false); return; }
  localStorage.setItem('stats_token', token);
  localStorage.setItem('stats_url', _cdUrl());
  ['cd-inscritos', 'cd-fin-fase', 'cd-historial', 'cd-motivos-desconocidos'].forEach(id => {
    document.getElementById(id).innerHTML = 'Cargando…';
  });
  try {
    const r = await fetch(_cdUrl() + '/api/control-dental/config', { headers: _cdHeaders(false) });
    if (r.status === 403) { toast('Token incorrecto', false); return; }
    const d = await r.json();
    if (!d.ok) { toast('Error al cargar la configuración', false); return; }
    renderCdConfig(d.config);
  } catch (e) {
    toast('No se pudo conectar con el backend', false);
    return;
  }
  loadCdInscritos();
  loadCdHistorial();
  loadCdMotivosDesconocidos();
}

// ── 1. Configuración ──────────────────────────────────
function renderCdConfig(c) {
  document.getElementById('cd-cfg-activo').checked = !!c.activo;
  document.getElementById('cd-cfg-frecuencia').value = String(c.frecuencia_meses || 6);
  document.getElementById('cd-cfg-hora').value = c.hora_envio || '11:00';
  document.getElementById('cd-cfg-max-envios').value = c.max_envios_por_dia || 30;
  document.getElementById('cd-cfg-meses-pausa').value = c.meses_sin_actividad_pausa || 9;
}

async function guardarControlDentalConfig() {
  const body = {
    activo: document.getElementById('cd-cfg-activo').checked,
    frecuencia_meses: +document.getElementById('cd-cfg-frecuencia').value,
    hora_envio: document.getElementById('cd-cfg-hora').value,
    max_envios_por_dia: +document.getElementById('cd-cfg-max-envios').value || 30,
    meses_sin_actividad_pausa: +document.getElementById('cd-cfg-meses-pausa').value || 9,
  };
  const r = await _cdFetch('/api/control-dental/config', 'POST', body);
  if (r.ok) { toast('✅ Configuración guardada'); renderCdConfig(r.config); }
  else toast('❌ ' + (r.error || 'Error'), false);
}

async function ejecutarControlDentalAhora() {
  const out = document.getElementById('cd-run-resultado');
  out.textContent = '⏳ Ejecutando (barre la agenda y envía los correos vencidos)…';
  try {
    const r = await _cdFetch('/api/control-dental/run', 'POST', {});
    if (r.ok) {
      out.innerHTML = `<span style="color:#2f855a">✅ Enviados: ${r.enviados ?? 0} · Omitidos: ${r.omitidos ?? 0} · Sin email: ${r.sin_email ?? 0} · Quedan para mañana: ${r.pendientes_manana ?? 0}</span>`;
      loadCdInscritos();
      loadCdHistorial();
    } else {
      out.innerHTML = `<span style="color:#e53e3e">${_esc(r.error || 'Error desconocido')}</span>`;
    }
  } catch (e) {
    out.innerHTML = `<span style="color:#e53e3e">No se pudo conectar con el backend.</span>`;
  }
}

// ── 2. Inscritos ──────────────────────────────────────
function filtrarCdInscritos(estado, btn) {
  CD_FILTRO = estado;
  document.querySelectorAll('#cd-inscritos-filtros button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  loadCdInscritos();
}

const CD_ESTADO_LABEL = {
  activo: 'Activo', dado_de_baja: 'Dado de baja', sin_email: 'Sin email',
  pausado_inactivo: 'Pausado (inactivo)', desactivado_manual: 'Desactivado a mano',
};
const CD_ESTADO_COLOR = {
  activo: '#38a169', dado_de_baja: '#a0aec0', sin_email: '#e53e3e',
  pausado_inactivo: '#C9A84C', desactivado_manual: '#718096',
};
const CD_TIPO_LABEL_PANEL = { fijos: 'Fijos', alineadores: 'Alineadores', ambos: 'Ambos', manual: 'Manual' };
const CD_MOTIVO_BAJA_LABEL_PANEL = { fin_definitivo: 'Retiro definitivo', fin_fase: 'Retiro de fase' };

async function loadCdInscritos() {
  const cont = document.getElementById('cd-inscritos');
  const contFase = document.getElementById('cd-fin-fase');
  try {
    const url = _cdUrl() + '/api/control-dental/inscritos' + (CD_FILTRO ? '?estado=' + encodeURIComponent(CD_FILTRO) : '');
    const r = await fetch(url, { headers: _cdHeaders(false) });
    if (r.status === 403) { cont.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">Token incorrecto.</p>'; return; }
    const d = await r.json();
    if (!d.ok) { cont.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">Error al cargar los inscritos.</p>'; return; }
    CD_INSCRITOS = d.items || [];
  } catch (e) {
    cont.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">No se pudo conectar con el backend.</p>';
    return;
  }
  renderCdInscritos();

  // Sub-lista "Bajas por retiro de fase": si el filtro activo no es ya
  // 'dado_de_baja', se pide aparte (no queremos que dependa del filtro elegido
  // arriba — esta lista siempre debe estar visible).
  try {
    const items = CD_FILTRO === 'dado_de_baja' ? CD_INSCRITOS : (await (
      await fetch(_cdUrl() + '/api/control-dental/inscritos?estado=dado_de_baja', { headers: _cdHeaders(false) })
    ).json()).items || [];
    renderCdFinFase((items || []).filter(i => i.motivo_baja === 'fin_fase'));
  } catch (e) {
    contFase.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">No se pudo conectar con el backend.</p>';
  }
}

function renderCdInscritos() {
  const cont = document.getElementById('cd-inscritos');
  if (!CD_INSCRITOS.length) {
    cont.innerHTML = '<p style="color:#718096;font-size:.85rem">Sin inscritos que coincidan con este filtro.</p>';
    return;
  }
  cont.innerHTML = `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:.85rem">
    <thead><tr style="background:#f8fafc;text-align:left">
      <th style="padding:8px 10px">Paciente</th><th style="padding:8px 10px">Tipo</th>
      <th style="padding:8px 10px">Estado</th><th style="padding:8px 10px">Próximo aviso</th>
      <th style="padding:8px 10px">Frecuencia</th><th style="padding:8px 10px"></th>
    </tr></thead>
    <tbody>${CD_INSCRITOS.map(i => _cdFilaInscrito(i)).join('')}</tbody>
  </table></div>`;
}

function _cdFilaInscrito(i) {
  const rutSafe = _esc(String(i.rut || '').replace(/[^a-zA-Z0-9]/g, '_'));
  const activo = i.estado === 'activo';
  return `
    <tr style="border-top:1px solid #e2e8f0">
      <td style="padding:8px 10px">${_esc(i.nombre || '—')}<br><span style="font-size:.78rem;color:#718096">${_esc(_rutFmt(i.rut))}</span></td>
      <td style="padding:8px 10px">${_esc(CD_TIPO_LABEL_PANEL[i.tipo] || i.tipo || '—')}</td>
      <td style="padding:8px 10px">
        <span class="tag" style="border-color:${CD_ESTADO_COLOR[i.estado] || '#a0aec0'};color:${CD_ESTADO_COLOR[i.estado] || '#a0aec0'}">${_esc(CD_ESTADO_LABEL[i.estado] || i.estado)}</span>
        ${i.motivo_baja ? `<br><span style="font-size:.75rem;color:#718096">${_esc(CD_MOTIVO_BAJA_LABEL_PANEL[i.motivo_baja] || i.motivo_baja)}</span>` : ''}
      </td>
      <td style="padding:8px 10px;color:#718096">${i.proximo_envio ? _esc(_fechaCorta(i.proximo_envio)) : '—'}</td>
      <td style="padding:8px 10px">${i.frecuencia_meses ? i.frecuencia_meses + ' meses' : '—'}</td>
      <td style="padding:8px 10px;white-space:nowrap">
        <button class="btn btn-sm" id="cd-btn-toggle-${rutSafe}" style="background:#e2e8f0" onclick="cdToggleActivo('${_esc(i.rut)}', ${activo})">${activo ? 'Desactivar' : 'Activar'}</button>
        <button class="btn btn-sm" id="cd-btn-nm-${rutSafe}" style="background:#e2e8f0" onclick="cdNoMolestar('${_esc(i.rut)}')">🚫 No molestar</button>
      </td>
    </tr>`;
}

async function cdToggleActivo(rut, estabaActivo) {
  const rutSafe = String(rut).replace(/[^a-zA-Z0-9]/g, '_');
  const btn = document.getElementById('cd-btn-toggle-' + rutSafe);
  if (btn) { btn.disabled = true; }
  const r = await _cdFetch('/api/control-dental/paciente', 'POST', { rut, activo: !estabaActivo });
  if (r.ok) { toast(estabaActivo ? '✅ Desactivado' : '✅ Activado'); loadCdInscritos(); }
  else { toast('❌ ' + (r.error || 'Error'), false); if (btn) btn.disabled = false; }
}

async function cdNoMolestar(rut) {
  if (!confirm('¿Marcar a este paciente para que NO se le vuelvan a enviar recordatorios de control dental?')) return;
  const r = await _cdFetch('/api/control-dental/no-molestar', 'POST', { rut });
  toast(r.ok ? '✅ Marcado — no se le enviarán más recordatorios' : '❌ ' + (r.error || 'Error'), r.ok);
}

function renderCdFinFase(items) {
  const cont = document.getElementById('cd-fin-fase');
  if (!items.length) {
    cont.innerHTML = '<p style="color:#718096;font-size:.85rem">Sin bajas de este tipo pendientes de revisar.</p>';
    return;
  }
  cont.innerHTML = `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:.85rem">
    <thead><tr style="background:#fffaf0;text-align:left">
      <th style="padding:8px 10px">Paciente</th><th style="padding:8px 10px">Dado de baja el</th><th style="padding:8px 10px"></th>
    </tr></thead>
    <tbody>${items.map(i => {
      const rutSafe = _esc(String(i.rut || '').replace(/[^a-zA-Z0-9]/g, '_'));
      return `<tr style="border-top:1px solid #fbd38d">
        <td style="padding:8px 10px">${_esc(i.nombre || '—')}<br><span style="font-size:.78rem;color:#718096">${_esc(_rutFmt(i.rut))}</span></td>
        <td style="padding:8px 10px;color:#718096">${i.fecha_baja ? _esc(_fechaCorta(i.fecha_baja)) : '—'}</td>
        <td style="padding:8px 10px"><button class="btn btn-sm btn-gold" id="cd-btn-reactivar-${rutSafe}" onclick="cdReactivar('${_esc(i.rut)}')">↩️ Reactivar</button></td>
      </tr>`;
    }).join('')}</tbody>
  </table></div>`;
}

async function cdReactivar(rut) {
  const rutSafe = String(rut).replace(/[^a-zA-Z0-9]/g, '_');
  const btn = document.getElementById('cd-btn-reactivar-' + rutSafe);
  if (btn) btn.disabled = true;
  const r = await _cdFetch('/api/control-dental/paciente', 'POST', { rut, activo: true });
  if (r.ok) { toast('✅ Reactivado'); loadCdInscritos(); }
  else { toast('❌ ' + (r.error || 'Error'), false); if (btn) btn.disabled = false; }
}

// ── 3. Historial ──────────────────────────────────────
async function loadCdHistorial() {
  const cont = document.getElementById('cd-historial');
  try {
    const r = await fetch(_cdUrl() + '/api/control-dental/historial', { headers: _cdHeaders(false) });
    const d = await r.json();
    if (!d.ok) { cont.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">Error al cargar el historial.</p>'; return; }
    renderCdHistorial(d.items || []);
  } catch (e) {
    cont.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">No se pudo conectar con el backend.</p>';
  }
}

function renderCdHistorial(items) {
  const cont = document.getElementById('cd-historial');
  if (!items.length) {
    cont.innerHTML = '<p style="color:#718096;font-size:.85rem">Sin envíos registrados aún.</p>';
    return;
  }
  cont.innerHTML = `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:.85rem">
    <thead><tr style="background:#f8fafc;text-align:left">
      <th style="padding:8px 10px">Fecha</th><th style="padding:8px 10px">Paciente</th><th style="padding:8px 10px">Email</th>
    </tr></thead>
    <tbody>${items.map(e => `
      <tr style="border-top:1px solid #e2e8f0">
        <td style="padding:8px 10px;color:#718096">${_esc(_fechaCorta(e.fecha))}</td>
        <td style="padding:8px 10px">${_esc(e.nombre || '—')}<br><span style="font-size:.78rem;color:#718096">${_esc(_rutFmt(e.rut))}</span></td>
        <td style="padding:8px 10px">${_esc(e.email || '—')}</td>
      </tr>`).join('')}</tbody>
  </table></div>`;
}

// ── 4. Motivos sin clasificar ──────────────────────────
const CD_CATEGORIA_OPCIONES = [
  ['inicio_fijos', 'Inicio — aparatos fijos'],
  ['inicio_alineadores', 'Inicio — alineadores'],
  ['fin_definitivo', 'Fin definitivo'],
  ['fin_fase', 'Fin de fase (revisar)'],
  ['control', 'Control (solo señal de vida)'],
];

async function loadCdMotivosDesconocidos() {
  const cont = document.getElementById('cd-motivos-desconocidos');
  cont.innerHTML = 'Cargando…';
  try {
    const r = await fetch(_cdUrl() + '/api/control-dental/motivos-desconocidos', { headers: _cdHeaders(false) });
    if (r.status === 403) { cont.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">Token incorrecto.</p>'; return; }
    const d = await r.json();
    if (!d.ok) { cont.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">Error al cargar los motivos.</p>'; return; }
    renderCdMotivosDesconocidos(d.motivos || []);
  } catch (e) {
    cont.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">No se pudo conectar con el backend.</p>';
  }
}

function renderCdMotivosDesconocidos(motivos) {
  const cont = document.getElementById('cd-motivos-desconocidos');
  if (!motivos.length) {
    cont.innerHTML = '<p style="color:#718096;font-size:.85rem">No hay motivos sin clasificar.</p>';
    return;
  }
  // Ya viene ordenado por el backend (más frecuentes primero) — no reordenar.
  cont.innerHTML = `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:.85rem">
    <thead><tr style="background:#f8fafc;text-align:left">
      <th style="padding:8px 10px">Motivo (Reason)</th><th style="padding:8px 10px">Veces visto</th>
      <th style="padding:8px 10px">Última vez</th><th style="padding:8px 10px">Categoría</th><th style="padding:8px 10px"></th>
    </tr></thead>
    <tbody>${motivos.map((m, idx) => `
      <tr style="border-top:1px solid #e2e8f0">
        <td style="padding:8px 10px">${_esc(m.reason)}</td>
        <td style="padding:8px 10px">${m.n ?? 0}</td>
        <td style="padding:8px 10px;color:#718096">${m.ultima ? _esc(_fechaCorta(m.ultima)) : '—'}</td>
        <td style="padding:8px 10px">
          <select id="cd-md-categoria-${idx}">
            ${CD_CATEGORIA_OPCIONES.map(([v, l]) => `<option value="${v}">${_esc(l)}</option>`).join('')}
          </select>
        </td>
        <td style="padding:8px 10px">
          <button class="btn btn-sm btn-primary" onclick='clasificarCdMotivoDesconocido(${JSON.stringify(m.reason)}, ${idx})'>Guardar</button>
        </td>
      </tr>`).join('')}</tbody>
  </table></div>`;
}

async function clasificarCdMotivoDesconocido(reason, idx) {
  const categoria = document.getElementById('cd-md-categoria-' + idx).value;
  const r = await _cdFetch('/api/control-dental/motivo', 'POST', { reason, categoria });
  if (r.ok) { toast('✅ Motivo clasificado'); loadCdMotivosDesconocidos(); }
  else toast('❌ ' + (r.error || 'Error'), false);
}

// Formulario secundario (details plegado): clasificar a mano un motivo que
// todavía no apareció en el barrido (no viene en la lista de arriba porque
// esa lista solo trae lo que el barrido YA vio en la agenda).
async function guardarCdMotivo() {
  const reason = document.getElementById('cd-motivo-reason').value.trim();
  const categoria = document.getElementById('cd-motivo-categoria').value;
  if (!reason) { toast('Ingresa el texto exacto del motivo', false); return; }
  const r = await _cdFetch('/api/control-dental/motivo', 'POST', { reason, categoria });
  if (r.ok) {
    toast('✅ Motivo clasificado');
    document.getElementById('cd-motivo-reason').value = '';
    loadCdMotivosDesconocidos();
  } else toast('❌ ' + (r.error || 'Error'), false);
}

// ── 5. Envío de prueba ─────────────────────────────────
async function enviarCdPrueba() {
  const email = document.getElementById('cd-test-email').value.trim();
  const out   = document.getElementById('cd-test-resultado');
  if (!email || email.indexOf('@') < 0) {
    out.innerHTML = '<span style="color:#c53030">Ingresa un email válido.</span>';
    return;
  }
  const btn = document.getElementById('cd-test-btn');
  btn.disabled = true;
  out.innerHTML = '<span style="color:#718096">Enviando…</span>';
  try {
    const d = await _cdFetch('/api/control-dental/test', 'POST', {
      email,
      nombre: document.getElementById('cd-test-nombre').value.trim(),
      rut: document.getElementById('cd-test-rut').value.trim(),
      frecuencia_meses: parseInt(document.getElementById('cd-test-frecuencia').value, 10),
    });
    if (!d.ok) {
      out.innerHTML = `<span style="color:#c53030">❌ ${d.error || 'No se pudo enviar'}</span>`;
      return;
    }
    // Se avisa con qué saludo salió: es la diferencia entre la prueba y el
    // envío real más fácil de pasar por alto (sin RUT queda "Estimado/a").
    const nota = d.ficha_encontrada
      ? `Saludo usado: «${d.saludo}» (según el género de la ficha).`
      : `Saludo usado: «${d.saludo}» — genérico, porque no se encontró ficha para ese RUT. En un envío real siempre hay RUT.`;
    out.innerHTML = `<span style="color:#276749">✅ Enviado a ${d.enviado_a}. Revisa la bandeja (y el spam la primera vez).</span>`
      + `<br><span style="color:#718096">${nota}</span>`;
  } catch (e) {
    out.innerHTML = `<span style="color:#c53030">❌ ${e.message}</span>`;
  } finally {
    btn.disabled = false;
  }
}

// ── 6. Backfill ────────────────────────────────────────
async function ejecutarCdBackfill() {
  if (!confirm('Esto barre ~6 meses de agenda en DentiDesk e inscribe a la cartera actual. Puede demorar varios minutos. ¿Continuar?')) return;
  const btn = document.getElementById('cd-backfill-btn');
  const out = document.getElementById('cd-backfill-resultado');
  btn.disabled = true;
  btn.textContent = '⏳ Corriendo en segundo plano (puede demorar varios minutos)…';
  out.innerHTML = '';
  try {
    const r = await _cdFetch('/api/control-dental/backfill', 'POST', { meses: 6 });
    if (r.ok) {
      out.innerHTML = '<span style="color:#2f855a">✅ Iniciado. Corre en el servidor; vuelve a esta pestaña en unos minutos y recarga los inscritos.</span>';
    } else {
      out.innerHTML = `<span style="color:#e53e3e">${_esc(r.error || 'Error desconocido')}</span>`;
    }
  } catch (e) {
    out.innerHTML = '<span style="color:#e53e3e">No se pudo conectar con el backend.</span>';
  }
  setTimeout(() => {
    btn.disabled = false;
    btn.textContent = 'Inscribir cartera actual (6 meses atrás)';
  }, 3 * 60 * 1000);
}

// ══════════════════════════════════════════════════════
// SATISFACCIÓN — encuestas NPS por WhatsApp
// ══════════════════════════════════════════════════════
function initSatisfaccion() {
  // Comparte token/URL con Estadísticas/Consentimientos/WhatsApp/Seguros/Control
  // dental: mismo ADMIN_TOKEN, mismo backend (patrón "remoto").
  const t = localStorage.getItem('stats_token') || '';
  const u = localStorage.getItem('stats_url') || 'https://ortodonciarichard.onrender.com';
  document.getElementById('sat-token').value = t;
  document.getElementById('sat-url').value = u;
  if (t) loadSatisfaccion();
}

function _satUrl() { return document.getElementById('sat-url').value.trim().replace(/\/$/, ''); }
function _satToken() { return document.getElementById('sat-token').value.trim(); }
function _satHeaders(json = true) {
  const h = { 'X-Admin-Token': _satToken() };
  if (json) h['Content-Type'] = 'application/json';
  return h;
}
async function _satFetch(path, method = 'GET', body = null) {
  const opts = { method, headers: _satHeaders() };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(_satUrl() + path, opts);
  return r.json();
}

async function loadSatisfaccion() {
  const token = _satToken();
  if (!token) { toast('Ingresa el admin token', false); return; }
  localStorage.setItem('stats_token', token);
  localStorage.setItem('stats_url', _satUrl());
  const statsEl = document.getElementById('sat-stats');
  statsEl.innerHTML = '<h3 style="margin-bottom:10px">📊 Estadísticas</h3><p style="font-size:.85rem;color:#718096">Cargando…</p>';

  let cfgData, resumenData;
  try {
    const [rCfg, rResumen] = await Promise.all([
      fetch(_satUrl() + '/api/nps/config', { headers: _satHeaders(false) }),
      fetch(_satUrl() + '/api/nps/resumen', { headers: _satHeaders(false) }),
    ]);
    if (rCfg.status === 403 || rResumen.status === 403) {
      toast('Token incorrecto', false);
      statsEl.innerHTML = '<h3 style="margin-bottom:10px">📊 Estadísticas</h3><p style="font-size:.85rem;color:#e53e3e">Token incorrecto.</p>';
      return;
    }
    cfgData = await rCfg.json();
    resumenData = await rResumen.json();
  } catch (e) {
    toast('No se pudo conectar con el backend', false);
    statsEl.innerHTML = '<h3 style="margin-bottom:10px">📊 Estadísticas</h3><p style="font-size:.85rem;color:#e53e3e">No se pudo conectar con el backend.</p>';
    return;
  }
  if (!cfgData.ok) { toast('Error al cargar la configuración', false); return; }
  if (!resumenData.ok) { toast('Error al cargar el resumen', false); return; }

  renderSatConfig(cfgData.config);
  renderSatStats(resumenData.resumen);
  loadSatPacientes();
}

function renderSatConfig(c) {
  document.getElementById('sat-activo').checked = !!c.activo;
  document.getElementById('sat-periodico').checked = !!c.periodico_activo;
  document.getElementById('sat-review-url').value = c.review_url || '';
  document.getElementById('sat-horas-despues').value = c.horas_despues_atencion ?? 24;
  document.getElementById('sat-ventana-inicio').value = c.ventana_inicio || '09:00';
  document.getElementById('sat-ventana-fin').value = c.ventana_fin || '19:30';
  document.getElementById('sat-frecuencia-meses').value = c.frecuencia_meses ?? 6;
  document.getElementById('sat-cooldown-meses').value = c.cooldown_meses ?? 3;
  document.getElementById('sat-silencio-promotor-meses').value = c.silencio_promotor_meses ?? 12;
  document.getElementById('sat-max-envios-dia').value = c.max_envios_por_dia ?? 30;
  document.getElementById('sat-buena').value = c.nps_buena_es || 'pasivo';
}

async function saveSatisfaccion() {
  const body = {
    activo: document.getElementById('sat-activo').checked,
    periodico_activo: document.getElementById('sat-periodico').checked,
    review_url: document.getElementById('sat-review-url').value.trim(),
    horas_despues_atencion: parseInt(document.getElementById('sat-horas-despues').value, 10) || 0,
    ventana_inicio: document.getElementById('sat-ventana-inicio').value,
    ventana_fin: document.getElementById('sat-ventana-fin').value,
    frecuencia_meses: parseInt(document.getElementById('sat-frecuencia-meses').value, 10) || 1,
    cooldown_meses: parseInt(document.getElementById('sat-cooldown-meses').value, 10) || 0,
    silencio_promotor_meses: parseInt(document.getElementById('sat-silencio-promotor-meses').value, 10) || 0,
    max_envios_por_dia: parseInt(document.getElementById('sat-max-envios-dia').value, 10) || 1,
    nps_buena_es: document.getElementById('sat-buena').value,
  };
  const r = await _satFetch('/api/nps/config', 'POST', body);
  if (r.ok) { toast('✅ Configuración guardada'); loadSatisfaccion(); }
  else toast('❌ ' + (r.error || 'Error'), false);
}

function _satPct(x) {
  if (x === null || x === undefined) return '—';
  return Math.round(x * 100) + '%';
}

function renderSatStats(r) {
  const el = document.getElementById('sat-stats');
  if (!r) { el.innerHTML = '<h3 style="margin-bottom:10px">📊 Estadísticas</h3><p style="font-size:.85rem;color:#718096">Sin datos aún.</p>'; return; }
  const navy = '#1A2E4A', gold = '#C9A84C';
  const npsTxt = (r.nps === null || r.nps === undefined) ? '—' : r.nps;
  const npsColor = (r.nps === null || r.nps === undefined) ? '#a0aec0' : (r.nps >= 50 ? '#38a169' : (r.nps >= 0 ? '#C9A84C' : '#e53e3e'));

  // Reseñas/mes del mes más reciente en resenas_mes, vs baseline.
  const meses = Object.keys(r.resenas_mes || {}).sort();
  const ultimoMes = meses.length ? meses[meses.length - 1] : null;
  const datoMes = ultimoMes ? r.resenas_mes[ultimoMes] : null;
  const baseResenas = r.baseline ? r.baseline.resenas_mensuales_prom : null;
  const baseRating = r.baseline ? r.baseline.rating : null;

  el.innerHTML = `
    <h3 style="margin-bottom:14px">📊 Estadísticas</h3>
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px">
      <div class="card" style="flex:1;min-width:160px;text-align:center;border-top:3px solid ${npsColor}">
        <div style="font-size:2.4rem;font-weight:700;color:${npsColor}">${npsTxt}</div>
        <div style="font-size:.82rem;color:#718096">NPS</div>
      </div>
      ${_statCard('Promotores', r.promotores ?? 0, '#38a169')}
      ${_statCard('Pasivos', r.pasivos ?? 0, gold)}
      ${_statCard('Detractores', r.detractores ?? 0, '#e53e3e')}
    </div>
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px">
      ${_statCard('Encuestas enviadas', r.enviadas ?? 0, navy)}
      ${_statCard('Tasa de respuesta', _satPct(r.tasa_respuesta), navy)}
      ${_statCard('Mediana atención → respuesta (días)', (r.mediana_atencion_respuesta_dias ?? '—'), navy)}
    </div>
    <div class="card" style="margin-bottom:12px">
      <h4 style="font-size:.88rem;margin-bottom:8px">Reseñas/mes vs línea base</h4>
      ${ultimoMes
        ? `<p style="font-size:.95rem">${_esc(ultimoMes)}: <strong>${datoMes.resenas}</strong> reseñas, rating <strong>${datoMes.rating}</strong>${baseResenas !== null ? ` (base: ${baseResenas}/mes, rating ${baseRating})` : ''}</p>`
        : '<p style="font-size:.85rem;color:#718096">Sin métricas de mes cargadas aún.</p>'}
      <p style="font-size:.9rem">Rating últimos 90 días: <strong>${r.rating_reciente ?? '—'}</strong>${baseRating !== null ? ` (base: ${baseRating})` : ''}</p>
    </div>
    <p style="font-size:.74rem;color:#a0aec0">
      El tiempo real hasta que la reseña aparece en Google no es atribuible por paciente; se muestra el tiempo hasta que el paciente responde la encuesta.
    </p>`;
}

async function saveMetricaMensual() {
  const mes = document.getElementById('sat-metrica-mes').value;
  const resenas = parseInt(document.getElementById('sat-metrica-resenas').value, 10);
  const rating = parseFloat(document.getElementById('sat-metrica-rating').value);
  if (!mes) { toast('Elige el mes', false); return; }
  if (isNaN(resenas) || isNaN(rating)) { toast('Ingresa reseñas y rating', false); return; }
  const r = await _satFetch('/api/nps/metrica-mensual', 'POST', { mes, resenas, rating });
  if (r.ok) { toast('✅ Mes guardado'); loadSatisfaccion(); }
  else toast('❌ ' + (r.error || 'Error'), false);
}

async function saveBaseline() {
  const resenas_mensuales_prom = parseFloat(document.getElementById('sat-base-resenas').value);
  const rating = parseFloat(document.getElementById('sat-base-rating').value);
  if (isNaN(resenas_mensuales_prom) || isNaN(rating)) { toast('Ingresa ambos valores de la línea base', false); return; }
  const r = await _satFetch('/api/nps/baseline', 'POST', { resenas_mensuales_prom, rating, meses: [] });
  if (r.ok) { toast('✅ Baseline guardada'); loadSatisfaccion(); }
  else toast('❌ ' + (r.error || 'Error'), false);
}

async function loadSatPacientes() {
  const conts = { promotor: 'sat-lista-promotor', pasivo: 'sat-lista-pasivo', detractor: 'sat-lista-detractor' };
  for (const cat of Object.keys(conts)) {
    const cont = document.getElementById(conts[cat]);
    cont.innerHTML = 'Cargando…';
    try {
      const r = await fetch(_satUrl() + '/api/nps/pacientes?categoria=' + encodeURIComponent(cat), { headers: _satHeaders(false) });
      if (r.status === 403) { cont.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">Token incorrecto.</p>'; continue; }
      const d = await r.json();
      if (!d.ok) { cont.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">Error al cargar.</p>'; continue; }
      renderSatLista(cont, d.items || []);
    } catch (e) {
      cont.innerHTML = '<p style="color:#e53e3e;font-size:.85rem">No se pudo conectar con el backend.</p>';
    }
  }
}

function renderSatLista(cont, items) {
  if (!items.length) { cont.innerHTML = '<p style="color:#718096;font-size:.85rem">Sin pacientes en esta categoría.</p>'; return; }
  cont.innerHTML = items.map(i => {
    const rutLimpio = String(i.rut || '').replace(/[^a-zA-Z0-9]/g, '_');
    return `<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:8px 0;border-top:1px solid #e2e8f0">
      <div>
        <span style="font-size:.88rem">${_esc(i.nombre || _rutFmt(i.rut))}</span>
        <span style="font-size:.78rem;color:#718096"> · ${_esc(i.doctor || '—')} · ${_esc(_fechaCorta(i.fecha))}</span>
      </div>
      <button class="btn btn-sm" id="btn-sat-nm-${_esc(rutLimpio)}" style="background:#e2e8f0;color:#2d3748"
        onclick="satNoMolestar('${_esc(i.rut)}')" title="No volver a encuestar a este paciente">🚫</button>
    </div>`;
  }).join('');
}

async function satNoMolestar(rut) {
  const rutLimpio = String(rut).replace(/[^a-zA-Z0-9]/g, '_');
  const btn = document.getElementById('btn-sat-nm-' + rutLimpio);
  if (btn) { btn.disabled = true; }
  const r = await _satFetch('/api/nps/no-molestar', 'POST', { rut });
  if (r.ok) { toast('✅ Paciente marcado como no molestar'); loadSatPacientes(); }
  else { toast('❌ ' + (r.error || 'Error'), false); if (btn) btn.disabled = false; }
}

// Iniciar
loadFotos();
