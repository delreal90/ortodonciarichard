/* Compras y Stock — Ortodoncia Richard
   SPA vanilla. Servida por el backend Flask en /compras, así que la API es del
   mismo origen (sin CORS). Se puede forzar otro backend con localStorage.compras_api. */
'use strict';

const API = (localStorage.getItem('compras_api') || '').replace(/\/$/, '');
let TOKEN = localStorage.getItem('compras_token') || '';
let ME = null;
const CACHE = { categorias: [], proveedores: [], productos: [] };

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const clp = n => '$' + Math.round(Number(n) || 0).toLocaleString('es-CL');
const money = (n, moneda) => moneda === 'USD'
  ? 'US$' + (Number(n) || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  : clp(n);
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
// Permisos por CAPACIDAD (ME.caps viene del backend según el rol).
const puede = cap => (ME?.caps || []).includes(cap);
const ROLES_LABEL = {
  admin: 'Administrador (todo)',
  registro: 'Registro (ingresa compras y stock)',
  solicitante: 'Solicitante (ve, escanea, pide compras)',
  lectura: 'Lectura (solo ver)',
  escaner: 'Escáner (solo escanear salidas)',
};
const ROLES_ORDEN = ['registro', 'solicitante', 'escaner', 'lectura', 'admin'];
const optsRoles = sel => ROLES_ORDEN.map(r =>
  `<option value="${r}" ${sel === r ? 'selected' : ''}>${ROLES_LABEL[r]}</option>`).join('');

// ── API ────────────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const o = { headers: {}, ...opts };
  o.headers['X-Compras-Token'] = TOKEN;
  if (o.body && !(o.body instanceof FormData)) {
    o.headers['Content-Type'] = 'application/json';
    o.body = JSON.stringify(o.body);
  }
  const r = await fetch(API + path, o);
  if (r.status === 401 && ME) { logout(); throw new Error('Sesión expirada'); }
  const ct = r.headers.get('content-type') || '';
  if (!ct.includes('application/json')) {
    if (!r.ok) throw new Error('Error ' + r.status);
    return r;
  }
  const j = await r.json();
  if (!r.ok || j.ok === false) throw new Error(j.error || 'Error ' + r.status);
  return j;
}

// ── UI helpers ───────────────────────────────────────────────────────────────
function toast(msg, tipo = '') {
  const t = document.createElement('div');
  t.className = 'toast ' + tipo;
  t.textContent = msg;
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

// ── Auth ─────────────────────────────────────────────────────────────────────
async function boot() {
  try {
    const j = await api('/api/compras/me');
    ME = j.usuario;
    entrarApp();
  } catch {
    let configurado = true;
    try { const r = await fetch(API + '/api/compras/me'); const jj = await r.json(); configurado = jj.configurado !== false; }
    catch { configurado = true; }
    mostrarAuth(configurado);
  }
}

function mostrarAuth(configurado) {
  $('#app').classList.add('hidden');
  $('#auth').classList.remove('hidden');
  const setup = !configurado;
  $('#authTitle').textContent = setup ? 'Configuración inicial' : 'Compras y Stock';
  $('#authSub').textContent = setup ? 'Crea la primera cuenta de administrador.' : 'Ingresa con tu cuenta.';
  $('#setupNombre').classList.toggle('hidden', !setup);
  $('#btnAuth').textContent = setup ? 'Crear administrador' : 'Entrar';
  $('#authHint').textContent = setup ? 'Esta cuenta podrá crear más usuarios después.' : '';
  $('#btnAuth').onclick = () => setup ? doSetup() : doLogin();
  $('#inPass').onkeydown = e => { if (e.key === 'Enter') $('#btnAuth').click(); };
}

async function doLogin() {
  try {
    const j = await api('/api/compras/login', { method: 'POST', body: { email: $('#inEmail').value, password: $('#inPass').value } });
    TOKEN = j.token; localStorage.setItem('compras_token', TOKEN); ME = j.usuario; entrarApp();
  } catch (e) { toast(e.message, 'err'); }
}
async function doSetup() {
  try {
    const j = await api('/api/compras/setup', { method: 'POST', body: { nombre: $('#inNombre').value, email: $('#inEmail').value, password: $('#inPass').value } });
    TOKEN = j.token; localStorage.setItem('compras_token', TOKEN); ME = j.usuario; entrarApp();
  } catch (e) { toast(e.message, 'err'); }
}
function logout() {
  api('/api/compras/logout', { method: 'POST' }).catch(() => {});
  TOKEN = ''; ME = null; localStorage.removeItem('compras_token');
  $('#app').classList.add('hidden'); mostrarAuth(true);
}

// Qué capacidad exige cada pestaña.
const TAB_CAP = { compras: 'registrar', historial: 'compras_ver', stock: 'stock',
  escanear: 'escanear', solicitudes: 'solicitar', recurrentes: 'registrar',
  reportes: 'reportes', admin: 'admin' };

async function entrarApp() {
  $('#auth').classList.add('hidden');
  $('#app').classList.remove('hidden');
  $('#uName').textContent = ME.nombre;
  $('#uRole').textContent = ME.rol;
  $('#btnLogout').onclick = logout;
  // mostrar solo las pestañas permitidas por el rol
  $$('#tabs button').forEach(b => b.classList.toggle('hidden', !puede(TAB_CAP[b.dataset.tab])));
  actualizarBadge(ME.pendientes || 0);
  await recargarCaches();
  // abrir la primera pestaña visible (un escáner-solo abre directo en Escanear)
  const primera = $$('#tabs button').find(b => !b.classList.contains('hidden'));
  irTab(primera ? primera.dataset.tab : 'escanear');
}

function actualizarBadge(n) {
  const b = $('#badgePend'); if (!b) return;
  b.textContent = n;
  b.classList.toggle('hidden', !(n > 0 && puede('solicitar')));
}
async function refrescarPendientesBadge() {
  if (!puede('solicitar')) return;
  try { actualizarBadge((await api('/api/compras/solicitudes')).pendientes.length); } catch {}
}

async function recargarCaches() {
  // Cada caché se carga solo si el rol tiene permiso; si falla, queda vacío (ej. un
  // usuario "escáner" no accede a categorías/proveedores y no debe romper).
  const safe = async (path, field) => { try { return (await api(path))[field]; } catch { return []; } };
  const [c, p, pr] = await Promise.all([
    safe('/api/compras/categorias', 'categorias'),
    safe('/api/compras/proveedores', 'proveedores'),
    safe('/api/compras/productos?detalle=1', 'productos')]);
  CACHE.categorias = c; CACHE.proveedores = p; CACHE.productos = pr;
}

// ── Tabs ─────────────────────────────────────────────────────────────────────
const RENDER = {};
function irTab(tab) {
  $$('#tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  $$('main > section').forEach(s => s.classList.toggle('hidden', s.id !== 'tab-' + tab));
  (RENDER[tab] || (() => {}))();
}
$('#tabs').addEventListener('click', e => { const b = e.target.closest('button'); if (b) irTab(b.dataset.tab); });

/* ══════════════════ BUSCADOR reutilizable ══════════════════ */
// Crea un buscador con dropdown. onPick(item), onCrear(texto)->item|null (opcional).
function buscador({ items, placeholder, onPick, onCrear, labelKey = 'nombre', valorInicial = '' }) {
  const wrap = document.createElement('div');
  wrap.className = 'search';
  wrap.innerHTML = `<input placeholder="${esc(placeholder)}" value="${esc(valorInicial)}"><div class="results hidden"></div>`;
  const inp = wrap.querySelector('input'), res = wrap.querySelector('.results');
  const cerrar = () => res.classList.add('hidden');
  const pintar = () => {
    const q = inp.value.trim().toLowerCase();
    const match = items.filter(i => (i[labelKey] || '').toLowerCase().includes(q)).slice(0, 8);
    let html = match.map(i => `<div data-id="${i.id}">${esc(i[labelKey])}${i.rut ? ' · ' + esc(i.rut) : ''}${i.stock_actual != null ? ` · stock ${i.stock_actual}` : ''}</div>`).join('');
    if (onCrear && q) html += `<div class="add" data-crear="1">➕ Crear "${esc(inp.value.trim())}"</div>`;
    res.innerHTML = html || '<div class="muted" style="padding:9px 12px">Sin resultados</div>';
    res.classList.remove('hidden');
  };
  inp.addEventListener('focus', pintar);
  inp.addEventListener('input', pintar);
  inp.addEventListener('blur', () => setTimeout(cerrar, 180));
  res.addEventListener('click', async e => {
    const d = e.target.closest('div'); if (!d) return;
    if (d.dataset.crear) {
      const item = await onCrear(inp.value.trim());
      if (item) { inp.value = item[labelKey]; onPick(item); cerrar(); }
      return;
    }
    if (d.dataset.id) {
      const item = items.find(i => String(i.id) === d.dataset.id);
      inp.value = item[labelKey]; onPick(item); cerrar();
    }
  });
  return { wrap, input: inp, reset: () => { inp.value = ''; } };
}

/* ══════════════════ TAB: NUEVA COMPRA ══════════════════ */
let compraItems = [];   // {producto_id, producto_nombre, unidad, cantidad, precio_unitario}
let compraProvId = null, compraFoto = null;

RENDER.compras = () => {
  if (!puede('registrar')) { $('#tab-compras').innerHTML = soloLectura(); return; }
  compraItems = []; compraProvId = null; compraFoto = null;
  const s = $('#tab-compras');
  const hoy = new Date().toISOString().slice(0, 10);
  s.innerHTML = `
    <div class="card">
      <h2>Nueva compra</h2>
      <div class="sub">Ingresa una factura o boleta con uno o varios productos.</div>
      <div class="row c2">
        <div class="field"><label>Fecha de compra</label><input type="date" id="cFecha" value="${hoy}"></div>
        <div class="field"><label>Proveedor</label><div id="cProvSlot"></div></div>
      </div>
      <div class="row c3">
        <div class="field"><label>Tipo de documento</label>
          <select id="cTipoDoc"><option value="factura">Factura</option><option value="boleta">Boleta</option><option value="otro">Otro</option></select></div>
        <div class="field"><label>N° documento</label><input id="cNroDoc" placeholder="Ej: 12345"></div>
        <div class="field"><label>Forma de pago</label>
          <select id="cPago"><option value="transferencia">Transferencia</option><option value="efectivo">Efectivo</option><option value="debito">Débito</option><option value="credito">Crédito</option><option value="cheque">Cheque</option><option value="otro">Otro</option></select></div>
      </div>
      <div class="row c3">
        <div class="field"><label>Tipo de gasto</label>
          <select id="cTipoGasto"><option value="variable">Variable</option><option value="fijo">Fijo</option><option value="recurrente">Recurrente (mensual)</option></select></div>
        <div class="field"><label>Categoría</label><select id="cCategoria"></select></div>
        <div class="field"><label>Foto factura/boleta (opcional)</label>
          <input type="file" id="cFoto" accept="image/*,application/pdf" capture="environment"></div>
      </div>
      <div class="row c4">
        <div class="field"><label>Moneda</label>
          <select id="cMoneda"><option value="CLP">Peso chileno (CLP)</option><option value="USD">Dólar (USD)</option></select></div>
        <div class="field hidden" id="cTCWrap"><label>Tipo de cambio (CLP por USD)</label>
          <input id="cTipoCambio" type="number" step="any" min="0" placeholder="Ej: 950"></div>
        <div class="field"><label>Costo despacho <span id="cDespLabel">(CLP)</span></label>
          <input id="cDespacho" type="number" step="any" min="0" value="0"></div>
        <div class="field"><label>Costo importación (CLP, opcional)</label>
          <input id="cImportacion" type="number" step="any" min="0" value="0"
                 title="Aduana/courier (FedEx, DHL). Suele llegar después — también se puede agregar editando la compra."></div>
      </div>
    </div>

    <div class="card">
      <h2>Productos</h2>
      <div class="sub">Busca el producto; si no existe, créalo al vuelo. Para un gasto sin productos (arriendo, luz, servicios) deja esto vacío e ingresa el monto directo abajo.</div>
      <div id="addProdSlot" class="field"></div>
      <div class="item-row item-head"><div>Producto</div><div>Marca</div><div>Cant.</div><div>Precio unit.</div><div class="right">Subtotal</div><div></div></div>
      <div id="itemsBox"></div>
      <div id="itemsEmpty" class="empty">Aún no agregas productos.</div>
      <div id="montoDirectoWrap" class="field" style="margin-top:12px;max-width:260px">
        <label>Monto total (gasto sin productos)</label>
        <input id="cMontoDirecto" type="number" step="any" min="0" placeholder="Ej: 850000">
      </div>
      <div id="recurBox" class="field hidden" style="margin-top:14px;padding:14px;background:var(--light-bg);border-radius:10px">
        <label style="margin-bottom:10px">🔁 Este gasto se repite cada mes</label>
        <div class="row c2">
          <div class="field"><label>Nombre del cargo</label><input id="rNombre" placeholder="Ej: Google Workspace"></div>
          <div class="field"><label>Día del mes en que se cobra</label>
            <input id="rDiaMes" type="number" min="1" max="31" value="1"
                   title="Si el mes no tiene ese día (ej. 31 en febrero), se cobra el último día del mes"></div>
        </div>
        <div class="row c2">
          <div class="field"><label>Hasta cuándo</label>
            <select id="rHasta"><option value="indef">Indefinido</option><option value="fecha">Hasta una fecha</option></select></div>
          <div class="field hidden" id="rFechaFinWrap"><label>Fecha de término</label><input id="rFechaFin" type="date"></div>
        </div>
        <p class="muted" style="font-size:12px">Se genera solo, cada mes, hasta que lo cortes desde la pestaña 🔁 Recurrentes.</p>
      </div>
      <div class="flex" style="margin-top:8px;border-top:2px solid var(--line);padding-top:12px">
        <div class="spacer"></div>
        <div style="font-size:13px;color:var(--text-mid)">TOTAL</div>
        <div id="cTotal" style="font-size:22px;font-weight:700;color:var(--navy);min-width:120px;text-align:right">$0</div>
      </div>
    </div>

    <div class="field" id="notaSlot"><label>Notas (opcional)</label><textarea id="cNotas" rows="2"></textarea></div>
    <div class="flex"><div class="spacer"></div><button class="btn gold" id="cGuardar">💾 Guardar compra</button></div>
  `;

  // proveedor
  const provBus = buscador({
    items: CACHE.proveedores, placeholder: 'Buscar proveedor…',
    onPick: it => { compraProvId = it.id; },
    onCrear: async txt => {
      try { const j = await api('/api/compras/proveedores', { method: 'POST', body: { nombre: txt } });
        const nuevo = { id: j.id, nombre: txt }; CACHE.proveedores.push(nuevo); return nuevo;
      } catch (e) { toast(e.message, 'err'); return null; }
    }
  });
  $('#cProvSlot').appendChild(provBus.wrap);

  // categorías
  $('#cCategoria').innerHTML = '<option value="">(sin categoría)</option>' +
    CACHE.categorias.map(c => `<option value="${c.id}">${esc(c.nombre)}</option>`).join('');

  // agregar producto
  const prodBus = buscador({
    items: CACHE.productos, placeholder: 'Buscar o crear producto…',
    onPick: it => { agregarItem(it); prodBus.reset(); },
    onCrear: async txt => { const p = await modalNuevoProducto(txt); if (p) { agregarItem(p); prodBus.reset(); } return null; }
  });
  $('#addProdSlot').appendChild(prodBus.wrap);

  $('#cFoto').onchange = subirFoto;
  $('#cGuardar').onclick = guardarCompra;
  $('#cMontoDirecto').oninput = recalcTotal;
  const onMoneda = () => {
    const usd = $('#cMoneda').value === 'USD';
    $('#cTCWrap').classList.toggle('hidden', !usd);
    $('#cDespLabel').textContent = usd ? '(USD)' : '(CLP)';
    recalcTotal();
  };
  $('#cMoneda').onchange = onMoneda;
  $('#cTipoCambio').oninput = recalcTotal;
  $('#cDespacho').oninput = recalcTotal;
  $('#cImportacion').oninput = recalcTotal;
  $('#cTipoGasto').onchange = () => pintarItems();
  $('#rHasta').onchange = e => $('#rFechaFinWrap').classList.toggle('hidden', e.target.value !== 'fecha');
  pintarItems();
};

function agregarItem(prod) {
  const ya = compraItems.find(i => i.producto_id === prod.id);
  if (ya) { ya.cantidad++; pintarItems(); return; }
  const ult = prod.ultima_compra || {};
  compraItems.push({ producto_id: prod.id, producto_nombre: prod.nombre, unidad: prod.unidad || 'unidad',
    marca: prod.marca || ult.marca || '', cantidad: 1, precio_unitario: ult.precio_unitario || 0 });
  pintarItems();
}
function pintarItems() {
  const box = $('#itemsBox'); if (!box) return;
  const hayItems = compraItems.length > 0;
  $('#itemsEmpty').classList.toggle('hidden', hayItems);
  // el monto directo solo aplica cuando NO hay productos
  const mw = $('#montoDirectoWrap'); if (mw) mw.classList.toggle('hidden', hayItems);
  // los campos de recurrencia (día de cobro, indefinido/hasta fecha) solo tienen
  // sentido para un gasto sin productos marcado "recurrente" (suscripción/servicio)
  const rb = $('#recurBox');
  if (rb) rb.classList.toggle('hidden', hayItems || $('#cTipoGasto')?.value !== 'recurrente');
  box.innerHTML = compraItems.map((it, i) => `
    <div class="item-row">
      <div>${esc(it.producto_nombre)} <span class="muted" style="font-size:12px">(${esc(it.unidad)})</span></div>
      <input class="marca" type="text" placeholder="Marca" value="${esc(it.marca || '')}" data-i="${i}" data-k="marca">
      <input type="number" min="0" step="any" value="${it.cantidad}" data-i="${i}" data-k="cantidad">
      <input type="number" min="0" step="any" value="${it.precio_unitario}" data-i="${i}" data-k="precio_unitario">
      <div class="sub">${clp(it.cantidad * it.precio_unitario)}</div>
      <div class="item-x" data-del="${i}">✕</div>
    </div>`).join('');
  box.querySelectorAll('input').forEach(inp => inp.oninput = () => {
    const k = inp.dataset.k;
    compraItems[inp.dataset.i][k] = k === 'marca' ? inp.value : (Number(inp.value) || 0);
    if (k !== 'marca') recalcTotal();
  });
  box.querySelectorAll('[data-del]').forEach(x => x.onclick = () => { compraItems.splice(+x.dataset.del, 1); pintarItems(); });
  recalcTotal();
}
function recalcTotal() {
  const el = $('#cTotal'); if (!el) return;
  const moneda = $('#cMoneda')?.value || 'CLP';
  const despacho = Number($('#cDespacho')?.value) || 0;
  const base = compraItems.length
    ? compraItems.reduce((a, it) => a + it.cantidad * it.precio_unitario, 0)
    : (Number($('#cMontoDirecto')?.value) || 0);
  const total = base + despacho;                 // en la moneda de la compra
  const importacion = Number($('#cImportacion')?.value) || 0;
  if (moneda === 'USD') {
    const tc = Number($('#cTipoCambio')?.value) || 0;
    el.innerHTML = money(total, 'USD') + (tc > 0
      ? `<div style="font-size:13px;color:var(--text-mid);font-weight:600">≈ ${clp(total * tc + importacion)}</div>`
      : `<div style="font-size:12px;color:var(--danger);font-weight:600">falta tipo de cambio</div>`);
  } else {
    el.textContent = clp(total + importacion);
  }
}

async function subirFoto(e) {
  const file = e.target.files[0]; if (!file) return;
  try {
    let blob = file;
    if (file.type.startsWith('image/')) blob = await downscale(file);
    const fd = new FormData(); fd.append('file', blob, file.name || 'factura.jpg');
    const j = await api('/api/compras/foto', { method: 'POST', body: fd });
    compraFoto = j.foto_path; toast('Foto adjuntada ✓', 'ok');
  } catch (err) { toast(err.message, 'err'); }
}
function downscale(file, max = 1600, q = 0.72) {
  return new Promise(res => {
    const img = new Image();
    img.onload = () => {
      let { width: w, height: h } = img;
      if (w > max || h > max) { const r = Math.min(max / w, max / h); w = w * r | 0; h = h * r | 0; }
      const c = document.createElement('canvas'); c.width = w; c.height = h;
      c.getContext('2d').drawImage(img, 0, 0, w, h);
      c.toBlob(b => res(b || file), 'image/jpeg', q);
    };
    img.onerror = () => res(file);
    img.src = URL.createObjectURL(file);
  });
}

async function guardarCompra() {
  const montoDirecto = Number($('#cMontoDirecto')?.value) || 0;
  if (!compraItems.length && montoDirecto <= 0)
    return toast('Agrega productos o ingresa un monto directo', 'err');
  const moneda = $('#cMoneda').value;
  const tipoCambio = moneda === 'USD' ? (Number($('#cTipoCambio').value) || 0) : 1;
  if (moneda === 'USD' && tipoCambio <= 0)
    return toast('Ingresa el tipo de cambio (CLP por dólar)', 'err');
  const esRecurrente = $('#cTipoGasto').value === 'recurrente' && !compraItems.length;

  if (esRecurrente) {
    const nombre = $('#rNombre').value.trim();
    const diaMes = Number($('#rDiaMes').value) || 0;
    if (!nombre) return toast('Ponle un nombre al cargo recurrente', 'err');
    if (diaMes < 1 || diaMes > 31) return toast('El día del mes debe ser entre 1 y 31', 'err');
    const fechaFin = $('#rHasta').value === 'fecha' ? $('#rFechaFin').value : null;
    if ($('#rHasta').value === 'fecha' && !fechaFin) return toast('Ingresa la fecha de término', 'err');
    const body = { nombre, proveedor_id: compraProvId, categoria_id: $('#cCategoria').value || null,
      monto: montoDirecto, moneda, tipo_cambio: tipoCambio, forma_pago: $('#cPago').value,
      dia_mes: diaMes, fecha_inicio: $('#cFecha').value, fecha_fin: fechaFin,
      foto_path: compraFoto, notas: $('#cNotas').value };
    try {
      const j = await api('/api/compras/suscripciones', { method: 'POST', body });
      toast(j.compra_id ? `Cargo recurrente creado y cobrado este mes ✓` : `Cargo recurrente creado — se cobrará el día ${diaMes} ✓`, 'ok');
      await recargarCaches(); refrescarPendientesBadge(); RENDER.compras();
    } catch (e) { toast(e.message, 'err'); }
    return;
  }

  const cab = {
    fecha: $('#cFecha').value, proveedor_id: compraProvId, tipo_doc: $('#cTipoDoc').value,
    nro_doc: $('#cNroDoc').value, forma_pago: $('#cPago').value, tipo_gasto: $('#cTipoGasto').value,
    categoria_id: $('#cCategoria').value || null, foto_path: compraFoto, notas: $('#cNotas').value,
    moneda, tipo_cambio: tipoCambio,
    costo_despacho: Number($('#cDespacho').value) || 0,
    costo_importacion: Number($('#cImportacion').value) || 0,
    total: compraItems.length ? undefined : montoDirecto
  };
  const items = compraItems.map(i => ({ producto_id: i.producto_id, marca: i.marca || '', cantidad: i.cantidad, precio_unitario: i.precio_unitario }));
  try {
    const j = await api('/api/compras/compras', { method: 'POST', body: { cabecera: cab, items } });
    toast('Compra #' + j.id + ' guardada ✓', 'ok');
    await recargarCaches(); refrescarPendientesBadge(); RENDER.compras();
  } catch (e) { toast(e.message, 'err'); }
}

async function modalNuevoProducto(nombreInicial = '') {
  return new Promise(resolve => {
    const m = modal(`
      <h3>Nuevo producto</h3>
      <div class="field"><label>Nombre</label><input id="npNombre" value="${esc(nombreInicial)}"></div>
      <div class="row c2">
        <div class="field"><label>Categoría producto</label><input id="npCat" placeholder="Ej: Insumo clínico"></div>
        <div class="field"><label>Unidad</label><select id="npUnidad">
          ${['unidad','caja','paquete','litro','kilo','metro','par','set'].map(u => `<option>${u}</option>`).join('')}</select></div>
      </div>
      <div class="row c2">
        <div class="field"><label>Stock inicial</label><input id="npStock" type="number" value="0" step="any"></div>
        <div class="field"><label>Stock mínimo (alerta)</label><input id="npMin" type="number" value="0" step="any"></div>
      </div>
      <div class="flex" style="margin-top:6px"><div class="spacer"></div>
        <button class="btn ghost" id="npCancel">Cancelar</button>
        <button class="btn gold" id="npOk">Crear</button></div>
    `);
    m.querySelector('#npNombre').focus();
    m.querySelector('#npCancel').onclick = () => { closeModal(); resolve(null); };
    m.querySelector('#npOk').onclick = async () => {
      try {
        const body = { nombre: m.querySelector('#npNombre').value, categoria_prod: m.querySelector('#npCat').value,
          unidad: m.querySelector('#npUnidad').value, stock_inicial: Number(m.querySelector('#npStock').value) || 0,
          stock_minimo: Number(m.querySelector('#npMin').value) || 0 };
        const j = await api('/api/compras/productos', { method: 'POST', body });
        const nuevo = { id: j.id, nombre: body.nombre, unidad: body.unidad, stock_actual: body.stock_inicial, ultima_compra: null };
        CACHE.productos.push(nuevo); closeModal(); toast('Producto creado ✓', 'ok'); resolve(nuevo);
      } catch (e) { toast(e.message, 'err'); }
    };
  });
}

/* ══════════════════ TAB: HISTORIAL ══════════════════ */
RENDER.historial = async () => {
  const s = $('#tab-historial');
  s.innerHTML = `
    <div class="card">
      <h2>Historial de compras</h2>
      <div class="row c4" style="margin-bottom:14px">
        <div class="field"><label>Desde</label><input type="date" id="hDesde"></div>
        <div class="field"><label>Hasta</label><input type="date" id="hHasta"></div>
        <div class="field"><label>Proveedor</label><select id="hProv"><option value="">Todos</option>${CACHE.proveedores.map(p => `<option value="${p.id}">${esc(p.nombre)}</option>`).join('')}</select></div>
        <div class="field"><label>Tipo</label><select id="hTipo"><option value="">Todos</option><option value="fijo">Fijo</option><option value="variable">Variable</option><option value="recurrente">Recurrente</option></select></div>
      </div>
      <button class="btn ghost sm" id="hFiltrar">Filtrar</button>
      <div class="tablewrap" style="margin-top:14px"><table id="hTabla"></table></div>
    </div>`;
  const cargar = async () => {
    const q = new URLSearchParams();
    if ($('#hDesde').value) q.set('desde', $('#hDesde').value);
    if ($('#hHasta').value) q.set('hasta', $('#hHasta').value);
    if ($('#hProv').value) q.set('proveedor_id', $('#hProv').value);
    if ($('#hTipo').value) q.set('tipo_gasto', $('#hTipo').value);
    try {
      const j = await api('/api/compras/compras?' + q);
      $('#hTabla').innerHTML = `<tr><th>Fecha</th><th>Proveedor</th><th>Doc</th><th>Categoría</th><th>Tipo</th><th class="num">Total</th><th></th></tr>` +
        (j.compras.length ? j.compras.map(c => `<tr>
          <td>${esc(c.fecha)}</td><td>${esc(c.proveedor_nombre || '—')}</td>
          <td>${esc(c.tipo_doc || '')} ${esc(c.nro_doc || '')}</td>
          <td>${esc(c.categoria_nombre || '—')}</td>
          <td><span class="pill ${c.tipo_gasto}">${c.tipo_gasto}</span>${c.moneda === 'USD' ? ' <span class="pill" style="background:#EBF8FF;color:#2B6CB0">USD</span>' : ''}</td>
          <td class="num">${clp(c.total_clp || c.total)}</td>
          <td><button class="btn ghost sm" data-ver="${c.id}">Ver</button></td></tr>`).join('')
          : `<tr><td colspan="7" class="empty">Sin compras en el período.</td></tr>`);
      $$('#hTabla [data-ver]').forEach(b => b.onclick = () => verCompra(b.dataset.ver));
    } catch (e) { toast(e.message, 'err'); }
  };
  $('#hFiltrar').onclick = cargar;
  cargar();
};

async function verCompra(id) {
  try {
    const j = await api('/api/compras/compras/' + id); const c = j.compra;
    const foto = c.foto_path ? `<p style="margin-top:10px"><a href="${API}/api/compras/foto/${encodeURIComponent(c.foto_path)}" target="_blank">📎 Ver documento adjunto</a></p>` : '';
    const mon = c.moneda || 'CLP';
    const costos = [];
    if (c.costo_despacho) costos.push(`Despacho: ${money(c.costo_despacho, mon)}`);
    if (c.costo_importacion) costos.push(`Importación: ${clp(c.costo_importacion)}`);
    if (mon === 'USD') costos.push(`Tipo cambio: ${clp(c.tipo_cambio)}/USD`);
    const m = modal(`
      <h3>Compra #${c.id}${mon === 'USD' ? ' <span class="pill" style="background:#EBF8FF;color:#2B6CB0">USD</span>' : ''}</h3>
      <p class="muted">${esc(c.fecha)} · ${esc(c.proveedor_nombre || 'sin proveedor')} · ${esc(c.tipo_doc || '')} ${esc(c.nro_doc || '')}</p>
      <p class="muted" style="margin-bottom:12px">${esc(c.forma_pago || '')} · <span class="pill ${c.tipo_gasto}">${c.tipo_gasto}</span> · ${esc(c.categoria_nombre || 'sin categoría')}${c.suscripcion_id ? ' · <span class="pill recurrente">🔁 generado automático</span>' : ''}</p>
      <div class="tablewrap"><table><tr><th>Producto</th><th>Marca</th><th class="num">Cant.</th><th class="num">P. unit.</th><th class="num">Subtotal</th></tr>
        ${c.items.map(i => `<tr><td>${esc(i.producto_nombre || '—')}</td><td class="muted">${esc(i.marca || '—')}</td><td class="num">${i.cantidad}</td><td class="num">${money(i.precio_unitario, mon)}</td><td class="num">${money(i.subtotal, mon)}</td></tr>`).join('')}
        ${c.items.length ? '' : `<tr><td colspan="5" class="muted">Gasto sin productos</td></tr>`}
        <tr><td colspan="4" class="num">Total ${mon}</td><td class="num"><b>${money(c.total, mon)}</b></td></tr>
        ${mon === 'USD' || c.costo_importacion ? `<tr><td colspan="4" class="num"><b>Total CLP</b></td><td class="num"><b>${clp(c.total_clp || c.total)}</b></td></tr>` : ''}</table></div>
      ${costos.length ? `<p class="muted" style="margin-top:10px">${costos.join(' · ')}</p>` : ''}
      ${c.notas ? `<p class="muted" style="margin-top:10px">📝 ${esc(c.notas)}</p>` : ''}
      ${foto}
      <div class="flex" style="margin-top:16px">
        ${puede('registrar') ? `<button class="btn gold sm" id="editCostos">✏️ Editar costos</button>` : ''}
        <div class="spacer"></div>
        ${puede('admin') ? `<button class="btn danger sm" id="delCompra">Eliminar</button>` : ''}
        <button class="btn ghost sm" onclick="document.getElementById('modalRoot').innerHTML=''">Cerrar</button></div>`);
    if (puede('registrar')) m.querySelector('#editCostos').onclick = () => editarCostosCompra(c);
    if (puede('admin')) m.querySelector('#delCompra').onclick = async () => {
      if (!confirm('¿Eliminar la compra #' + c.id + '? Se revertirá el stock que sumó.')) return;
      try { await api('/api/compras/compras/eliminar', { method: 'POST', body: { id: c.id } });
        closeModal(); toast('Compra eliminada', 'ok'); await recargarCaches(); RENDER.historial();
      } catch (e) { toast(e.message, 'err'); }
    };
  } catch (e) { toast(e.message, 'err'); }
}

function editarCostosCompra(c) {
  const mon = c.moneda || 'CLP';
  const m = modal(`
    <h3>Editar costos — Compra #${c.id}</h3>
    <p class="muted" style="margin-bottom:12px">Ajusta despacho, moneda o agrega el <b>costo de importación</b> (aduana/courier) que suele llegar después por FedEx, DHL, etc. El total se recalcula solo.</p>
    <div class="row c2">
      <div class="field"><label>Moneda</label><select id="ecMoneda">
        <option value="CLP" ${mon === 'CLP' ? 'selected' : ''}>Peso (CLP)</option>
        <option value="USD" ${mon === 'USD' ? 'selected' : ''}>Dólar (USD)</option></select></div>
      <div class="field ${mon === 'USD' ? '' : 'hidden'}" id="ecTCWrap"><label>Tipo de cambio (CLP/USD)</label>
        <input id="ecTC" type="number" step="any" min="0" value="${c.tipo_cambio || ''}"></div>
    </div>
    <div class="row c2">
      <div class="field"><label>Costo despacho (${mon})</label><input id="ecDesp" type="number" step="any" min="0" value="${c.costo_despacho || 0}"></div>
      <div class="field"><label>Costo importación (CLP)</label><input id="ecImp" type="number" step="any" min="0" value="${c.costo_importacion || 0}"></div>
    </div>
    <div class="flex" style="margin-top:6px"><div class="spacer"></div>
      <button class="btn ghost" onclick="document.getElementById('modalRoot').innerHTML=''">Cancelar</button>
      <button class="btn gold" id="ecOk">Guardar</button></div>`);
  m.querySelector('#ecMoneda').onchange = e => m.querySelector('#ecTCWrap').classList.toggle('hidden', e.target.value !== 'USD');
  m.querySelector('#ecOk').onclick = async () => {
    const moneda = m.querySelector('#ecMoneda').value;
    const body = { id: c.id, moneda,
      tipo_cambio: moneda === 'USD' ? (Number(m.querySelector('#ecTC').value) || 0) : 1,
      costo_despacho: Number(m.querySelector('#ecDesp').value) || 0,
      costo_importacion: Number(m.querySelector('#ecImp').value) || 0 };
    if (moneda === 'USD' && body.tipo_cambio <= 0) return toast('Ingresa el tipo de cambio', 'err');
    try {
      const r = await api('/api/compras/compras/actualizar', { method: 'POST', body });
      closeModal(); toast('Costos actualizados · Total ' + clp(r.total_clp) + ' ✓', 'ok');
      RENDER.historial();
    } catch (e) { toast(e.message, 'err'); }
  };
}

/* ══════════════════ TAB: STOCK ══════════════════ */
RENDER.stock = async () => {
  const s = $('#tab-stock');
  let alertas = [];
  try { alertas = (await api('/api/compras/alertas')).productos; } catch {}
  const prods = CACHE.productos;
  s.innerHTML = `
    ${alertas.length ? `<div class="card" style="border-color:var(--warn);background:var(--warn-bg)">
      <h2 style="color:var(--warn)">⚠️ ${alertas.length} producto(s) bajo el mínimo</h2>
      <div class="flex wrap" style="margin-top:8px">${alertas.map(a => `<span class="pill low">${esc(a.nombre)}: ${a.stock_actual}/${a.stock_minimo}</span>`).join('')}</div>
    </div>` : ''}
    <div class="card">
      <div class="flex" style="margin-bottom:12px"><h2>Productos y stock</h2><div class="spacer"></div>
        <input id="stBuscar" placeholder="Filtrar…" style="max-width:220px">
        ${puede('registrar') ? `<button class="btn gold sm" id="stNuevo">➕ Producto</button>` : ''}</div>
      <div class="tablewrap"><table id="stTabla"></table></div>
    </div>`;
  const pintar = () => {
    const q = ($('#stBuscar').value || '').toLowerCase();
    const list = prods.filter(p => p.nombre.toLowerCase().includes(q) || (p.categoria_prod || '').toLowerCase().includes(q));
    $('#stTabla').innerHTML = `<tr><th>Producto</th><th>Categoría</th><th class="num">Stock</th><th>Última compra</th><th class="num">Últ. precio</th><th></th></tr>` +
      (list.length ? list.map(p => {
        const uc = p.ultima_compra;
        const low = p.stock_minimo > 0 && p.stock_actual <= p.stock_minimo;
        return `<tr>
          <td><b>${esc(p.nombre)}</b> <span class="muted">${esc(p.unidad)}</span></td>
          <td class="muted">${esc(p.categoria_prod || '—')}</td>
          <td class="num"><span class="pill ${low ? 'low' : 'ok'}">${p.stock_actual}</span>${p.marca ? `<div class="muted" style="font-size:11px">${esc(p.marca)}</div>` : ''}</td>
          <td class="muted">${uc ? esc(uc.fecha) + ' · ' + esc(uc.proveedor || '—') + (uc.marca ? ' · ' + esc(uc.marca) : '') : '—'}</td>
          <td class="num">${uc ? clp(uc.precio_unitario) : '—'}</td>
          <td class="right"><button class="btn ghost sm" data-ver="${p.id}">Ver</button>
            ${puede('escanear') ? `<button class="btn ghost sm" data-salida="${p.id}" title="Sacar del stock">➖</button>` : ''}</td></tr>`;
      }).join('') : `<tr><td colspan="6" class="empty">Sin productos. Créalos al registrar una compra o con el botón «Producto».</td></tr>`);
    $$('#stTabla [data-ver]').forEach(b => b.onclick = () => verProducto(b.dataset.ver));
    $$('#stTabla [data-salida]').forEach(b => b.onclick = () => modalSalida(prods.find(p => p.id == b.dataset.salida)));
  };
  $('#stBuscar').oninput = pintar;
  if ($('#stNuevo')) $('#stNuevo').onclick = async () => { const p = await modalNuevoProducto(''); if (p) { await recargarCaches(); RENDER.stock(); } };
  pintar();
};

async function verProducto(id) {
  try {
    const j = await api('/api/compras/productos/' + id); const p = j.producto;
    const codigos = (p.codigos || []).map(c => `<span class="pill ok" title="${c.origen}">${esc(c.codigo)}</span>`).join(' ') || '<span class="muted">ninguno</span>';
    const hist = (p.historial_precios || []).slice(0, 8);
    const maxP = Math.max(1, ...hist.map(h => h.precio_unitario));
    const m = modal(`
      <h3>${esc(p.nombre)}</h3>
      <p class="muted" style="margin-bottom:10px">${esc(p.categoria_prod || 'sin categoría')} · ${esc(p.unidad)}${p.marca ? ' · última marca: ' + esc(p.marca) : ''} · stock <b>${p.stock_actual}</b> (mín. ${p.stock_minimo})</p>
      <div class="field"><label>Códigos asociados (barras/QR)</label><div>${codigos}</div>
        ${puede('registrar') ? `<div class="flex" style="margin-top:8px">
          <input id="vpCod" placeholder="Escanea o escribe un código" style="flex:1">
          <button class="btn ghost sm" id="vpAddCod">Asociar</button>
          <button class="btn gold sm" id="vpGenCod">Generar + imprimir</button></div>` : ''}</div>
      <div class="field"><label>Historial de precios</label>
        ${hist.length ? hist.map(h => `<div class="bar"><div class="lab">${esc(h.fecha)}${h.marca ? ' · ' + esc(h.marca) : ''}</div>
          <div class="track"><div class="fill" style="width:${Math.round(h.precio_unitario / maxP * 100)}%"></div></div>
          <div class="val">${clp(h.precio_unitario)}</div></div>`).join('') : '<p class="muted">Sin compras registradas.</p>'}</div>
      <div class="field"><label>Movimientos recientes</label>
        <div class="tablewrap"><table>${(p.movimientos || []).slice(0, 10).map(mv => `<tr>
          <td class="muted">${esc((mv.creado || '').slice(0, 16).replace('T', ' '))}</td>
          <td><span class="pill ${mv.tipo === 'salida' ? 'low' : 'ok'}">${mv.tipo}</span></td>
          <td class="num">${mv.cantidad}</td><td class="muted">${esc(mv.motivo || '')}</td></tr>`).join('') || '<tr><td class="muted">Sin movimientos.</td></tr>'}</table></div></div>
      <div class="flex" style="margin-top:14px"><div class="spacer"></div>
        <button class="btn ghost sm" onclick="document.getElementById('modalRoot').innerHTML=''">Cerrar</button></div>`);
    if (puede('registrar')) {
      m.querySelector('#vpAddCod').onclick = async () => {
        const cod = m.querySelector('#vpCod').value.trim(); if (!cod) return;
        try { await api('/api/compras/productos/codigo', { method: 'POST', body: { producto_id: id, codigo: cod } });
          toast('Código asociado ✓', 'ok'); verProducto(id);
        } catch (e) { toast(e.message, 'err'); }
      };
      m.querySelector('#vpGenCod').onclick = async () => {
        try { const r = await api('/api/compras/productos/generar-codigo', { method: 'POST', body: { producto_id: id, imprimir: true } });
          toast('Código ' + r.codigo + ' generado y enviado a imprimir ✓', 'ok'); verProducto(id);
        } catch (e) { toast(e.message, 'err'); }
      };
    }
  } catch (e) { toast(e.message, 'err'); }
}

function modalSalida(prod) {
  const m = modal(`
    <h3>Sacar del stock</h3>
    <p class="muted" style="margin-bottom:12px">${esc(prod.nombre)} — stock actual <b>${prod.stock_actual}</b> ${esc(prod.unidad)}</p>
    <div class="row c2">
      <div class="field"><label>Cantidad</label><input id="msCant" type="number" value="1" step="any" min="0"></div>
      <div class="field"><label>Motivo</label><input id="msMotivo" value="Consumo"></div>
    </div>
    <div class="flex" style="margin-top:6px"><div class="spacer"></div>
      <button class="btn ghost" onclick="document.getElementById('modalRoot').innerHTML=''">Cancelar</button>
      <button class="btn gold" id="msOk">Descontar</button></div>`);
  m.querySelector('#msOk').onclick = async () => {
    try { await api('/api/compras/salida', { method: 'POST', body: { producto_id: prod.id, cantidad: Number(m.querySelector('#msCant').value) || 0, motivo: m.querySelector('#msMotivo').value } });
      closeModal(); toast('Stock descontado ✓', 'ok'); await recargarCaches(); RENDER.stock();
    } catch (e) { toast(e.message, 'err'); }
  };
}

/* ══════════════════ TAB: ESCANEAR SALIDA ══════════════════ */
let scanStream = null, scanLoop = null, scanDetector = null, scanBusy = false;
RENDER.escanear = () => {
  const s = $('#tab-escanear');
  const soporta = 'BarcodeDetector' in window;
  s.innerHTML = `
    <div class="card">
      <h2>Escanear salida de stock</h2>
      <div class="sub">Escanea el código de barras o QR de la caja que sacas; se descuenta del stock.</div>
      <div class="row c2">
        <div class="field"><label>Código (lector USB o manual)</label>
          <input id="scInput" placeholder="Apunta el lector aquí y escanea…" autocomplete="off"></div>
        <div class="field"><label>Cantidad a descontar</label><input id="scCant" type="number" value="1" step="any" min="0"></div>
      </div>
      <div class="flex wrap">
        ${soporta ? `<button class="btn" id="scCam">📷 Escanear con cámara</button>` : `<span class="muted">La cámara no está disponible en este navegador; usa un lector USB o escribe el código.</span>`}
      </div>
      <div id="scCamBox" class="scanbox hidden" style="margin-top:14px"><video id="video" autoplay muted playsinline></video>
        <p style="margin-top:8px;font-size:13px">Apunta al código…</p></div>
      <div id="scResult" style="margin-top:14px"></div>
    </div>`;
  const inp = $('#scInput'); inp.focus();
  // Lector USB: la mayoría envía "Enter" al final → se procesa al instante (handler abajo).
  // Auto-descuento sin Enter: se detecta un escáner por la VELOCIDAD ENTRE TECLAS —
  // un lector mete los caracteres a <35ms entre sí; una persona tipea mucho más lento.
  // Así, escribir a mano NUNCA dispara solo (exige Enter) y no hay falsos positivos.
  let lastKey = 0, maxGap = 0, scTimer = null;
  const resetGap = () => { lastKey = 0; maxGap = 0; };
  const enviar = () => { const v = inp.value.trim(); inp.value = ''; resetGap(); clearTimeout(scTimer); if (v) procesarCodigo(v); };
  inp.onkeydown = e => {
    if (e.key === 'Enter') { e.preventDefault(); enviar(); return; }
    if (e.key.length === 1) {                 // solo teclas de carácter
      const now = performance.now();
      if (lastKey) maxGap = Math.max(maxGap, now - lastKey);
      lastKey = now;
    }
  };
  inp.oninput = () => {
    clearTimeout(scTimer);
    scTimer = setTimeout(() => {
      const v = inp.value.trim();
      // ráfaga de escáner: 6+ caracteres y TODAS las teclas llegaron muy rápido
      if (v.length >= 6 && maxGap > 0 && maxGap < 35) enviar();
      resetGap();
    }, 150);
  };
  if (soporta) $('#scCam').onclick = toggleCam;
};
async function toggleCam() {
  if (scanStream) return pararCam();
  try {
    scanDetector = scanDetector || new window.BarcodeDetector({
      formats: ['qr_code', 'ean_13', 'ean_8', 'code_128', 'code_39', 'upc_a', 'upc_e', 'itf', 'codabar', 'data_matrix'] });
    scanStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
    const v = $('#video'); v.srcObject = scanStream;
    $('#scCamBox').classList.remove('hidden'); $('#scCam').textContent = '⏹ Detener cámara';
    const tick = async () => {
      if (!scanStream) return;
      if (!scanBusy) {
        scanBusy = true;
        try { const codes = await scanDetector.detect(v); if (codes && codes.length) { pararCam(); procesarCodigo(codes[0].rawValue); return; } }
        catch {}
        scanBusy = false;
      }
      scanLoop = requestAnimationFrame(tick);
    };
    tick();
  } catch (e) { toast('No se pudo abrir la cámara: ' + e.message, 'err'); }
}
function pararCam() {
  if (scanLoop) cancelAnimationFrame(scanLoop);
  if (scanStream) { scanStream.getTracks().forEach(t => t.stop()); scanStream = null; }
  scanBusy = false;
  const box = $('#scCamBox'); if (box) box.classList.add('hidden');
  const b = $('#scCam'); if (b) b.textContent = '📷 Escanear con cámara';
}
async function procesarCodigo(codigo) {
  if (!codigo) return;
  const cant = Number($('#scCant')?.value) || 1;
  try {
    const j = await api('/api/compras/salida', { method: 'POST', body: { codigo, cantidad: cant, motivo: 'Consumo (escaneo)' } });
    beep(true);
    $('#scResult').innerHTML = `<div class="card" style="border-color:var(--ok);background:var(--ok-bg);margin:0">
      <b>${esc(j.producto.nombre)}</b> — descontado ${cant} ${esc(j.producto.unidad)}. Stock ahora: <b>${j.stock_actual}</b></div>`;
    toast('Salida registrada ✓', 'ok');
  } catch (e) {
    beep(false);
    if (/no reconocido/i.test(e.message)) ofrecerMapear(codigo);
    else toast(e.message, 'err');
  }
  $('#scInput')?.focus();
}
function ofrecerMapear(codigo) {
  if (!puede('registrar')) { toast('Código no reconocido', 'err'); return; }
  const bus = buscador({
    items: CACHE.productos, placeholder: 'Buscar producto para asociar…',
    onPick: async it => {
      try { await api('/api/compras/productos/codigo', { method: 'POST', body: { producto_id: it.id, codigo } });
        toast('Código asociado a ' + it.nombre + ' ✓', 'ok'); closeModal(); procesarCodigo(codigo);
      } catch (e) { toast(e.message, 'err'); }
    },
    onCrear: async txt => { const p = await modalNuevoProducto(txt); if (p) { await api('/api/compras/productos/codigo', { method: 'POST', body: { producto_id: p.id, codigo } }); toast('Producto creado y código asociado ✓', 'ok'); closeModal(); procesarCodigo(codigo); } return null; }
  });
  const m = modal(`<h3>Código nuevo</h3><p class="muted" style="margin-bottom:12px">El código <b>${esc(codigo)}</b> no está asociado a ningún producto. Asócialo una vez y de ahí en adelante se reconocerá solo.</p><div id="mapSlot"></div>
    <div class="flex" style="margin-top:14px"><div class="spacer"></div><button class="btn ghost" onclick="document.getElementById('modalRoot').innerHTML=''">Cancelar</button></div>`);
  m.querySelector('#mapSlot').appendChild(bus.wrap);
}
function beep(ok) {
  try { const ac = new (window.AudioContext || window.webkitAudioContext)(); const o = ac.createOscillator(); const g = ac.createGain();
    o.connect(g); g.connect(ac.destination); o.frequency.value = ok ? 880 : 220; g.gain.value = .1; o.start(); o.stop(ac.currentTime + .12);
  } catch {}
}

/* ══════════════════ TAB: SOLICITUDES DE COMPRA ══════════════════ */
let solicitudItems = [];   // {producto_id, nombre, unidad, cantidad, razon}
let _sugCache = [];

RENDER.solicitudes = async () => {
  const s = $('#tab-solicitudes');
  s.innerHTML = `
    <div class="card">
      <h2>Armar solicitud de compra</h2>
      <div class="sub">Agrega los productos que crees necesario comprar. Al agregar uno, el sistema sugiere una cantidad según su consumo.</div>
      <div id="solAddSlot" class="field"></div>
      <div id="solItems"></div>
      <div id="solEmpty" class="empty">Aún no agregas productos a la solicitud.</div>
      <div class="field" style="margin-top:10px"><label>Nota (opcional)</label><input id="solNota" placeholder="Ej: para el próximo mes"></div>
      <div class="flex"><div class="spacer"></div><button class="btn gold" id="solEnviar">📨 Enviar solicitud</button></div>
    </div>
    <div class="card">
      <h2>💡 Sugerencias del sistema</h2>
      <div class="sub">Productos que conviene comprar por stock bajo el mínimo o por proyección de consumo (días de stock restantes).</div>
      <div id="solSug"><p class="muted">Calculando…</p></div>
    </div>
    <div class="card">
      <div class="flex"><h2>🛒 Pendientes por comprar</h2><div class="spacer"></div><span id="solPendN" class="muted"></span></div>
      <div class="sub">Quedan aquí hasta que se registre una compra del producto (se resuelven solos). Los administradores reciben aviso al enviarse una solicitud.</div>
      <div class="tablewrap"><table id="solPend"></table></div>
    </div>`;
  const bus = buscador({
    items: CACHE.productos, placeholder: 'Buscar producto para solicitar…',
    onPick: async it => { await agregarSolicitudItem(it); bus.reset(); },
    onCrear: puede('registrar') ? async txt => { const p = await modalNuevoProducto(txt); if (p) { await agregarSolicitudItem(p); bus.reset(); } return null; } : null
  });
  $('#solAddSlot').appendChild(bus.wrap);
  $('#solEnviar').onclick = enviarSolicitud;
  pintarSolicitudItems();
  cargarSugerencias();
  cargarPendientes();
};

async function agregarSolicitudItem(prod) {
  if (solicitudItems.find(i => i.producto_id === prod.id)) return;
  let cantidad = 1, razon = '';
  try {
    const r = await api('/api/compras/solicitudes/sugerir?producto_id=' + prod.id);
    cantidad = r.sugerencia.cantidad; razon = r.sugerencia.base;
  } catch {}
  solicitudItems.push({ producto_id: prod.id, nombre: prod.nombre, unidad: prod.unidad || 'unidad', cantidad, razon });
  pintarSolicitudItems();
}
function pintarSolicitudItems() {
  const box = $('#solItems'); if (!box) return;
  $('#solEmpty').classList.toggle('hidden', solicitudItems.length > 0);
  box.innerHTML = solicitudItems.map((it, i) => `
    <div class="item-row" style="grid-template-columns:1fr 100px 34px">
      <div>${esc(it.nombre)} <span class="muted" style="font-size:12px">(${esc(it.unidad)})</span>${it.razon ? `<div class="muted" style="font-size:11px">sugerido: ${esc(it.razon)}</div>` : ''}</div>
      <input type="number" min="0" step="any" value="${it.cantidad}" data-i="${i}">
      <div class="item-x" data-del="${i}">✕</div>
    </div>`).join('');
  box.querySelectorAll('input').forEach(inp => inp.oninput = () => { solicitudItems[inp.dataset.i].cantidad = Number(inp.value) || 0; });
  box.querySelectorAll('[data-del]').forEach(x => x.onclick = () => { solicitudItems.splice(+x.dataset.del, 1); pintarSolicitudItems(); });
}
async function cargarSugerencias() {
  const box = $('#solSug'); if (!box) return;
  try {
    _sugCache = (await api('/api/compras/solicitudes/sugerencias')).sugerencias;
    box.innerHTML = _sugCache.length ? `<div class="tablewrap"><table>
      <tr><th>Producto</th><th class="num">Stock</th><th>Motivo</th><th class="num">Sugerido</th><th></th></tr>
      ${_sugCache.map((sug, i) => `<tr>
        <td><b>${esc(sug.nombre)}</b> <span class="muted">${esc(sug.unidad)}</span></td>
        <td class="num"><span class="pill low">${sug.stock_actual}</span></td>
        <td class="muted">${esc(sug.razon)}${sug.rate_mensual ? ` · ~${sug.rate_mensual}/mes` : ''}</td>
        <td class="num"><b>${sug.cantidad_sugerida}</b></td>
        <td class="right"><button class="btn ghost sm" data-add="${i}">＋ Agregar</button></td></tr>`).join('')}
      </table></div>` : '<p class="muted">Nada urgente por ahora. 👍</p>';
    box.querySelectorAll('[data-add]').forEach(b => b.onclick = () => {
      const sug = _sugCache[+b.dataset.add];
      if (!solicitudItems.find(i => i.producto_id === sug.producto_id)) {
        solicitudItems.push({ producto_id: sug.producto_id, nombre: sug.nombre, unidad: sug.unidad, cantidad: sug.cantidad_sugerida, razon: sug.razon });
        pintarSolicitudItems(); toast('Agregado a la solicitud', 'ok');
      }
    });
  } catch (e) { box.innerHTML = '<p class="muted">' + esc(e.message) + '</p>'; }
}
async function cargarPendientes() {
  const box = $('#solPend'); if (!box) return;
  try {
    const list = (await api('/api/compras/solicitudes')).pendientes;
    actualizarBadge(list.length);
    if ($('#solPendN')) $('#solPendN').textContent = list.length ? list.length + ' pendiente(s)' : '';
    box.innerHTML = list.length ? `<tr><th>Producto</th><th class="num">Cantidad</th><th>Solicitó</th><th>Fecha</th><th></th></tr>
      ${list.map(p => `<tr>
        <td>${esc(p.producto_nombre)} <span class="muted">${esc(p.unidad)}</span>${p.nota ? `<div class="muted" style="font-size:11px">${esc(p.nota)}</div>` : ''}</td>
        <td class="num">${p.cantidad_sugerida}</td>
        <td class="muted">${esc(p.solicitante || '—')}</td>
        <td class="muted">${esc((p.creado || '').slice(0, 10))}</td>
        <td class="right"><button class="btn ghost sm" data-cancel="${p.id}">Quitar</button></td></tr>`).join('')}`
      : '<tr><td class="empty">No hay productos pendientes por comprar.</td></tr>';
    box.querySelectorAll('[data-cancel]').forEach(b => b.onclick = async () => {
      try { await api('/api/compras/solicitudes/cancelar', { method: 'POST', body: { id: +b.dataset.cancel } }); toast('Quitado de pendientes', 'ok'); cargarPendientes(); }
      catch (e) { toast(e.message, 'err'); }
    });
  } catch (e) { box.innerHTML = '<tr><td class="empty">' + esc(e.message) + '</td></tr>'; }
}
async function enviarSolicitud() {
  if (!solicitudItems.length) return toast('Agrega al menos un producto', 'err');
  const items = solicitudItems.map(i => ({ producto_id: i.producto_id, cantidad: i.cantidad, motivo: 'manual' }));
  try {
    await api('/api/compras/solicitudes', { method: 'POST', body: { items, nota: $('#solNota').value } });
    toast('Solicitud enviada · se avisó a los administradores ✓', 'ok');
    solicitudItems = []; $('#solNota').value = ''; pintarSolicitudItems(); cargarPendientes(); cargarSugerencias();
  } catch (e) { toast(e.message, 'err'); }
}

/* ══════════════════ TAB: CARGOS RECURRENTES ══════════════════ */
RENDER.recurrentes = async () => {
  const s = $('#tab-recurrentes');
  if (!puede('registrar')) { s.innerHTML = soloLectura(); return; }
  s.innerHTML = `
    <div class="card">
      <h2>🔁 Cargos recurrentes</h2>
      <div class="sub">Se generan solos cada mes en el día que elegiste, hasta que los cortes. Para crear uno nuevo, ve a «Nueva compra», elige tipo de gasto «Recurrente» y deja los productos vacíos.</div>
      <div class="tablewrap"><table id="recTabla"></table></div>
    </div>`;
  await pintarRecurrentes();
};
async function pintarRecurrentes() {
  const tbl = $('#recTabla'); if (!tbl) return;
  try {
    const list = (await api('/api/compras/suscripciones')).suscripciones;
    tbl.innerHTML = `<tr><th>Cargo</th><th>Proveedor</th><th class="num">Monto</th><th class="num">Día</th><th>Próximo cobro</th><th>Estado</th><th></th></tr>` +
      (list.length ? list.map(r => `<tr>
        <td><b>${esc(r.nombre)}</b>${r.notas ? `<div class="muted" style="font-size:11px">${esc(r.notas)}</div>` : ''}</td>
        <td class="muted">${esc(r.proveedor_nombre || '—')}</td>
        <td class="num">${money(r.monto, r.moneda)}</td>
        <td class="num">${r.dia_mes}</td>
        <td class="muted">${r.activa ? esc(r.proxima_cobranza || '—') : '—'}</td>
        <td>${r.activa ? '<span class="pill ok">activo</span>' : '<span class="pill low">cortado</span>'}
          ${r.fecha_fin ? `<div class="muted" style="font-size:11px">${r.activa ? 'hasta' : 'cortado'} ${esc(r.fecha_fin)}</div>` : (r.activa ? '<div class="muted" style="font-size:11px">indefinido</div>' : '')}</td>
        <td class="right">${r.activa ? `
          <button class="btn ghost sm" data-editar="${r.id}">Editar</button>
          <button class="btn danger sm" data-cortar="${r.id}">Cortar</button>` : ''}</td></tr>`).join('')
        : `<tr><td colspan="7" class="empty">No hay cargos recurrentes. Créalos desde «Nueva compra» → tipo de gasto «Recurrente».</td></tr>`);
    tbl.querySelectorAll('[data-cortar]').forEach(b => b.onclick = () => cortarRecurrente(list.find(r => r.id == b.dataset.cortar)));
    tbl.querySelectorAll('[data-editar]').forEach(b => b.onclick = () => editarRecurrente(list.find(r => r.id == b.dataset.editar)));
  } catch (e) { tbl.innerHTML = `<tr><td class="empty">${esc(e.message)}</td></tr>`; }
}
function cortarRecurrente(r) {
  const m = modal(`<h3>Cortar cargo recurrente</h3>
    <p class="muted" style="margin-bottom:14px">«${esc(r.nombre)}» (${money(r.monto, r.moneda)}/mes) dejará de cobrarse. Las compras ya generadas quedan en el historial.</p>
    <div class="field"><label>Último mes que se cobra</label>
      <select id="ctModo"><option value="hoy">Desde ahora (no se cobra más)</option><option value="fecha">Elegir fecha de término</option></select></div>
    <div class="field hidden" id="ctFechaWrap"><label>Fecha de término</label><input id="ctFecha" type="date" value="${new Date().toISOString().slice(0, 10)}"></div>
    <div class="flex" style="margin-top:6px"><div class="spacer"></div>
      <button class="btn ghost" onclick="document.getElementById('modalRoot').innerHTML=''">Cancelar</button>
      <button class="btn danger" id="ctOk">Cortar</button></div>`);
  m.querySelector('#ctModo').onchange = e => m.querySelector('#ctFechaWrap').classList.toggle('hidden', e.target.value !== 'fecha');
  m.querySelector('#ctOk').onclick = async () => {
    const fechaFin = m.querySelector('#ctModo').value === 'fecha' ? m.querySelector('#ctFecha').value : null;
    try {
      await api('/api/compras/suscripciones/cortar', { method: 'POST', body: { id: r.id, fecha_fin: fechaFin } });
      closeModal(); toast('Cargo recurrente cortado', 'ok'); pintarRecurrentes();
    } catch (e) { toast(e.message, 'err'); }
  };
}
function editarRecurrente(r) {
  const m = modal(`<h3>Editar cargo recurrente</h3>
    <div class="field"><label>Nombre</label><input id="erNombre" value="${esc(r.nombre)}"></div>
    <div class="row c2">
      <div class="field"><label>Monto (${r.moneda})</label><input id="erMonto" type="number" step="any" min="0" value="${r.monto}"></div>
      <div class="field"><label>Día del mes</label><input id="erDia" type="number" min="1" max="31" value="${r.dia_mes}"></div>
    </div>
    <div class="field"><label>Notas</label><input id="erNotas" value="${esc(r.notas || '')}"></div>
    <p class="muted" style="font-size:12px">Para cambiar moneda, proveedor o cortarlo, usa los botones de la lista.</p>
    <div class="flex" style="margin-top:6px"><div class="spacer"></div>
      <button class="btn ghost" onclick="document.getElementById('modalRoot').innerHTML=''">Cancelar</button>
      <button class="btn gold" id="erOk">Guardar</button></div>`);
  m.querySelector('#erOk').onclick = async () => {
    try {
      await api('/api/compras/suscripciones/actualizar', { method: 'POST', body: {
        id: r.id, nombre: m.querySelector('#erNombre').value, monto: Number(m.querySelector('#erMonto').value) || 0,
        dia_mes: Number(m.querySelector('#erDia').value) || 1, notas: m.querySelector('#erNotas').value } });
      closeModal(); toast('Cargo recurrente actualizado ✓', 'ok'); pintarRecurrentes();
    } catch (e) { toast(e.message, 'err'); }
  };
}

/* ══════════════════ TAB: REPORTES ══════════════════ */
RENDER.reportes = async () => {
  const s = $('#tab-reportes');
  s.innerHTML = `
    <div class="card">
      <div class="flex wrap" style="margin-bottom:6px"><h2>Reportes de gasto</h2><div class="spacer"></div>
        <input type="date" id="rDesde" style="max-width:160px"><input type="date" id="rHasta" style="max-width:160px">
        <button class="btn ghost sm" id="rFiltrar">Filtrar</button>
        <button class="btn gold sm" id="rExport">⬇ Excel</button></div>
      <div id="rBody"></div>
    </div>`;
  const cargar = async () => {
    const q = new URLSearchParams();
    if ($('#rDesde').value) q.set('desde', $('#rDesde').value);
    if ($('#rHasta').value) q.set('hasta', $('#rHasta').value);
    try {
      const j = await api('/api/compras/reportes?' + q); const r = j.reporte;
      const porTipo = t => (r.por_tipo.find(x => x.label === t) || {}).total || 0;
      $('#rBody').innerHTML = `
        <div class="tiles">
          <div class="tile"><div class="n">${clp(r.total)}</div><div class="l">Gasto total</div></div>
          <div class="tile"><div class="n">${r.n_compras}</div><div class="l">Compras</div></div>
          <div class="tile"><div class="n">${clp(porTipo('fijo'))}</div><div class="l">Gastos fijos</div></div>
          <div class="tile"><div class="n">${clp(porTipo('variable'))}</div><div class="l">Gastos variables</div></div>
          <div class="tile"><div class="n">${clp(porTipo('recurrente'))}</div><div class="l">Recurrentes (mensual)</div></div>
        </div>
        ${barras('Por mes', r.por_mes, 'mes')}
        ${barras('Por categoría', r.por_categoria, 'label')}
        ${barras('Por proveedor', r.por_proveedor.slice(0, 12), 'label')}`;
    } catch (e) { toast(e.message, 'err'); }
  };
  $('#rFiltrar').onclick = cargar;
  $('#rExport').onclick = () => {
    const q = new URLSearchParams();
    if ($('#rDesde').value) q.set('desde', $('#rDesde').value);
    if ($('#rHasta').value) q.set('hasta', $('#rHasta').value);
    descargarXlsx('/api/compras/export.xlsx?' + q);
  };
  cargar();
};
function barras(titulo, data, key) {
  if (!data || !data.length) return '';
  const max = Math.max(1, ...data.map(d => d.total));
  return `<div class="field"><label>${titulo}</label>${data.map(d => `
    <div class="bar"><div class="lab">${esc(d[key])}</div>
      <div class="track"><div class="fill" style="width:${Math.round(d.total / max * 100)}%"></div></div>
      <div class="val">${clp(d.total)}</div></div>`).join('')}</div>`;
}
async function descargarXlsx(path) {
  try {
    const r = await fetch(API + path, { headers: { 'X-Compras-Token': TOKEN } });
    if (!r.ok) throw new Error('No se pudo exportar');
    const blob = await r.blob(); const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'compras.xlsx'; a.click(); URL.revokeObjectURL(url);
  } catch (e) { toast(e.message, 'err'); }
}

/* ══════════════════ TAB: ADMINISTRACIÓN ══════════════════ */
RENDER.admin = async () => {
  const s = $('#tab-admin');
  if (!puede('admin')) { s.innerHTML = soloLectura(); return; }
  s.innerHTML = `
    <div class="card"><div class="flex"><h2>Categorías de gasto</h2><div class="spacer"></div>
      <input id="adCatNueva" placeholder="Nueva categoría" style="max-width:220px">
      <button class="btn gold sm" id="adCatAdd">Agregar</button></div>
      <div id="adCats" style="margin-top:12px"></div></div>
    <div class="card"><div class="flex"><h2>Proveedores</h2><div class="spacer"></div>
      <input id="adProvNuevo" placeholder="Nuevo proveedor" style="max-width:220px">
      <button class="btn gold sm" id="adProvAdd">Agregar</button></div>
      <div class="tablewrap" style="margin-top:12px"><table id="adProvs"></table></div></div>
    <div class="card"><div class="flex"><h2>Usuarios</h2><div class="spacer"></div>
      <button class="btn gold sm" id="adUserAdd">➕ Usuario</button></div>
      <div class="tablewrap" style="margin-top:12px"><table id="adUsers"></table></div></div>`;

  // categorías
  const pintarCats = () => {
    $('#adCats').innerHTML = CACHE.categorias.map(c => `<span class="pill" style="background:var(--light-bg);margin:0 6px 6px 0;display:inline-flex;gap:6px;align-items:center">
      ${esc(c.nombre)} <a href="#" data-arch="${c.id}" title="Archivar" style="text-decoration:none">✕</a></span>`).join('') || '<span class="muted">Sin categorías.</span>';
    $$('#adCats [data-arch]').forEach(a => a.onclick = async e => { e.preventDefault();
      try { await api('/api/compras/categorias/actualizar', { method: 'POST', body: { id: +a.dataset.arch, archivada: true } });
        await recargarCaches(); pintarCats(); } catch (er) { toast(er.message, 'err'); } });
  };
  pintarCats();
  $('#adCatAdd').onclick = async () => {
    const n = $('#adCatNueva').value.trim(); if (!n) return;
    try { await api('/api/compras/categorias', { method: 'POST', body: { nombre: n } }); $('#adCatNueva').value = ''; await recargarCaches(); pintarCats(); }
    catch (e) { toast(e.message, 'err'); }
  };

  // proveedores
  const pintarProvs = () => {
    $('#adProvs').innerHTML = `<tr><th>Nombre</th><th>RUT</th><th>Contacto</th><th></th></tr>` +
      CACHE.proveedores.map(p => `<tr><td>${esc(p.nombre)}</td><td>${esc(p.rut || '—')}</td><td>${esc(p.contacto || '—')}</td>
        <td class="right"><button class="btn ghost sm" data-ed="${p.id}">Editar</button></td></tr>`).join('');
    $$('#adProvs [data-ed]').forEach(b => b.onclick = () => editarProveedor(CACHE.proveedores.find(p => p.id == b.dataset.ed)));
  };
  pintarProvs();
  $('#adProvAdd').onclick = async () => {
    const n = $('#adProvNuevo').value.trim(); if (!n) return;
    try { await api('/api/compras/proveedores', { method: 'POST', body: { nombre: n } }); $('#adProvNuevo').value = ''; await recargarCaches(); pintarProvs(); }
    catch (e) { toast(e.message, 'err'); }
  };

  // usuarios
  const pintarUsers = async () => {
    try { const j = await api('/api/compras/usuarios');
      $('#adUsers').innerHTML = `<tr><th>Nombre</th><th>Email</th><th>Rol</th><th>Estado</th><th></th></tr>` +
        j.usuarios.map(u => `<tr><td>${esc(u.nombre)}</td><td>${esc(u.email)}</td>
          <td><span class="rolechip">${u.rol}</span></td>
          <td>${u.activo ? '<span class="pill ok">activo</span>' : '<span class="pill low">inactivo</span>'}</td>
          <td class="right"><button class="btn ghost sm" data-eu="${u.id}" data-n="${esc(u.nombre)}" data-r="${u.rol}" data-a="${u.activo}">Editar</button></td></tr>`).join('');
      $$('#adUsers [data-eu]').forEach(b => b.onclick = () => editarUsuario(b.dataset));
    } catch (e) { toast(e.message, 'err'); }
  };
  pintarUsers();
  $('#adUserAdd').onclick = () => nuevoUsuario(pintarUsers);
};

function editarProveedor(p) {
  const m = modal(`<h3>Editar proveedor</h3>
    <div class="field"><label>Nombre</label><input id="epN" value="${esc(p.nombre)}"></div>
    <div class="row c2"><div class="field"><label>RUT</label><input id="epR" value="${esc(p.rut || '')}"></div>
      <div class="field"><label>Contacto</label><input id="epC" value="${esc(p.contacto || '')}"></div></div>
    <div class="field"><label>Notas</label><textarea id="epNotas" rows="2">${esc(p.notas || '')}</textarea></div>
    <div class="flex" style="margin-top:6px"><div class="spacer"></div>
      <button class="btn ghost" onclick="document.getElementById('modalRoot').innerHTML=''">Cancelar</button>
      <button class="btn gold" id="epOk">Guardar</button></div>`);
  m.querySelector('#epOk').onclick = async () => {
    try { await api('/api/compras/proveedores/actualizar', { method: 'POST', body: { id: p.id, nombre: m.querySelector('#epN').value, rut: m.querySelector('#epR').value, contacto: m.querySelector('#epC').value, notas: m.querySelector('#epNotas').value } });
      closeModal(); toast('Proveedor actualizado ✓', 'ok'); await recargarCaches(); RENDER.admin();
    } catch (e) { toast(e.message, 'err'); }
  };
}

function nuevoUsuario(despues) {
  const m = modal(`<h3>Nuevo usuario</h3>
    <div class="field"><label>Nombre</label><input id="nuN"></div>
    <div class="field"><label>Email</label><input id="nuE" type="email"></div>
    <div class="field"><label>Contraseña</label><input id="nuP" type="password"></div>
    <div class="field"><label>Rol</label><select id="nuR">${optsRoles('registro')}</select></div>
    <div class="flex" style="margin-top:6px"><div class="spacer"></div>
      <button class="btn ghost" onclick="document.getElementById('modalRoot').innerHTML=''">Cancelar</button>
      <button class="btn gold" id="nuOk">Crear</button></div>`);
  m.querySelector('#nuOk').onclick = async () => {
    try { await api('/api/compras/usuarios', { method: 'POST', body: { nombre: m.querySelector('#nuN').value, email: m.querySelector('#nuE').value, password: m.querySelector('#nuP').value, rol: m.querySelector('#nuR').value } });
      closeModal(); toast('Usuario creado ✓', 'ok'); despues();
    } catch (e) { toast(e.message, 'err'); }
  };
}
function editarUsuario(ds) {
  const m = modal(`<h3>Editar usuario</h3>
    <div class="field"><label>Nombre</label><input id="euN" value="${esc(ds.n)}"></div>
    <div class="field"><label>Rol</label><select id="euR">${optsRoles(ds.r)}</select></div>
    <div class="field"><label>Estado</label><select id="euA"><option value="1" ${ds.a == 'true' || ds.a == '1' ? 'selected' : ''}>Activo</option><option value="0" ${ds.a == 'false' || ds.a == '0' ? 'selected' : ''}>Inactivo</option></select></div>
    <div class="field"><label>Nueva contraseña (opcional)</label><input id="euP" type="password" placeholder="Dejar vacío para no cambiar"></div>
    <div class="flex" style="margin-top:6px"><div class="spacer"></div>
      <button class="btn ghost" onclick="document.getElementById('modalRoot').innerHTML=''">Cancelar</button>
      <button class="btn gold" id="euOk">Guardar</button></div>`);
  m.querySelector('#euOk').onclick = async () => {
    try { const body = { id: +ds.eu, nombre: m.querySelector('#euN').value, rol: m.querySelector('#euR').value, activo: m.querySelector('#euA').value === '1' };
      const pw = m.querySelector('#euP').value; if (pw) body.password = pw;
      await api('/api/compras/usuarios/actualizar', { method: 'POST', body });
      closeModal(); toast('Usuario actualizado ✓', 'ok'); RENDER.admin();
    } catch (e) { toast(e.message, 'err'); }
  };
}

function soloLectura() {
  return `<div class="card empty">No tienes permiso para esta sección.<br><span class="muted">Tu rol es «${esc(ME?.rol || '')}».</span></div>`;
}

// arrancar
boot();
