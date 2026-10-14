/* Tareas — Ortodoncia Richard
   Sitio propio (/tareas) para la bolsa de tareas de los tiempos muertos. Usa los MISMOS
   usuarios y la MISMA sesión que Compras (/api/compras/login, localStorage
   'compras_token'): quien entra a uno queda dentro del otro. Lo que cada persona ve lo
   decide su PUESTO; un administrador además gestiona propuesta, plantillas y personas. */
'use strict';

const API = (localStorage.getItem('compras_api') || '').replace(/\/$/, '');
let TOKEN = localStorage.getItem('compras_token') || '';
let ME = null;
let TAB = 'mias';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const puede = cap => (ME?.caps || []).includes(cap);
const PUESTOS_LABEL = { '': '— sin puesto —', secretaria: 'Recepción', asistente_orto: 'Asistente de ortodoncia',
  asistente_vial: 'Asistente Dr. Vial', aseo: 'Aseo' };
const optsPuesto = (sel, conVacio = true) => Object.entries(PUESTOS_LABEL)
  .filter(([v]) => conVacio || v)
  .map(([v, l]) => `<option value="${v}" ${(sel || '') === v ? 'selected' : ''}>${l}</option>`).join('');

async function api(path, opts = {}) {
  const o = { headers: {}, ...opts };
  o.headers['X-Compras-Token'] = TOKEN;
  if (o.body) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(o.body); }
  const r = await fetch(API + path, o);
  if (r.status === 401 && ME) { logout(); throw new Error('Sesión expirada'); }
  const j = await r.json().catch(() => ({ ok: false, error: 'Error ' + r.status }));
  if (!r.ok || j.ok === false) throw new Error(j.error || 'Error ' + r.status);
  return j;
}
function toast(msg, tipo = '') {
  const t = document.createElement('div');
  t.className = 'toast ' + tipo; t.textContent = msg;
  $('#toast').appendChild(t);
  setTimeout(() => t.remove(), tipo === 'err' ? 4500 : 2600);
}
function modal(html) {
  const root = $('#modalRoot');
  root.innerHTML = `<div class="overlay"><div class="modal">${html}</div></div>`;
  root.querySelector('.overlay').addEventListener('click', e => {
    if (e.target.classList.contains('overlay')) closeModal();
  });
  return root.querySelector('.modal');
}
function closeModal() { $('#modalRoot').innerHTML = ''; }

async function boot() {
  try { ME = (await api('/api/compras/me')).usuario; entrar(); }
  catch { mostrarLogin(); }
}
function mostrarLogin() {
  $('#app').classList.add('hidden'); $('#auth').classList.remove('hidden');
  $('#btnAuth').onclick = doLogin;
  $('#inPass').onkeydown = e => { if (e.key === 'Enter') doLogin(); };
}
async function doLogin() {
  try {
    const j = await api('/api/compras/login', { method: 'POST', body: { email: $('#inEmail').value, password: $('#inPass').value } });
    TOKEN = j.token; localStorage.setItem('compras_token', TOKEN); ME = j.usuario; entrar();
  } catch (e) { toast(e.message, 'err'); }
}
function logout() {
  api('/api/compras/logout', { method: 'POST' }).catch(() => {});
  TOKEN = ''; ME = null; localStorage.removeItem('compras_token'); mostrarLogin();
}
function entrar() {
  $('#auth').classList.add('hidden'); $('#app').classList.remove('hidden');
  $('#uName').textContent = ME.nombre;
  $('#uRole').textContent = ME.puesto ? PUESTOS_LABEL[ME.puesto] : ME.rol;
  $('#btnLogout').onclick = logout;
  if (!puede('tareas')) {
    $('main').innerHTML = '<div class="card empty muted">Tu usuario no tiene acceso a Tareas.</div>';
    $('#tabs').classList.add('hidden');
    return;
  }
  // Compras solo para quien tiene algo que hacer ahí.
  $('#lnkCompras').classList.toggle('hidden', ME.rol === 'equipo');
  const esAdmin = puede('tareas_admin');
  $('#tabs [data-tab="admin"]').classList.toggle('hidden', !esAdmin);
  $('#tabs [data-tab="personas"]').classList.toggle('hidden', !esAdmin);
  $('#tabs [data-tab="mias"]').classList.toggle('hidden', !ME.puesto && esAdmin);
  $('#tabs').classList.toggle('hidden', !esAdmin);   // una sola pestaña: sin barra
  actualizarBadgeTar(ME.tareas_disponibles || 0);
  irTab(ME.puesto || !esAdmin ? 'mias' : 'admin');
}
function actualizarBadgeTar(n) {
  const b = $('#badgeTar'); if (!b) return;
  b.textContent = n; b.classList.toggle('hidden', !(n > 0 && ME?.puesto));
}

const RENDER = {};
function irTab(tab) {
  TAB = tab;
  $$('#tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  $$('main > section').forEach(s => s.classList.toggle('hidden', s.id !== 'tab-' + tab));
  (RENDER[tab] || (() => {}))();
}
function refrescar() { irTab(TAB); }
$('#tabs').addEventListener('click', e => { const b = e.target.closest('button'); if (b) irTab(b.dataset.tab); });

/* ══════════════════ TAREAS (tiempos muertos) ══════════════════ */
// Cada persona ve la bolsa de SU puesto. Un administrador ve además la propuesta del
// día, los encargos, las plantillas que se repiten y la configuración.
const PRIO = { 1: ['Alta', 'low'], 2: ['Normal', 'fijo'], 3: ['Baja', 'variable'] };
const ORIGEN_LABEL = { plantilla: '🔁 fija', sistema: '★ del sistema', encargo: '📌 encargo' };
const FREC_LABEL = { diaria: 'Diaria', semanal: 'Semanal', mensual: 'Mensual' };
const DIAS_SEM = [[1, 'Lu'], [2, 'Ma'], [3, 'Mi'], [4, 'Ju'], [5, 'Vi']];
const horas = m => (m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')}` : `${m} min`);

function tarjetaTarea(t, botones) {
  const [pl, pc] = PRIO[t.prioridad] || PRIO[2];
  return `<div class="card" style="padding:14px;margin-bottom:10px">
    <div class="flex wrap" style="gap:6px">
      <b style="font-size:15px">${esc(t.titulo)}</b><div class="spacer"></div>
      ${t.de_hoy ? '<span class="pill ok">de hoy</span>' : ''}
      <span class="pill ${pc}">${pl}</span><span class="pill">${t.minutos} min</span>
    </div>
    <div class="muted" style="font-size:12px;margin-top:4px">${ORIGEN_LABEL[t.origen] || ''}
      ${t.asignada_nombre ? ' · para ' + esc(t.asignada_nombre) : ''}${t.vence ? ' · hasta ' + esc(t.vence) : ''}</div>
    ${t.detalle ? `<div style="white-space:pre-line;font-size:13px;margin-top:8px">${esc(t.detalle)}</div>` : ''}
    <div class="flex wrap" style="margin-top:10px;gap:6px">${botones}</div></div>`;
}

async function accionTarea(accion, id, nota) {
  try {
    await api('/api/compras/tareas/' + accion, { method: 'POST', body: { id, nota } });
    toast({ tomar: 'Tarea tomada ✓', terminar: '¡Hecha! ✓', soltar: 'Devuelta a la bolsa' }[accion] || 'Listo ✓', 'ok');
    closeModal(); refrescar();
  } catch (e) { toast(e.message, 'err'); }
}

function terminarConNota(id) {
  const m = modal(`<h3>Marcar como hecha</h3>
    <div class="field"><label>Nota (opcional)</label><textarea id="tnNota" rows="2" placeholder="Ej: faltaban 2 cajas de guantes, las pedí"></textarea></div>
    <div class="flex" style="margin-top:6px"><div class="spacer"></div>
      <button class="btn ghost" onclick="closeModal()">Cancelar</button>
      <button class="btn gold" id="tnOk">✓ Hecha</button></div>`);
  m.querySelector('#tnOk').onclick = () => accionTarea('terminar', id, m.querySelector('#tnNota').value);
}

RENDER.tareas = async () => {
  const sec = $('#tab-mias');
  sec.innerHTML = '<div class="card muted">Cargando…</div>';
  let html = '';
  if (ME.puesto) {
    try {
      const j = await api('/api/compras/tareas');
      actualizarBadgeTar(j.bolsa.length);
      html += `<div class="card"><h3 style="margin-bottom:6px">¿Tienes tiempo libre?</h3>
        <p class="muted" style="font-size:13px;margin-bottom:10px">${esc(PUESTOS_LABEL[ME.puesto])} · hoy llevas ${j.hechas_hoy} tarea(s) hecha(s)</p>
        <div class="flex wrap">${[15, 30, 60].map(n => `<button class="btn gold" data-libre="${n}">${n} min</button>`).join('')}</div></div>`;
      if (j.mias.length) {
        html += `<h3 style="margin:16px 0 8px">En curso</h3>` + j.mias.map(t => tarjetaTarea(t,
          `<button class="btn gold sm" data-hecha="${t.id}">✓ Hecha</button>
           <button class="btn ghost sm" data-soltar="${t.id}">Devolver a la bolsa</button>`)).join('');
      }
      html += `<h3 style="margin:16px 0 8px">Bolsa de tareas (${j.bolsa.length})</h3>` + (j.bolsa.length
        ? j.bolsa.map(t => tarjetaTarea(t,
          `<button class="btn sm" data-tomar="${t.id}">Tomar</button>
           <button class="btn ghost sm" data-hecha="${t.id}">✓ Ya la hice</button>`)).join('')
        : '<div class="card empty muted">No hay tareas pendientes para tu puesto. 🎉</div>');
    } catch (e) { html += `<div class="card">${esc(e.message)}</div>`; }
  }
  sec.innerHTML = html || `<div class="card muted">Tu usuario no tiene un puesto asignado. ${puede('tareas_admin') ? 'Asígnalo en la pestaña Personas.' : 'Pídele a un administrador que te lo asigne.'}</div>`;
  $$('#tab-mias [data-tomar]').forEach(b => b.onclick = () => accionTarea('tomar', +b.dataset.tomar));
  $$('#tab-mias [data-soltar]').forEach(b => b.onclick = () => accionTarea('soltar', +b.dataset.soltar));
  $$('#tab-mias [data-hecha]').forEach(b => b.onclick = () => terminarConNota(+b.dataset.hecha));
  $$('#tab-mias [data-libre]').forEach(b => b.onclick = async () => {
    try {
      const j = await api('/api/compras/tareas/sugerir', { method: 'POST', body: { minutos: +b.dataset.libre } });
      if (!j.tarea) return toast('No hay ninguna tarea que quepa en ese tiempo', '');
      const m = modal(`<h3>Te sugiero esta</h3>${tarjetaTarea(j.tarea,
        `<button class="btn gold" id="sgTomar">Tomar</button>
         <button class="btn ghost" onclick="closeModal()">Ahora no</button>`)}`);
      m.querySelector('#sgTomar').onclick = () => accionTarea('tomar', j.tarea.id);
    } catch (e) { toast(e.message, 'err'); }
  });
};

async function pintarTareasAdmin() {
  const box = $('#tarAdmin'); if (!box) return;
  let j;
  try { j = await api('/api/compras/tareas/admin'); } catch (e) { box.innerHTML = `<div class="card">${esc(e.message)}</div>`; return; }
  const P = j.puestos, cfg = j.config, prop = j.propuesta, est = j.estimacion;
  const optsGente = (puesto, sel) => '<option value="">Cualquiera del puesto</option>' + j.equipo
    .filter(u => u.puesto === puesto).map(u => `<option value="${u.id}" ${sel == u.id ? 'selected' : ''}>${esc(u.nombre)}</option>`).join('');
  const ESTADO_PROP = { armada: 'armada, sin enviar', enviada: 'enviada al Dr., esperando aprobación',
    aprobada: 'aprobada ✓', auto: 'publicada sin aprobación' };

  let propHtml = `<div class="card"><div class="flex wrap"><h3>Propuesta de hoy</h3><div class="spacer"></div>
      <button class="btn ghost sm" id="tpGen">↻ Generar / rehacer</button>
      ${cfg.activo ? '<button class="btn ghost sm" id="tpEnv">✉️ Generar y enviar al Dr.</button>' : ''}</div>
    <p class="muted" style="font-size:13px;margin:6px 0 10px">Tiempo libre estimado hoy (horario + agenda):
      ${Object.entries(est.por_puesto).map(([k, v]) => `<b>${esc(P[k])}</b> ${horas(v)}`).join(' · ')}
      ${est.sin_agenda ? '<br>⚠️ No hay agenda de hoy en la base: se supuso a los tres ortodoncistas atendiendo.' : ''}
      ${est.admin_vial ? '<br>Hoy hay bloque admin del Dr. Vial.' : ''}</p>`;
  if (!prop) propHtml += '<p class="muted">Todavía no se arma la de hoy.</p>';
  else {
    propHtml += `<p style="margin-bottom:8px">Estado: <b>${ESTADO_PROP[prop.estado] || prop.estado}</b></p>`;
    const abierta = !['aprobada', 'auto'].includes(prop.estado);
    propHtml += prop.tareas.length ? `<div class="tablewrap"><table id="tpTabla"><tr><th>Puesto</th><th>Tarea</th><th class="num">Min</th><th>Para</th>${abierta ? '<th>Quitar</th>' : ''}</tr>` +
      prop.tareas.map(t => `<tr><td>${esc(P[t.puesto])}</td><td>${esc(t.titulo)}</td><td class="num">${t.minutos}</td>
        <td>${abierta ? `<select data-asig="${t.id}">${optsGente(t.puesto, t.asignada_a)}</select>` : esc(t.asignada_nombre || 'cualquiera')}</td>
        ${abierta ? `<td><input type="checkbox" data-quitar="${t.id}"></td>` : ''}</tr>`).join('') + '</table></div>'
      : '<p class="muted">No hay tareas pendientes que proponer.</p>';
    if (abierta) propHtml += '<div class="flex" style="margin-top:10px"><div class="spacer"></div><button class="btn gold" id="tpAprobar">✅ Aprobar y publicar</button></div>';
  }
  propHtml += '</div>';

  const encHtml = `<div class="card"><h3>Nuevo encargo</h3>
    <div class="field"><label>Qué hay que hacer</label><input id="teTit" placeholder="Ej: llamar al técnico del sillón 3"></div>
    <div class="field"><label>Detalle (opcional)</label><textarea id="teDet" rows="2"></textarea></div>
    <div class="row c3"><div class="field"><label>Puesto</label><select id="tePu">${Object.entries(P).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('')}</select></div>
      <div class="field"><label>Para</label><select id="teAs"></select></div>
      <div class="field"><label>Minutos aprox.</label><input id="teMin" type="number" value="15" min="1"></div></div>
    <div class="row c2"><div class="field"><label>Prioridad</label><select id="tePr"><option value="1">Alta</option><option value="2" selected>Normal</option><option value="3">Baja</option></select></div>
      <div class="field"><label>Hasta (opcional)</label><input id="teVen" type="date"></div></div>
    <div class="flex"><div class="spacer"></div><button class="btn gold" id="teOk">Agregar a la bolsa</button></div></div>`;

  const plHtml = `<div class="card"><div class="flex"><h3>Tareas que se repiten</h3><div class="spacer"></div>
      <button class="btn ghost sm" id="tplCat" title="Agrega las ~25 tareas sugeridas por área (no duplica)">📋 Cargar catálogo sugerido</button>
      <button class="btn ghost sm" id="tplNueva">➕ Nueva</button></div>
    ${j.plantillas.length ? `<div class="tablewrap"><table><tr><th>Tarea</th><th>Puesto</th><th>Cada</th><th class="num">Min</th><th>Estado</th><th></th></tr>` +
      j.plantillas.map((p, i) => `<tr><td>${esc(p.titulo)}${p.admin_vial ? ' <span class="pill">admin Vial</span>' : ''}</td><td>${esc(P[p.puesto])}</td>
        <td>${FREC_LABEL[p.frecuencia]}${p.frecuencia === 'mensual' ? ' (día ' + p.dia_mes + ')' : (p.dias ? ' · ' + p.dias.split(',').map(d => (DIAS_SEM.find(x => x[0] == d) || [0, d])[1]).join(' ') : '')}</td>
        <td class="num">${p.minutos}</td><td>${p.activa ? '<span class="pill ok">activa</span>' : '<span class="pill low">pausada</span>'}</td>
        <td><button class="btn ghost sm" data-tpl="${i}">Editar</button></td></tr>`).join('') + '</table></div>'
      : '<p class="muted">Aún no hay tareas fijas. Agrega las de cada área.</p>'}</div>`;

  const abiertas = j.tareas.filter(t => ['pendiente', 'tomada'].includes(t.estado));
  const hechas = j.tareas.filter(t => t.estado === 'hecha').slice(0, 30);
  const listaHtml = `<div class="card"><h3>Abiertas (${abiertas.length})</h3>
    ${abiertas.length ? `<div class="tablewrap"><table><tr><th>Tarea</th><th>Puesto</th><th>Estado</th><th></th></tr>` + abiertas.map(t =>
      `<tr><td>${esc(t.titulo)}<br><span class="muted" style="font-size:12px">${ORIGEN_LABEL[t.origen] || ''} · ${esc(t.fecha)}${t.publicada ? '' : ' · aún no publicada'}</span></td>
       <td>${esc(P[t.puesto])}</td><td>${t.estado === 'tomada' ? 'la tiene ' + esc(t.tomada_nombre || '') : 'en la bolsa'}</td>
       <td><button class="btn ghost sm" data-desc="${t.id}">Descartar</button></td></tr>`).join('') + '</table></div>' : '<p class="muted">Nada abierto.</p>'}
    <h3 style="margin-top:14px">Últimas hechas</h3>
    ${hechas.length ? `<div class="tablewrap"><table><tr><th>Tarea</th><th>Quién</th><th>Cuándo</th><th>Nota</th></tr>` + hechas.map(t =>
      `<tr><td>${esc(t.titulo)}</td><td>${esc(t.hecha_nombre || '')}</td><td>${esc((t.hecha_en || '').replace('T', ' ').slice(0, 16))}</td><td>${esc(t.nota || '')}</td></tr>`).join('') + '</table></div>' : '<p class="muted">Todavía ninguna.</p>'}
    <div class="flex" style="margin-top:10px"><div class="spacer"></div><button class="btn ghost sm" id="tResumen">📊 Resumen de esta semana</button></div></div>`;

  const optsStock = '<option value="">— nadie en especial —</option>' + j.equipo.filter(u => u.puesto === 'asistente_orto')
    .map(u => `<option value="${u.id}" ${cfg.preferido_stock == u.id ? 'selected' : ''}>${esc(u.nombre)}</option>`).join('');
  const cfgHtml = `<div class="card"><h3>Configuración</h3>
    <label class="flex" style="margin:8px 0"><input type="checkbox" id="tcAct" ${cfg.activo ? 'checked' : ''}>
      <span>Mandarme la propuesta cada mañana para aprobar <span class="muted">(si está apagado, las tareas se publican solas)</span></span></label>
    <label class="flex" style="margin:8px 0"><input type="checkbox" id="tcWa" ${cfg.whatsapp ? 'checked' : ''}><span>Avisarme también por WhatsApp</span></label>
    <label class="flex" style="margin:8px 0"><input type="checkbox" id="tcPub" ${cfg.publicar_sin_aprobar ? 'checked' : ''}><span>Si no apruebo a tiempo, publicar igual</span></label>
    <div class="row c3"><div class="field"><label>Hora de la propuesta</label><input id="tcHp" type="time" value="${esc(cfg.hora_propuesta)}"></div>
      <div class="field"><label>Publicar sin aprobación a las</label><input id="tcHpub" type="time" value="${esc(cfg.hora_publicar)}"></div>
      <div class="field"><label>Llenar hasta (% del libre)</label><input id="tcPct" type="number" min="10" max="100" value="${cfg.llenado_pct}"></div></div>
    <div class="field"><label>Tareas de stock y etiquetas, de preferencia para</label><select id="tcStock">${optsStock}</select></div>
    <p class="muted" style="font-size:12px">La dotación por día y bloque sale del horario (abril 2026). Para cambiarla, avisar a quien mantiene el sistema.</p>
    <div class="flex"><div class="spacer"></div><button class="btn gold" id="tcOk">Guardar</button></div></div>`;

  box.innerHTML = propHtml + encHtml + plHtml + listaHtml + cfgHtml;

  const pintarAsig = () => { $('#teAs').innerHTML = optsGente($('#tePu').value, ''); };
  $('#tePu').onchange = pintarAsig; pintarAsig();
  $('#teOk').onclick = async () => {
    try {
      await api('/api/compras/tareas/encargo', { method: 'POST', body: {
        titulo: $('#teTit').value, detalle: $('#teDet').value, puesto: $('#tePu').value,
        asignada_a: $('#teAs').value || null, minutos: +$('#teMin').value, prioridad: +$('#tePr').value,
        vence: $('#teVen').value } });
      toast('Encargo agregado ✓', 'ok'); refrescar();
    } catch (e) { toast(e.message, 'err'); }
  };
  const correr = async enviar => {
    try { const r = await api('/api/compras/tareas/propuesta/run', { method: 'POST', body: { enviar } });
      toast(`Propuesta: ${r.n_propuestas} tarea(s) · ${ESTADO_PROP[r.estado] || r.estado}` +
        (r.correo && !r.correo.ok ? ' · el correo no salió: ' + r.correo.error : ''), r.correo && !r.correo.ok ? 'err' : 'ok');
      refrescar();
    } catch (e) { toast(e.message, 'err'); }
  };
  $('#tpGen').onclick = () => correr(false);
  if ($('#tpEnv')) $('#tpEnv').onclick = () => correr(true);
  if ($('#tpAprobar')) $('#tpAprobar').onclick = async () => {
    const cambios = [];
    $$('#tpTabla [data-asig]').forEach(s => cambios.push({ id: +s.dataset.asig, asignada_a: s.value ? +s.value : null }));
    $$('#tpTabla [data-quitar]').forEach(c => { if (c.checked) cambios.push({ id: +c.dataset.quitar, quitar: true }); });
    try { await api('/api/compras/tareas/aprobar', { method: 'POST', body: { cambios } });
      toast('Propuesta aprobada y publicada ✓', 'ok'); refrescar(); } catch (e) { toast(e.message, 'err'); }
  };
  $$('#tarAdmin [data-desc]').forEach(b => b.onclick = async () => {
    try { await api('/api/compras/tareas/descartar', { method: 'POST', body: { id: +b.dataset.desc } });
      toast('Descartada', 'ok'); refrescar(); } catch (e) { toast(e.message, 'err'); }
  });
  $('#tplNueva').onclick = () => editarPlantilla(null, P, j.equipo);
  $('#tplCat').onclick = async () => {
    try { const r = await api('/api/compras/tareas/plantillas/catalogo', { method: 'POST' });
      toast(r.creadas ? `${r.creadas} tarea(s) agregada(s) ✓ — revísalas y ajusta` : 'El catálogo ya estaba cargado', 'ok');
      refrescar(); } catch (e) { toast(e.message, 'err'); }
  };
  $$('#tarAdmin [data-tpl]').forEach(b => b.onclick = () => editarPlantilla(j.plantillas[+b.dataset.tpl], P, j.equipo));
  $('#tResumen').onclick = async () => {
    try { const r = (await api('/api/compras/tareas/resumen')).resumen;
      modal(`<h3>Resumen ${esc(r.desde)} a ${esc(r.hasta)}</h3>
        <table><tr><th>Persona</th><th class="num">Hechas</th><th class="num">Tiempo</th></tr>${r.personas.map(p =>
          `<tr><td>${esc(p.nombre)} <span class="muted">${esc(P[p.puesto] || '')}</span></td><td class="num">${p.hechas}</td><td class="num">${horas(p.minutos)}</td></tr>`).join('')}</table>
        <p class="muted" style="font-size:13px;margin-top:10px">${Object.keys(P).map(k =>
          `${esc(P[k])}: libre ~${horas(r.libre_estimado[k] || 0)}, en tareas ${horas(r.minutos_hechos[k] || 0)}, vencidas ${r.vencidas[k] || 0}`).join('<br>')}</p>
        <div class="flex"><div class="spacer"></div><button class="btn ghost" onclick="closeModal()">Cerrar</button></div>`);
    } catch (e) { toast(e.message, 'err'); }
  };
  $('#tcOk').onclick = async () => {
    try { await api('/api/compras/tareas/config', { method: 'POST', body: {
        activo: $('#tcAct').checked, whatsapp: $('#tcWa').checked, publicar_sin_aprobar: $('#tcPub').checked,
        hora_propuesta: $('#tcHp').value, hora_publicar: $('#tcHpub').value, llenado_pct: +$('#tcPct').value,
        preferido_stock: $('#tcStock').value ? +$('#tcStock').value : null } });
      toast('Configuración guardada ✓', 'ok'); refrescar(); } catch (e) { toast(e.message, 'err'); }
  };
}

function editarPlantilla(p, P, equipo) {
  const v = p || { titulo: '', detalle: '', puesto: 'asistente_orto', minutos: 15, prioridad: 2,
    frecuencia: 'semanal', dias: '', dia_mes: 1, admin_vial: 0, activa: 1, preferido_id: null };
  const dias = (v.dias || '').split(',').filter(Boolean).map(Number);
  const m = modal(`<h3>${p ? 'Editar' : 'Nueva'} tarea que se repite</h3>
    <div class="field"><label>Qué hay que hacer</label><input id="tpTit" value="${esc(v.titulo)}"></div>
    <div class="field"><label>Detalle / cómo hacerla (opcional)</label><textarea id="tpDet" rows="2">${esc(v.detalle || '')}</textarea></div>
    <div class="row c3"><div class="field"><label>Puesto</label><select id="tpPu">${Object.entries(P).map(([k, l]) => `<option value="${k}" ${v.puesto === k ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></div>
      <div class="field"><label>Minutos aprox.</label><input id="tpMin" type="number" min="1" value="${v.minutos}"></div>
      <div class="field"><label>Prioridad</label><select id="tpPr">${[1, 2, 3].map(n => `<option value="${n}" ${v.prioridad == n ? 'selected' : ''}>${PRIO[n][0]}</option>`).join('')}</select></div></div>
    <div class="row c2"><div class="field"><label>Cada cuánto</label><select id="tpFr">${Object.entries(FREC_LABEL).map(([k, l]) => `<option value="${k}" ${v.frecuencia === k ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
      <div class="field" id="tpDiaMesBox"><label>Día del mes</label><input id="tpDm" type="number" min="1" max="28" value="${v.dia_mes}"></div></div>
    <div class="field" id="tpDiasBox"><label>Días <span class="muted">(semanal: nace el primero marcado; diaria: solo esos días; vacío = todos)</span></label>
      <div class="flex wrap">${DIAS_SEM.map(([n, l]) => `<label class="flex" style="gap:4px"><input type="checkbox" data-dia="${n}" ${dias.includes(n) ? 'checked' : ''}>${l}</label>`).join('')}</div></div>
    <div class="field"><label>De preferencia para</label><select id="tpPref"></select></div>
    <label class="flex" style="margin:6px 0"><input type="checkbox" id="tpAv" ${v.admin_vial ? 'checked' : ''}><span>Solo en el bloque admin del Dr. Vial (martes AM)</span></label>
    ${p ? `<label class="flex" style="margin:6px 0"><input type="checkbox" id="tpAct" ${v.activa ? 'checked' : ''}><span>Activa</span></label>` : ''}
    <div class="flex" style="margin-top:6px"><div class="spacer"></div>
      <button class="btn ghost" onclick="closeModal()">Cancelar</button>
      <button class="btn gold" id="tpOk">Guardar</button></div>`);
  const sync = () => {
    const fr = m.querySelector('#tpFr').value;
    m.querySelector('#tpDiaMesBox').classList.toggle('hidden', fr !== 'mensual');
    m.querySelector('#tpDiasBox').classList.toggle('hidden', fr === 'mensual');
    const pu = m.querySelector('#tpPu').value;
    m.querySelector('#tpPref').innerHTML = '<option value="">Cualquiera del puesto</option>' + equipo.filter(u => u.puesto === pu)
      .map(u => `<option value="${u.id}" ${v.preferido_id == u.id ? 'selected' : ''}>${esc(u.nombre)}</option>`).join('');
  };
  m.querySelector('#tpFr').onchange = sync; m.querySelector('#tpPu').onchange = sync; sync();
  m.querySelector('#tpOk').onclick = async () => {
    const body = { titulo: m.querySelector('#tpTit').value, detalle: m.querySelector('#tpDet').value,
      puesto: m.querySelector('#tpPu').value, minutos: +m.querySelector('#tpMin').value,
      prioridad: +m.querySelector('#tpPr').value, frecuencia: m.querySelector('#tpFr').value,
      dia_mes: +m.querySelector('#tpDm').value, admin_vial: m.querySelector('#tpAv').checked,
      dias: $$('[data-dia]', m).filter(c => c.checked).map(c => +c.dataset.dia),
      preferido_id: m.querySelector('#tpPref').value ? +m.querySelector('#tpPref').value : null };
    if (p) { body.id = p.id; body.activa = m.querySelector('#tpAct').checked; }
    try { await api('/api/compras/tareas/plantillas' + (p ? '/actualizar' : ''), { method: 'POST', body });
      closeModal(); toast('Guardada ✓', 'ok'); refrescar(); } catch (e) { toast(e.message, 'err'); }
  };
}


RENDER.mias = () => RENDER.tareas();
RENDER.admin = () => pintarTareasAdmin();

/* ══════════════════ PERSONAS ══════════════════ */
// Son los MISMOS usuarios de Compras, pero el acceso a Tareas se asigna aparte: alguien
// puede ser Inventario en Compras y solo Personal en Tareas. Una persona creada acá queda
// SIN acceso a Compras; dárselo es cosa de un administrador de Compras.
const ROLES_TAREAS_LABEL = { '': 'Sin acceso', personal: 'Personal', admin: 'Administrador' };
const optsRolTareas = (sel, conVacio = true) => Object.entries(ROLES_TAREAS_LABEL)
  .filter(([v]) => conVacio || v)
  .map(([v, l]) => `<option value="${v}" ${(sel || '') === v ? 'selected' : ''}>${l}</option>`).join('');

RENDER.personas = async () => {
  const sec = $('#tab-personas');
  sec.innerHTML = '<div class="card muted">Cargando…</div>';
  let ps;
  try { ps = (await api('/api/compras/tareas/personas')).personas; } catch (e) { sec.innerHTML = `<div class="card">${esc(e.message)}</div>`; return; }
  const con = ps.filter(p => p.rol_tareas), sin = ps.filter(p => !p.rol_tareas && p.activo);
  sec.innerHTML = `<div class="card"><div class="flex"><h3>Personas con acceso a Tareas</h3><div class="spacer"></div>
      <button class="btn gold sm" id="peNueva">➕ Nueva persona</button></div>
    <p class="muted" style="font-size:13px;margin-bottom:10px">Mismo usuario y clave que Compras, pero el acceso a cada sitio se asigna por separado.</p>
    ${con.length ? `<div class="tablewrap"><table><tr><th>Nombre</th><th>Usuario</th><th>Tareas</th><th>Puesto</th><th>Compras</th><th></th></tr>` +
      con.map(p => `<tr><td>${esc(p.nombre)}${p.activo ? '' : ' <span class="pill low">inactiva</span>'}</td><td>${esc(p.email)}</td>
        <td>${esc(ROLES_TAREAS_LABEL[p.rol_tareas])}</td><td>${p.puesto ? esc(PUESTOS_LABEL[p.puesto]) : '<span class="muted">—</span>'}</td>
        <td class="muted">${p.compras ? esc(p.rol_compras) : 'no'}</td>
        <td style="white-space:nowrap"><button class="btn ghost sm" data-ed="${p.id}">Editar</button>
            <button class="btn ghost sm" data-cl="${p.id}" title="Cambiar contraseña">🔑</button></td></tr>`).join('') + '</table></div>'
      : '<p class="muted">Todavía nadie tiene acceso.</p>'}</div>
    ${sin.length ? `<div class="card"><h3>Usuarios de Compras sin acceso a Tareas</h3>
      <p class="muted" style="font-size:13px;margin-bottom:10px">Si alguno también hace tareas (por ejemplo, quien lleva el inventario), dale acceso y su puesto.</p>
      <table>${sin.map(p => `<tr><td>${esc(p.nombre)}</td><td class="muted">${esc(p.rol_compras)}</td>
        <td style="text-align:right"><button class="btn ghost sm" data-ed="${p.id}">Dar acceso</button></td></tr>`).join('')}</table></div>` : ''}`;
  const porId = Object.fromEntries(ps.map(p => [p.id, p]));
  $('#peNueva').onclick = nuevaPersona;
  $$('#tab-personas [data-ed]').forEach(b => b.onclick = () => editarPersona(porId[+b.dataset.ed]));
  $$('#tab-personas [data-cl]').forEach(b => b.onclick = () => resetClave(porId[+b.dataset.cl]));
};

function nuevaPersona() {
  const m = modal(`<h3>Nueva persona</h3>
    <div class="field"><label>Nombre</label><input id="npN" placeholder="Nombre y apellido"></div>
    <div class="field"><label>Usuario para entrar</label><input id="npE" placeholder="Ej: maria (sin espacios)"></div>
    <div class="field"><label>Contraseña</label><input id="npP" type="password" placeholder="Mínimo 6 caracteres"></div>
    <div class="row c2"><div class="field"><label>Puesto</label><select id="npPu">${optsPuesto('asistente_orto', false)}</select></div>
      <div class="field"><label>Acceso a Tareas</label><select id="npRt">${optsRolTareas('personal', false)}</select></div></div>
    <p class="muted" style="font-size:12px">Queda sin acceso a Compras.</p>
    <div class="flex" style="margin-top:8px"><div class="spacer"></div>
      <button class="btn ghost" onclick="closeModal()">Cancelar</button>
      <button class="btn gold" id="npOk">Crear</button></div>`);
  m.querySelector('#npOk').onclick = async () => {
    try { await api('/api/compras/tareas/personas', { method: 'POST', body: { nombre: m.querySelector('#npN').value,
        email: m.querySelector('#npE').value, password: m.querySelector('#npP').value,
        puesto: m.querySelector('#npPu').value, rol_tareas: m.querySelector('#npRt').value } });
      closeModal(); toast('Persona creada ✓ — avísale su usuario y clave', 'ok'); refrescar();
    } catch (e) { toast(e.message, 'err'); }
  };
}

function editarPersona(p) {
  const m = modal(`<h3>${esc(p.nombre)}</h3>
    <div class="row c2"><div class="field"><label>Acceso a Tareas</label><select id="epRt">${optsRolTareas(p.rol_tareas || 'personal')}</select></div>
      <div class="field"><label>Puesto</label><select id="epPu">${optsPuesto(p.puesto)}</select></div></div>
    <p class="muted" style="font-size:12px">"Personal" ve las tareas de su puesto. "Administrador" además arma la propuesta, las tareas fijas y las personas.
      ${p.compras ? `En Compras es <b>${esc(p.rol_compras)}</b>; eso se cambia desde Compras.` : ''}</p>
    <div class="flex" style="margin-top:8px"><div class="spacer"></div>
      <button class="btn ghost" onclick="closeModal()">Cancelar</button>
      <button class="btn gold" id="epOk">Guardar</button></div>`);
  m.querySelector('#epOk').onclick = async () => {
    try { await api('/api/compras/tareas/personas/actualizar', { method: 'POST', body: { id: p.id,
        puesto: m.querySelector('#epPu').value, rol_tareas: m.querySelector('#epRt').value } });
      closeModal(); toast('Guardado ✓', 'ok'); refrescar();
    } catch (e) { toast(e.message, 'err'); }
  };
}

function resetClave(p) {
  const m = modal(`<h3>Nueva contraseña para ${esc(p.nombre)}</h3>
    <div class="field"><label>Contraseña</label><input id="rcP" type="password" placeholder="Mínimo 6 caracteres"></div>
    <p class="muted" style="font-size:12px">Es la misma clave de Compras. Se cierran sus sesiones abiertas.</p>
    <div class="flex" style="margin-top:8px"><div class="spacer"></div>
      <button class="btn ghost" onclick="closeModal()">Cancelar</button>
      <button class="btn gold" id="rcOk">Guardar</button></div>`);
  m.querySelector('#rcOk').onclick = async () => {
    try { await api('/api/compras/tareas/personas/actualizar', { method: 'POST', body: { id: p.id, password: m.querySelector('#rcP').value } });
      closeModal(); toast('Contraseña cambiada ✓', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  };
}

boot();
