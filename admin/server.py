"""
Panel de Administración — Ortodoncia Richard
Ejecutar: python admin/server.py
Abrir: http://localhost:5001
"""

import os
import re
import hmac
import json
import hashlib
import secrets
import shutil
import subprocess
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from bs4 import BeautifulSoup

app = Flask(__name__, static_folder='.')

# Limite de tamaño del cuerpo de la petición (anti-DoS): la firma llega como PNG
# en base64; 3 MB es holgado para una firma y frena payloads gigantes.
app.config['MAX_CONTENT_LENGTH'] = 3 * 1024 * 1024

# Detrás del proxy de Render: hacer que request.remote_addr sea la IP real del
# cliente (la que Render pone en X-Forwarded-For), en vez de la IP del proxy.
# x_for=1 = confiamos en UN solo proxy (Render). Así el rate-limit por IP y el
# sello de firma electrónica usan la IP real y NO una X-Forwarded-For falsificada
# por el cliente (werkzeug toma la entrada que agregó el proxy de confianza).
try:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
except ImportError:
    pass

# Rate limiting: frena abusos/bots que podrian disparar el trafico (y el costo).
# Limite global por IP + limites mas estrictos en endpoints que llaman a DentiDesk.
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    limiter = Limiter(get_remote_address, app=app,
                      default_limits=["600 per hour", "60 per minute"],
                      storage_uri="memory://")
except ImportError:
    limiter = None

def rate_limit(spec):
    """Decorador de limite por endpoint (no-op si flask-limiter no esta)."""
    def deco(f):
        return limiter.limit(spec)(f) if limiter else f
    return deco

# CORS: sitio publicado + localhost (el panel admin corre local y consulta el
# backend de Render para stats/consentimientos). Los orígenes localhost son de
# bajo riesgo porque TODOS los endpoints sensibles exigen ADMIN_TOKEN/KIOSK_TOKEN,
# y CORS solo controla lectura desde JS del navegador, no llamadas servidor a
# servidor. La whitelist es exacta (no comodines).
_ALLOWED_ORIGINS = {
    'https://delreal90.github.io',
    'https://ortodonciarichard.cl',
    'https://www.ortodonciarichard.cl',
    'http://localhost:3000', 'http://127.0.0.1:3000',
    'http://localhost:3050', 'http://127.0.0.1:3050',
    'http://localhost:5001', 'http://127.0.0.1:5001',
}
@app.after_request
def _cors(resp):
    origin = request.headers.get('Origin', '')
    if origin in _ALLOWED_ORIGINS:
        resp.headers['Access-Control-Allow-Origin'] = origin
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Admin-Token, X-Kiosk-Token, X-Compras-Token, X-Print-Token'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return resp

@app.route('/api/<path:_any>', methods=['OPTIONS'])
def _cors_preflight(_any):
    return ('', 204)

BASE = Path(__file__).parent.parent  # carpeta ortodonciarichard/
INDEX = BASE / 'index.html'
IMAGES = BASE / 'images'
MAINJS = BASE / 'js' / 'main.js'

EN_RENDER = bool(os.environ.get('RENDER'))

# Rutas de administración bloqueadas en producción (Render solo expone /api/agenda/*)
# ⚠️ UN SOLO mecanismo: agregar el path ACÁ, nunca un `if EN_RENDER` suelto dentro de la
# función. Tener dos mecanismos fue exactamente lo que dejó /api/upload sin ninguno de los
# dos (abierto en producción). Si agregas una ruta de administración, súmala a este set.
RUTAS_SOLO_LOCAL = {'/api/info', '/api/equipo', '/api/casos', '/api/faq',
                    '/api/doctores', '/api/equipo/agregar', '/api/equipo/eliminar',
                    '/api/publicar', '/api/scheduling-config', '/api/upload',
                    '/api/galeria', '/api/galeria/agregar', '/api/galeria/eliminar',
                    '/api/galeria/renombrar', '/api/galeria/reordenar'}

# Extensiones aceptadas al subir un archivo por /api/upload.
EXTENSIONES_SUBIDA = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.mp4', '.webm'}

@app.before_request
def bloquear_admin_en_produccion():
    if EN_RENDER and request.path in RUTAS_SOLO_LOCAL:
        return jsonify({'error': 'No disponible en producción'}), 403

# ── Utilidades ─────────────────────────────────────────────────────────────

def read_html():
    return BeautifulSoup(INDEX.read_text(encoding='utf-8'), 'html.parser')

def write_html(soup):
    INDEX.write_text(str(soup), encoding='utf-8')

def arg_int(nombre, default=0, minimo=None, maximo=None):
    """Lee un parametro numerico de la query string sin reventar. Un '?offset=abc'
    lanzaba ValueError y Flask devolvia su pagina HTML de error 500 en medio del
    flujo de agendar hora — el frontend espera JSON y se rompia."""
    try:
        v = int(request.args.get(nombre, default) or default)
    except (TypeError, ValueError):
        v = default
    if minimo is not None:
        v = max(minimo, v)
    if maximo is not None:
        v = min(maximo, v)
    return v

# ── Rutas estáticas ─────────────────────────────────────────────────────────

@app.route('/')
def index():
    # En producción (Render) solo exponemos la API. El panel es solo local.
    if os.environ.get('RENDER'):
        return jsonify({'ok': True, 'servicio': 'Ortodoncia Richard API'}), 200
    return send_from_directory('.', 'panel.html')

@app.route('/images/<path:filename>')
def serve_image(filename):
    if os.environ.get('RENDER'):
        return jsonify({'error': 'No disponible en producción'}), 403
    return send_from_directory(str(IMAGES), filename)

# ══════════════════════════════════════════════════════════════════════════════
# 1. FOTOS — subir imágenes
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/upload', methods=['POST'])
def upload():
    f = request.files.get('file')
    target = request.form.get('target', '')  # ej: "dr-alberto-del-real.jpeg"
    if not f or not target:
        return jsonify({'ok': False, 'error': 'Faltan datos'})
    # `target` viene del navegador: sanear SIEMPRE antes de tocar el disco. Sin esto un
    # target con '../' escribe fuera de images/ (la carpeta del sitio entero).
    nombre = secure_filename(target)
    if not nombre or Path(nombre).suffix.lower() not in EXTENSIONES_SUBIDA:
        return jsonify({'ok': False, 'error': 'Nombre o formato de archivo no permitido'}), 400
    dest = (IMAGES / nombre).resolve()
    if dest.parent != IMAGES.resolve():   # cinturón y tirantes
        return jsonify({'ok': False, 'error': 'Ruta inválida'}), 400
    f.save(str(dest))
    return jsonify({'ok': True, 'path': f'images/{nombre}'})

# ══════════════════════════════════════════════════════════════════════════════
# 2. INFO CLÍNICA — leer y editar textos básicos
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/info', methods=['GET'])
def get_info():
    soup = read_html()
    data = {}
    # Teléfono
    tel = soup.find('a', href=re.compile(r'tel:'))
    data['telefono'] = tel.get_text(strip=True) if tel else ''
    # WhatsApp
    wa = soup.find('a', href=re.compile(r'wa\.me'))
    data['whatsapp'] = wa['href'].split('wa.me/')[1].split('?')[0] if wa else ''
    # Horario
    horario = soup.find(string=re.compile(r'Lunes a Viernes'))
    data['horario'] = horario.strip() if horario else ''
    # Dirección
    addr = soup.find('address')
    data['direccion'] = addr.get_text(' ', strip=True) if addr else ''
    return jsonify(data)

@app.route('/api/info', methods=['POST'])
def set_info():
    data = request.json
    content = INDEX.read_text(encoding='utf-8')

    if data.get('horario'):
        content = re.sub(
            r'Lunes a Viernes[^<]*',
            data['horario'],
            content
        )
    if data.get('telefono'):
        content = re.sub(
            r'(<a[^>]*tel:[^>]*>)[^<]*(</a>)',
            rf'\g<1>{data["telefono"]}\g<2>',
            content
        )
    INDEX.write_text(content, encoding='utf-8')
    return jsonify({'ok': True})

# ══════════════════════════════════════════════════════════════════════════════
# 3. EQUIPO — leer y editar nombres/roles del staff
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/equipo', methods=['GET'])
def get_equipo():
    soup = read_html()
    result = []
    for tab_id in ['tab-especialistas', 'tab-secretaria', 'tab-asistentes', 'tab-laboratorio']:
        tab = soup.find(id=tab_id)
        if not tab:
            continue
        for card in tab.find_all(class_='team-card'):
            info = card.find(class_='team-info')
            photo_div = card.find(class_='team-photo')
            img = photo_div.find('img') if photo_div else None
            placeholder = photo_div.get('data-initials') if photo_div else None
            member = {
                'tab': tab_id.replace('tab-', ''),
                'nombre': info.find('h3').get_text(strip=True) if info and info.find('h3') else '',
                'rol': info.find(class_='team-role').get_text(strip=True) if info and info.find(class_='team-role') else '',
                'foto': img['src'] if img else None,
                'placeholder': placeholder,
            }
            result.append(member)
    return jsonify(result)

@app.route('/api/equipo', methods=['POST'])
def set_equipo():
    """Actualiza nombre, rol y foto de un miembro del equipo"""
    data = request.json
    # data: {tab, nombre_actual, nombre_nuevo, rol_nuevo, foto_nueva}
    soup = read_html()
    tab = soup.find(id=f'tab-{data["tab"]}')
    if not tab:
        return jsonify({'ok': False, 'error': 'Tab no encontrado'})

    for card in tab.find_all(class_='team-card'):
        info = card.find(class_='team-info')
        h3 = info.find('h3') if info else None
        if h3 and h3.get_text(strip=True) == data.get('nombre_actual', ''):
            if data.get('nombre_nuevo'):
                h3.string = data['nombre_nuevo']
            rol_el = info.find(class_='team-role')
            if rol_el and data.get('rol_nuevo'):
                rol_el.string = data['rol_nuevo']
            if data.get('foto_nueva'):
                photo_div = card.find(class_='team-photo')
                if photo_div:
                    img = photo_div.find('img')
                    if img:
                        img['src'] = data['foto_nueva']
                        img['alt'] = data.get('nombre_nuevo', data['nombre_actual'])
                    else:
                        # Era placeholder — reemplazar por img real
                        photo_div.clear()
                        photo_div['class'] = ['team-photo']
                        if photo_div.get('data-initials'):
                            del photo_div['data-initials']
                        new_img = BeautifulSoup(
                            f'<img src="{data["foto_nueva"]}" alt="{data.get("nombre_nuevo", data["nombre_actual"])}">',
                            'html.parser'
                        )
                        photo_div.append(new_img)
            break

    write_html(soup)

    # Si es doctor (tab especialistas), sincronizar también en doctorData de main.js
    tab_id = data.get('tab', '')
    if tab_id == 'especialistas':
        doctors = read_doctor_data()
        nombre_buscar = data.get('nombre_nuevo') or data.get('nombre_actual', '')
        doc = next((d for d in doctors.values() if d.get('name') == nombre_buscar), None)
        if doc:
            if data.get('nombre_nuevo'):
                doc['name'] = data['nombre_nuevo']
            if data.get('rol_nuevo'):
                doc['role'] = data['rol_nuevo']
            if data.get('foto_nueva'):
                doc['photo'] = data['foto_nueva']
            write_doctor_data(doctors)

    return jsonify({'ok': True})

# ══════════════════════════════════════════════════════════════════════════════
# 4. CASOS CLÍNICOS — leer, editar y agregar
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/casos', methods=['GET'])
def get_casos():
    soup = read_html()
    section = soup.find(id='tratamientos')
    result = []
    if section:
        for card in section.find_all(class_='treatment-card'):
            if 'treatment-card-cta' in card.get('class', []):
                continue
            img = card.find('img')
            h3 = card.find('h3')
            p = card.find('p')
            result.append({
                'foto': img['src'] if img else '',
                'titulo': h3.get_text(strip=True) if h3 else '',
                'descripcion': p.get_text(strip=True) if p else '',
            })
    return jsonify(result)

@app.route('/api/casos', methods=['POST'])
def set_casos():
    """Agrega un caso nuevo o actualiza uno existente"""
    data = request.json
    soup = read_html()
    section = soup.find(id='tratamientos')
    grid = section.find(class_='treatments-grid') if section else None
    if not grid:
        return jsonify({'ok': False, 'error': 'No se encontró la grilla de tratamientos'})

    if data.get('accion') == 'agregar':
        # Insertar antes del card CTA
        cta = grid.find(class_='treatment-card-cta')
        new_card = BeautifulSoup(f'''
        <div class="treatment-card reveal">
            <div class="treatment-image">
                <img src="{data['foto']}" alt="{data['titulo']}">
                <div class="treatment-overlay"><span>Caso clínico</span></div>
            </div>
            <div class="treatment-body">
                <h3>{data['titulo']}</h3>
                <p>{data['descripcion']}</p>
            </div>
        </div>''', 'html.parser')
        if cta:
            cta.insert_before(new_card)
        else:
            grid.append(new_card)

    elif data.get('accion') == 'editar':
        for card in grid.find_all(class_='treatment-card'):
            h3 = card.find('h3')
            if h3 and h3.get_text(strip=True) == data.get('titulo_actual'):
                if data.get('titulo'): h3.string = data['titulo']
                p = card.find('p')
                if p and data.get('descripcion'): p.string = data['descripcion']
                img = card.find('img')
                if img and data.get('foto'):
                    img['src'] = data['foto']
                    img['alt'] = data.get('titulo', data['titulo_actual'])
                break

    elif data.get('accion') == 'eliminar':
        for card in grid.find_all(class_='treatment-card'):
            h3 = card.find('h3')
            if h3 and h3.get_text(strip=True) == data.get('titulo'):
                card.decompose()
                break

    write_html(soup)
    return jsonify({'ok': True})

# ══════════════════════════════════════════════════════════════════════════════
# 5. FAQ — leer, editar, agregar, eliminar
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/faq', methods=['GET'])
def get_faq():
    soup = read_html()
    result = []
    for tab_content in soup.find_all(class_='faq-content'):
        tab_id = tab_content.get('id', '')
        for item in tab_content.find_all(class_='acc-item'):
            btn = item.find(class_='acc-btn')
            body = item.find(class_='acc-body')
            if btn and body:
                pregunta = btn.get_text(strip=True)
                # quitar el texto del ícono chevron si está como texto
                pregunta = re.sub(r'\s*$', '', pregunta).strip()
                result.append({
                    'tab': tab_id,
                    'pregunta': pregunta,
                    'respuesta': body.get_text(' ', strip=True),
                })
    return jsonify(result)

@app.route('/api/faq', methods=['POST'])
def set_faq():
    data = request.json
    soup = read_html()

    if data.get('accion') == 'agregar':
        tab_content = soup.find(id=data['tab'])
        if not tab_content:
            return jsonify({'ok': False, 'error': 'Tab no encontrado'})
        new_item = BeautifulSoup(f'''
        <div class="acc-item">
            <button class="acc-btn">{data['pregunta']} <i class="fas fa-chevron-down"></i></button>
            <div class="acc-body"><p>{data['respuesta']}</p></div>
        </div>''', 'html.parser')
        tab_content.append(new_item)

    elif data.get('accion') == 'editar':
        for item in soup.find_all(class_='acc-item'):
            btn = item.find(class_='acc-btn')
            if btn and data['pregunta_actual'] in btn.get_text():
                btn.clear()
                btn.append(BeautifulSoup(f'{data["pregunta_nueva"]} <i class="fas fa-chevron-down"></i>', 'html.parser'))
                body = item.find(class_='acc-body')
                if body:
                    body.clear()
                    body.append(BeautifulSoup(f'<p>{data["respuesta"]}</p>', 'html.parser'))
                break

    elif data.get('accion') == 'eliminar':
        for item in soup.find_all(class_='acc-item'):
            btn = item.find(class_='acc-btn')
            if btn and data['pregunta'] in btn.get_text():
                item.decompose()
                break

    write_html(soup)
    return jsonify({'ok': True})

# ══════════════════════════════════════════════════════════════════════════════
# 6. CV DOCTORES — leer y editar doctorData en main.js
# ══════════════════════════════════════════════════════════════════════════════

def read_doctor_data():
    """Extrae doctorData de main.js parseando campo a campo"""
    js = MAINJS.read_text(encoding='utf-8')
    result = {}

    # Extraer el bloque completo entre "const doctorData = {" y el cierre "};"
    start = js.find('const doctorData = {')
    if start == -1:
        return {}
    # Encontrar el cierre balanceado
    depth = 0
    end = start
    for i, ch in enumerate(js[start:], start):
        if ch == '{': depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    block = js[start:end]

    # Detectar todos los IDs de doctores dinámicamente
    doc_ids = re.findall(r'\n    (\w+):\s*\{', block)

    for doc_id in doc_ids:
        # Extraer el sub-bloque de cada doctor
        d_start = block.find(f'\n    {doc_id}: {{')
        if d_start == -1:
            continue
        d_depth = 0
        d_end = d_start
        for i, ch in enumerate(block[d_start:], d_start):
            if ch == '{': d_depth += 1
            elif ch == '}':
                d_depth -= 1
                if d_depth == 0:
                    d_end = i + 1
                    break
        doc_block = block[d_start:d_end]

        def get_str(field, b=doc_block):
            r = re.search(rf"{field}:\s*'((?:[^'\\]|\\.)*)'", b)
            return r.group(1).replace("\\'", "'") if r else ''

        def get_arr(field, b=doc_block):
            r = re.search(rf"{field}:\s*\[(.*?)\]", b, re.DOTALL)
            if not r: return []
            return [i.replace("\\'", "'") for i in re.findall(r"'((?:[^'\\]|\\.)*)'", r.group(1))]

        result[doc_id] = {
            'name':        get_str('name'),
            'role':        get_str('role'),
            'photo':       get_str('photo'),
            'bio':         get_str('bio'),
            'memberships': get_arr('memberships'),
            'education':   get_arr('education'),
            'specialties': get_arr('specialties'),
        }
    return result

def write_doctor_data(data):
    """Reemplaza doctorData en main.js con los nuevos datos"""
    js = MAINJS.read_text(encoding='utf-8')

    def to_js_array(lst):
        items = ',\n            '.join(f"'{item}'" for item in lst)
        return f'[\n            {items},\n        ]'

    entries = []
    for key, d in data.items():
        education = to_js_array(d.get('education', []))
        specialties = to_js_array(d.get('specialties', []))
        memberships = to_js_array(d.get('memberships', []))
        bio = d.get('bio', '').replace("'", "\\'")
        entry = f"""    {key}: {{
        name:        '{d.get("name", "")}',
        role:        '{d.get("role", "")}',
        photo:       '{d.get("photo", "")}',
        memberships: {memberships},
        bio:         '{bio}',
        education: {education},
        specialties: {specialties},
    }}"""
        entries.append(entry)

    new_block = 'const doctorData = {\n' + ',\n'.join(entries) + '\n};\n'
    js = re.sub(r'const doctorData\s*=\s*\{.*?\};\s*\n', new_block, js, flags=re.DOTALL)
    MAINJS.write_text(js, encoding='utf-8')

@app.route('/api/doctores', methods=['GET'])
def get_doctores():
    return jsonify(read_doctor_data())

@app.route('/api/doctores', methods=['POST'])
def set_doctores():
    data = request.json
    # data: {id: 'alberto', campo: 'bio'|'education'|'specialties'|'memberships'|'name'|'role', valor: ...}
    doctors = read_doctor_data()
    doc_id = data.get('id')
    if doc_id not in doctors:
        return jsonify({'ok': False, 'error': 'Doctor no encontrado'})
    campo = data.get('campo')
    valor = data.get('valor')
    doctors[doc_id][campo] = valor
    write_doctor_data(doctors)

    # Si cambiaron membresías, sincronizar badges en index.html
    if campo == 'memberships':
        soup = read_html()
        card = soup.find(attrs={'data-doctor-id': doc_id})
        if card:
            badges_div = card.find(class_='team-badges')
            if badges_div:
                badges_div.clear()
                for m in (valor if isinstance(valor, list) else []):
                    span = soup.new_tag('span', attrs={'class': 'team-badge'})
                    span.string = m
                    badges_div.append(span)
                write_html(soup)

    return jsonify({'ok': True})

# ══════════════════════════════════════════════════════════════════════════════
# 7. EQUIPO — agregar y eliminar miembros del staff
# ══════════════════════════════════════════════════════════════════════════════

TAB_ROLES = {
    'secretaria': 'Secretaria',
    'asistentes': 'Asistente Dental',
    'laboratorio': 'Laboratorio y Aseo',
}

@app.route('/api/equipo/agregar', methods=['POST'])
def agregar_miembro():
    data = request.json
    # data: {tab, nombre, rol}
    soup = read_html()
    tab = soup.find(id=f'tab-{data["tab"]}')
    if not tab:
        return jsonify({'ok': False, 'error': 'Tab no encontrado'})

    grid = tab.find(class_='team-grid') or tab.find(class_='team-grid team-grid-small')
    # Calcular siguiente placeholder
    existing = tab.find_all(class_='team-card')
    count = len(existing) + 1
    prefix = data['tab'][0].upper()  # S, A, L

    foto_html = ''
    if data.get('foto'):
        foto_html = f'<img src="{data["foto"]}" alt="{data["nombre"]}">'
    else:
        foto_html = '<div class="initials-circle"></div>'
        placeholder_attr = f'data-initials="{prefix}{count}"'

    if data.get('foto'):
        photo_div = f'<div class="team-photo">{foto_html}</div>'
    else:
        photo_div = f'<div class="team-photo placeholder-photo" {placeholder_attr}>{foto_html}</div>'

    if data['tab'] == 'especialistas':
        doc_id = re.sub(r'[^a-z]', '', data['nombre'].lower().split()[-1])
        card_extra = f'doctor-card" data-doctor-id="{doc_id}" role="button" tabindex="0'
        badges_html = '<div class="team-badges"></div>'
    else:
        card_extra = ''
        badges_html = ''

    new_card = BeautifulSoup(f'''
    <div class="team-card reveal {card_extra}">
        {photo_div}
        <div class="team-info">
            <h3>{data["nombre"]}</h3>
            <p class="team-role">{data.get("rol", TAB_ROLES.get(data["tab"], ""))}</p>
            {badges_html}
        </div>
    </div>''', 'html.parser')

    if grid:
        grid.append(new_card)
    else:
        tab.append(new_card)

    write_html(soup)

    # Si es especialista, agregar entrada básica en doctorData (main.js)
    if data['tab'] == 'especialistas':
        doc_id = re.sub(r'[^a-z]', '', data['nombre'].lower().split()[-1])  # apellido como key
        doctors = read_doctor_data()
        if doc_id not in doctors:
            doctors[doc_id] = {
                'name': data['nombre'],
                'role': data.get('rol', 'Ortodoncista'),
                'photo': data.get('foto', f'images/doctor-placeholder.jpg'),
                'memberships': [],
                'bio': '',
                'education': [],
                'specialties': [],
            }
            write_doctor_data(doctors)

    return jsonify({'ok': True})

@app.route('/api/equipo/eliminar', methods=['POST'])
def eliminar_miembro():
    data = request.json
    # data: {tab, nombre}
    soup = read_html()
    tab = soup.find(id=f'tab-{data["tab"]}')
    if not tab:
        return jsonify({'ok': False, 'error': 'Tab no encontrado'})

    for card in tab.find_all(class_='team-card'):
        info = card.find(class_='team-info')
        h3 = info.find('h3') if info else None
        if h3 and h3.get_text(strip=True) == data['nombre']:
            card.decompose()
            write_html(soup)

            # Si es especialista, eliminar también de doctorData (main.js)
            if data['tab'] == 'especialistas':
                doctors = read_doctor_data()
                # Buscar por nombre exacto
                to_delete = next((k for k, v in doctors.items() if v.get('name') == data['nombre']), None)
                if to_delete:
                    del doctors[to_delete]
                    write_doctor_data(doctors)

            return jsonify({'ok': True})

    return jsonify({'ok': False, 'error': 'Miembro no encontrado'})

# ══════════════════════════════════════════════════════════════════════════════
# 8. GIT — publicar cambios a GitHub
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/publicar', methods=['POST'])
def publicar():
    data = request.json
    msg = data.get('mensaje', 'Actualización desde panel admin')
    try:
        subprocess.run(['git', 'add', '.'], cwd=str(BASE), check=True)
        commit = subprocess.run(['git', 'commit', '-m', msg], cwd=str(BASE), capture_output=True, text=True)
        if commit.returncode != 0:
            if 'nothing to commit' in commit.stdout or 'nothing to commit' in commit.stderr:
                return jsonify({'ok': True, 'detalle': 'No hay cambios nuevos para publicar.'})
            return jsonify({'ok': False, 'error': commit.stderr or commit.stdout})
        result = subprocess.run(['git', 'push'], cwd=str(BASE), capture_output=True, text=True)
        if result.returncode == 0:
            return jsonify({'ok': True, 'detalle': 'Publicado en GitHub Pages ✓'})
        else:
            return jsonify({'ok': False, 'error': result.stderr})
    except subprocess.CalledProcessError as e:
        return jsonify({'ok': False, 'error': str(e)})

# ══════════════════════════════════════════════════════════════════════════════
# 9. AGENDA DENTIDESK — disponibilidad, reserva y configuracion
# ══════════════════════════════════════════════════════════════════════════════

# Asegurar que la carpeta admin/ este en el path (gunicorn en Render
# puede ejecutar desde otra carpeta de trabajo).
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fechas       # hoy_chile()/ahora_chile(). Render corre en UTC: un date.today()
                    # pelado ya es MANIANA entre las 20:00 y medianoche hora Chile.
import scheduling
import dentidesk
import notify
import wa_cloud
import recordatorios_wa
import webhook_wa
import recaptacion
import control_dental
import nps
from datetime import date, datetime, timedelta

_DIAS = ['Lunes', 'Martes', 'Miercoles', 'Jueves', 'Viernes', 'Sabado', 'Domingo']
_MESES = ['', 'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
          'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']

def _fecha_legible(d):
    return f'{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month]}'

@app.route('/api/agenda/config', methods=['GET'])
def agenda_config():
    """Datos que el frontend necesita para armar el flujo: motivos + doctores
    (con su foto tomada de doctorData en main.js)."""
    cfg = scheduling.load_config()
    doctors = read_doctor_data()

    # Especialidades = las que tienen al menos un doctor que atiende online.
    activas = {dc['especialidad'] for k, dc in cfg['doctores'].items()
               if not k.startswith('_') and isinstance(dc, dict)
               and dc.get('atiende') and dc.get('especialidad')}
    especialidades = [{'key': k, 'label': v['label']}
                      for k, v in cfg['especialidades'].items()
                      if not k.startswith('_') and isinstance(v, dict) and k in activas]

    motivos = [{'key': k, 'label': v['label'], 'urgencia': v['urgencia'],
                'especialidad': v.get('especialidad', ''),
                # Flujos especiales (Estudio Integral): motivos ocultos del menu
                # + entrada compuesta que agenda 2 citas con separacion minima.
                'oculto': bool(v.get('oculto')),
                'compuesto': v.get('compuesto') or None,
                'separacion_min_dias': v.get('separacion_min_dias'),
                'solo_pacientes_existentes': bool(v.get('solo_pacientes_existentes'))}
               for k, v in cfg['motivos'].items()
               if not k.startswith('_') and isinstance(v, dict)]
    doctores = []
    for doc_id, dc in cfg['doctores'].items():
        if doc_id.startswith('_') or not isinstance(dc, dict) or not dc.get('atiende'):
            continue
        info = doctors.get(doc_id, {})
        doctores.append({
            'key': doc_id,
            'name': info.get('name', doc_id.title()),
            'role': info.get('role', ''),
            'photo': info.get('photo', ''),
            'especialidad': dc.get('especialidad', ''),
        })
    return jsonify({'especialidades': especialidades, 'motivos': motivos,
                    'doctores': doctores, 'mock': not cfg['dentidesk']['enabled'],
                    'turnstile_sitekey': os.environ.get('TURNSTILE_SITEKEY', ''),
                    'sabias_que': [s for s in (cfg.get('sabias_que') or []) if isinstance(s, str) and s.strip()]})

@rate_limit('40 per minute')
@app.route('/api/agenda/paciente', methods=['GET'])
def agenda_paciente():
    """Valida el RUT y lo cruza con DentiDesk. Devuelve si existe + datos precargados."""
    rut = request.args.get('rut', '')
    if not scheduling.rut_valido(rut):
        return jsonify({'ok': False, 'error': 'RUT invalido'}), 400
    info = dentidesk.buscar_paciente(rut)
    return jsonify({'ok': True, 'rut': scheduling.formatear_rut(rut),
                    'existe': info['existe'], 'datos': info['datos']})

import threading as _threading

# Cache de slots libres por (doctor, fecha) -- NO por motivo. Una sola llamada
# getAvailableHours (con el motivo mas corto como referencia) define los bloques
# de 15 min libres del doctor ese dia; las horas de CADA motivo se derivan
# localmente por duracion (dentidesk.horas_que_caben, validado contra DentiDesk).
# Asi, cambiar de motivo o de doctor no vuelve a preguntar a DentiDesk.
# Estrategia "stale-while-revalidate": si el cache esta algo viejo (> TTL) pero no
# demasiado (< MAX_STALE), se devuelve al instante y se refresca en segundo plano.
# Ademas _loop_calentador() lo mantiene tibio, y cada reserva lo refresca.
# (La reserva SIEMPRE valida contra datos frescos, no contra este cache.)
_SLOTS_CACHE = {}
_SLOTS_TTL = 300          # 5 min: las horas a futuro cambian lento
_SLOTS_MAX_STALE = 1800   # 30 min: mas alla de esto NO servir viejo (traer sincrono)
_SLOTS_INFLIGHT = set()
_SLOTS_LOCK = _threading.Lock()

# Límite GLOBAL de consultas simultáneas a DentiDesk. Sin esto, varios pacientes
# (o la precarga) disparan decenas de llamadas a la vez y la instancia 0.5 CPU +
# DentiDesk se saturan: cada request frío salta de ~6s a ~16s. Con el tope, las
# llamadas se ordenan y ninguna se ahoga.
_DENTI_SEM = _threading.BoundedSemaphore(10)

def _slots15_dia(doctor, d, cfg, force=False):
    """Bloques de 15 min libres del doctor ese dia, cacheados (SWR)."""
    import time as _t
    key = (doctor, d.isoformat())
    hit = _SLOTS_CACHE.get(key)
    if not force and hit and (_t.time() - hit[0]) < _SLOTS_MAX_STALE:
        if (_t.time() - hit[0]) >= _SLOTS_TTL:
            with _SLOTS_LOCK:
                if key not in _SLOTS_INFLIGHT:
                    _SLOTS_INFLIGHT.add(key)
                    def job():
                        try:
                            _slots15_dia(doctor, d, cfg, force=True)
                        except Exception:
                            pass
                        finally:
                            _SLOTS_INFLIGHT.discard(key)
                    _threading.Thread(target=job, daemon=True).start()
        return hit[1]
    with _DENTI_SEM:
        slots = dentidesk.bloques_libres_15(cfg, doctor, d)
    _SLOTS_CACHE[key] = (_t.time(), slots)
    return slots

def _horas_de_dia(doctor, motivo, d, cfg):
    """Horas ofrecibles para (doctor, motivo, dia): slots cacheados del doctor
    + derivacion local por duracion + reglas locales (ocupacion, anticipacion)."""
    if not cfg['dentidesk']['enabled']:
        # Modo demo: grilla simulada, sin cache.
        libres, ocupados = dentidesk.disponibilidad_real(doctor, d, motivo, cfg)
        return scheduling.horas_disponibles(doctor, d, motivo, libres, ocupados, cfg)
    libres15 = _slots15_dia(doctor, d, cfg)
    libres = dentidesk.horas_que_caben(libres15, cfg['motivos'][motivo]['duracion_min'])
    with _DENTI_SEM:
        ocupados = dentidesk.bloques_ocupados(cfg, doctor, d)
    return scheduling.horas_disponibles(doctor, d, motivo, libres, ocupados, cfg)

def _horas_de_dia_libre(doctor, duracion_min, d, cfg):
    """Como _horas_de_dia(), pero por DURACION en vez de motivo_key -- la usa
    el reagendamiento, donde el motivo original de la cita puede no estar en
    la lista de motivos agendables online (ver dentidesk.id_reason_por_label)."""
    if not cfg['dentidesk']['enabled']:
        # Modo demo: misma grilla/hash que dentidesk.disponibilidad_real() en
        # modo mock, pero sin necesitar un motivo_key real.
        manana = [f'{h:02d}:{m:02d}' for h in range(9, 13) for m in (0, 15, 30, 45)]
        tarde  = [f'{h:02d}:{m:02d}' for h in range(15, 19) for m in (0, 15, 30, 45)]
        worked = manana + tarde
        ocupados = {h for h in worked if dentidesk._hash01(doctor, d.isoformat(), h, 'real') < 0.25}
        libres = {h for h in worked if h not in ocupados}
        return scheduling.horas_disponibles_libre(doctor, d, libres, ocupados, cfg)
    libres15 = _slots15_dia(doctor, d, cfg)
    libres = dentidesk.horas_que_caben(libres15, duracion_min)
    with _DENTI_SEM:
        ocupados = dentidesk.bloques_ocupados(cfg, doctor, d)
    return scheduling.horas_disponibles_libre(doctor, d, libres, ocupados, cfg)

def _refrescar_dia_reservado(doctor, d, cfg):
    """Tras crear una cita, refresca en segundo plano los slots del doctor y la
    agenda de ese dia -- la hora recien tomada desaparece al instante para el
    siguiente paciente, sin esperar el TTL ni el calentador."""
    if not cfg['dentidesk']['enabled']:
        return
    def job():
        try:
            dentidesk._get_agenda_day(cfg, d, force=True)
            _slots15_dia(doctor, d, cfg, force=True)
        except Exception:
            pass
    _threading.Thread(target=job, daemon=True).start()

@rate_limit('30 per minute')
@app.route('/api/agenda/disponibilidad', methods=['GET'])
def agenda_disponibilidad():
    """Horas disponibles para (doctor, motivo) en los proximos dias habiles.
    Consulta los dias en paralelo (cada dia es una llamada a DentiDesk)."""
    from concurrent.futures import ThreadPoolExecutor

    doctor = request.args.get('doctor')
    motivo = request.args.get('motivo')
    cfg = scheduling.load_config()
    if doctor not in cfg['doctores'] or motivo not in cfg['motivos']:
        return jsonify({'ok': False, 'error': 'Parametros invalidos'}), 400

    hoy = fechas.hoy_chile()
    todos = scheduling.dias_habiles_ventana(hoy, cfg)
    # Paginacion: se cargan de a PAGE dias habiles (evita decenas de llamadas
    # a DentiDesk de una sola vez). Pagina chica = carga inicial mas rapida; el
    # frontend pide mas con 'offset'.
    #
    # min_dias (opcional): el servidor sigue escaneando lotes EN PARALELO hasta
    # juntar al menos ese numero de dias CON horas (o agotar la ventana / el tope
    # por request). Antes esto lo hacia el frontend pidiendo pagina tras pagina
    # en SERIE (cada pagina fria = 2-3s contra DentiDesk): con doctores de pocos
    # dias disponibles eso sumaba 6-12s para ver las horas (mediana real medida:
    # 7.5s). Resolverlo en una sola request evita los ping-pong cliente-servidor.
    PAGE = 6
    MAX_DIAS_REQ = 30   # tope de dias escaneados por request (protege a DentiDesk)
    offset = arg_int('offset', 0, minimo=0)
    min_dias = arg_int('min_dias', 0, minimo=0, maximo=10)

    def trabajo(d):
        try:
            return d, _horas_de_dia(doctor, motivo, d, cfg)
        except Exception:
            return d, []

    dias = []
    idx = offset
    escaneados = 0
    while idx < len(todos) and escaneados < MAX_DIAS_REQ:
        lote = todos[idx:idx + PAGE]
        with ThreadPoolExecutor(max_workers=8) as pool:
            for d, horas in sorted(pool.map(trabajo, lote), key=lambda x: x[0]):
                if horas:
                    dias.append({'fecha': d.isoformat(), 'legible': _fecha_legible(d), 'horas': horas})
        idx += len(lote)
        escaneados += len(lote)
        if len(dias) >= (min_dias or 1):
            break
        if not min_dias:
            break   # sin min_dias: comportamiento clasico de UNA pagina
    return jsonify({'ok': True, 'dias': dias,
                    'offset_siguiente': idx,
                    'hay_mas': idx < len(todos)})

# Estados que indican que la cita YA no esta vigente (no se puede reagendar de
# nuevo desde el mismo link -- ya fue cancelada, reagendada o atendida).
_ESTADOS_NO_REAGENDABLES = ('cancel', 'reagend', 're-agend', 'atendid', 'no seguir')

@rate_limit('30 per minute')
@app.route('/api/agenda/reagendar-info', methods=['GET'])
def agenda_reagendar_info():
    """Datos de la cita ORIGINAL para precargar el flujo de reagendar (doctor +
    motivo, de solo lectura -- el paciente no puede cambiarlos). id_agenda +
    fecha vienen del link que manda el webhook de WhatsApp (#reagendar=<id>&
    fecha=<fecha>): DentiDesk no tiene 'buscar por id', hay que saber el dia."""
    id_agenda = (request.args.get('id_agenda') or '').strip()
    fecha_str = (request.args.get('fecha') or '').strip()
    if not id_agenda or not fecha_str:
        return jsonify({'ok': False, 'error': 'Parametros invalidos'}), 400
    try:
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'ok': False, 'error': 'Fecha invalida'}), 400

    cfg = scheduling.load_config()
    if not cfg['dentidesk']['enabled']:
        return jsonify({'ok': False, 'error': 'Modo demo: sin datos reales de DentiDesk'}), 400

    c = dentidesk.info_cita(cfg, id_agenda, fecha)
    if not c:
        return jsonify({'ok': False, 'error': 'No encontramos esa cita. Escríbenos por WhatsApp y te ayudamos.'}), 404
    estado_txt = (c.get('Status') or '').lower()
    if any(s in estado_txt for s in _ESTADOS_NO_REAGENDABLES):
        return jsonify({'ok': False, 'error': 'Esta cita ya no está vigente.'}), 409

    doctor_nombre = (c.get('ProfessionalName') or '').strip()
    doctor_key = dentidesk.doc_key_por_nombre(cfg, doctor_nombre)
    motivo_label = (c.get('Reason') or '').strip()
    duracion_min = int(c.get('duration') or 30)
    if not doctor_key:
        return jsonify({'ok': False, 'error': 'No pudimos identificar al profesional de esta cita. Escríbenos por WhatsApp.'}), 409
    id_reason = dentidesk.id_reason_por_label(cfg, doctor_key, motivo_label)
    if not id_reason:
        return jsonify({'ok': False, 'error': 'No pudimos reagendar automáticamente este motivo. Escríbenos por WhatsApp y te ayudamos.'}), 409

    doctors = read_doctor_data()
    info = doctors.get(doctor_key, {})
    hora_actual = (c.get('time') or '')[:5]
    # Regla de almuerzo: una cita de mañana larga (ortodoncia, 60+ min) debe
    # mantenerse en la mañana al reagendar (ver scheduling.restriccion_manana_reagenda).
    solo_manana = scheduling.restriccion_manana_reagenda(cfg, doctor_key, hora_actual, duracion_min)
    return jsonify({
        'ok': True, 'id_agenda': id_agenda,
        'doctor': doctor_key, 'doctor_nombre': info.get('name', doctor_nombre),
        'doctor_foto': info.get('photo', ''),
        'motivo_label': motivo_label, 'duracion_min': duracion_min,
        'fecha_actual': fecha.isoformat(), 'hora_actual': hora_actual,
        'solo_manana': solo_manana,
    })

@rate_limit('30 per minute')
@app.route('/api/agenda/disponibilidad-reagendar', methods=['GET'])
def agenda_disponibilidad_reagendar():
    """Igual que /api/agenda/disponibilidad, pero por DURACION (no motivo) --
    la usa el flujo de reagendar, que preserva el motivo original de la cita
    (puede no estar en la lista de motivos agendables online)."""
    from concurrent.futures import ThreadPoolExecutor

    doctor = request.args.get('doctor')
    cfg = scheduling.load_config()
    if doctor not in cfg['doctores']:
        return jsonify({'ok': False, 'error': 'Parametros invalidos'}), 400
    try:
        duracion_min = int(request.args.get('duracion', 0))
    except (TypeError, ValueError):
        duracion_min = 0
    if duracion_min <= 0:
        return jsonify({'ok': False, 'error': 'Parametros invalidos'}), 400

    # solo_am: regla de almuerzo (cita de mañana larga de ortodoncia debe
    # mantenerse en la mañana). El frontend lo pasa segun reagendar-info.
    solo_am = str(request.args.get('solo_am', '')).strip() in ('1', 'true', 'yes', 'on')
    corte = cfg['horario'].get('corte_pm', '14:00')

    hoy = fechas.hoy_chile()
    todos = scheduling.dias_habiles_ventana(hoy, cfg)
    PAGE = 6
    MAX_DIAS_REQ = 30
    offset = arg_int('offset', 0, minimo=0)
    min_dias = arg_int('min_dias', 0, minimo=0, maximo=10)

    def trabajo(d):
        try:
            horas = _horas_de_dia_libre(doctor, duracion_min, d, cfg)
            if solo_am:
                horas = [h for h in horas if h < corte]
            return d, horas
        except Exception:
            return d, []

    dias = []
    idx = offset
    escaneados = 0
    while idx < len(todos) and escaneados < MAX_DIAS_REQ:
        lote = todos[idx:idx + PAGE]
        with ThreadPoolExecutor(max_workers=8) as pool:
            for d, horas in sorted(pool.map(trabajo, lote), key=lambda x: x[0]):
                if horas:
                    dias.append({'fecha': d.isoformat(), 'legible': _fecha_legible(d), 'horas': horas})
        idx += len(lote)
        escaneados += len(lote)
        if len(dias) >= (min_dias or 1):
            break
        if not min_dias:
            break
    return jsonify({'ok': True, 'dias': dias,
                    'offset_siguiente': idx,
                    'hay_mas': idx < len(todos)})

def _check_admin_token():
    """Protege endpoints sensibles. En produccion se define ADMIN_TOKEN (env var);
    el llamador debe mandar el header 'X-Admin-Token'. Sin ADMIN_TOKEN configurado
    (desarrollo local) se permite.

    SEGURIDAD: solo se acepta por header, NO por ?token= en la URL — las URLs
    quedan en logs, historial y Referer, filtrando el token. Comparacion de
    tiempo constante (hmac.compare_digest) para evitar timing attacks."""
    tok = os.environ.get('ADMIN_TOKEN')
    if not tok:
        return True
    provisto = request.headers.get('X-Admin-Token') or ''
    return hmac.compare_digest(provisto, tok)

@app.route('/api/pacientes/actualizar', methods=['POST'])
def pacientes_actualizar():
    """Reconstruye la base de pacientes barriendo la agenda de DentiDesk (2x/dia)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    import pacientes
    cfg = scheduling.load_config()
    if not cfg['dentidesk']['enabled']:
        return jsonify({'ok': False, 'error': 'Modo demo: sin credenciales DentiDesk'}), 400
    data = request.json or {}
    res = pacientes.construir_desde_agenda(
        cfg,
        dias_atras=int(data.get('dias_atras', 180)),
        dias_adelante=int(data.get('dias_adelante', 120)),
    )
    return jsonify({'ok': True, **res})

@app.route('/api/pacientes/importar', methods=['POST'])
def pacientes_importar():
    """Siembra la base desde el Excel del panel DentiDesk (multipart 'file').
    Se usa UNA vez para sembrar produccion. Protegido por ADMIN_TOKEN."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    import pacientes, tempfile
    f = request.files.get('file')
    if not f:
        return jsonify({'ok': False, 'error': 'Falta el archivo'}), 400
    reemplazar = str(request.form.get('reemplazar', '')).lower() in ('1', 'true', 'yes', 'on')
    with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
        f.save(tmp.name)
        ruta = tmp.name
    try:
        res = pacientes.importar_export_excel(ruta, reemplazar=reemplazar)
    finally:
        try:
            os.remove(ruta)
        except OSError:
            pass
    return jsonify({'ok': True, **res})

@app.route('/api/pacientes/importar-cumpleanos', methods=['POST'])
def pacientes_importar_cumpleanos():
    """Agrega la FECHA DE NACIMIENTO a la base desde el export 'Listado de
    Cumpleanos' del panel DentiDesk (multipart 'file'). Protegido.

    ⚠️ Pese a la extension .xls, ese export NO es Excel: es una tabla HTML. Se
    guarda con sufijo .xls igual (el parser mira el contenido, no el nombre).

    Es idempotente: se puede volver a correr cada vez que la clinica re-exporte
    el listado para incorporar a los pacientes nuevos. Solo escribe
    fecha_nacimiento e id_paciente; nunca pisa email/telefono/genero/direccion."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    import pacientes, tempfile
    f = request.files.get('file')
    if not f:
        return jsonify({'ok': False, 'error': 'Falta el archivo'}), 400
    crear = str(request.form.get('crear_nuevos', '1')).lower() in ('1', 'true', 'yes', 'on')
    with tempfile.NamedTemporaryFile(delete=False, suffix='.xls') as tmp:
        f.save(tmp.name)
        ruta = tmp.name
    try:
        res = pacientes.importar_cumpleanos(ruta, crear_nuevos=crear)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'No se pudo leer el archivo: {e}'}), 400
    finally:
        try:
            os.remove(ruta)
        except OSError:
            pass
    # Si no se reconocio NINGUNA fila es que subieron otro archivo (p.ej. el
    # Excel de pacientes en vez del listado de cumpleanos). Devolver ok:true con
    # todo en cero se leeria como "importado" cuando no se importo nada.
    if not (res['nuevos'] or res['actualizados'] or res['duplicados_archivo']):
        return jsonify({'ok': False, 'error': 'No se reconocio ninguna fila con RUT y fecha '
                                              'de nacimiento. ¿Es el export "Listado de '
                                              'Cumpleaños" del panel DentiDesk?'}), 400
    return jsonify({'ok': True, **res})


@app.route('/api/pacientes/reset', methods=['POST'])
def pacientes_reset():
    """Vacia la base de pacientes (para resembrar desde cero). Protegido."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    import pacientes
    pacientes.vaciar()
    return jsonify({'ok': True, 'total': 0})

@app.route('/api/pacientes/estado', methods=['GET'])
def pacientes_estado():
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    import pacientes
    return jsonify({'ok': True, 'total': pacientes.total(),
                    'fecha_nacimiento': pacientes.cobertura_fecha_nacimiento()})

def _verificar_turnstile(token):
    """Valida el token de Cloudflare Turnstile contra siteverify.
    Si no hay TURNSTILE_SECRET configurado, devuelve True (captcha desactivado)."""
    secret = os.environ.get('TURNSTILE_SECRET', '').strip()
    if not secret:
        return True  # captcha no configurado aun -> no bloquear
    if not token:
        return False
    try:
        import requests
        r = requests.post('https://challenges.cloudflare.com/turnstile/v0/siteverify',
                          data={'secret': secret, 'response': token,
                                'remoteip': request.headers.get('CF-Connecting-IP', '')},
                          timeout=10)
        return bool((r.json() or {}).get('success'))
    except Exception:
        return False  # ante error de verificacion, mejor rechazar

@rate_limit('10 per minute')
@app.route('/api/agenda/reservar', methods=['POST'])
def agenda_reservar():
    """Crea la cita en DentiDesk y dispara la confirmacion (WhatsApp / email)."""
    data = request.json or {}
    cfg = scheduling.load_config()

    # Reagendar NO pasa por aqui: va SIEMPRE por /api/agenda/reservar-reagenda,
    # que preserva el motivo/doctor EXACTOS de la cita vieja. Este endpoint
    # (wizard libre) dejaba elegir cualquier motivo -> un reagendamiento
    # terminaba con un tipo de cita distinto (bug real: "Imp essix" quedo como
    # "Control Fijo"). Se rechaza aunque llegue de un navegador con JS viejo
    # cacheado, para cortar el bug de raiz sin esperar a que refresque.
    if str(data.get('reagenda_id_agenda') or '').strip():
        return jsonify({'ok': False, 'reagenda_bloqueada': True,
                        'error': 'Para reagendar esta hora, escríbenos por WhatsApp y te ayudamos.'}), 409

    # Captcha Cloudflare Turnstile (anti-bot). Solo se valida si esta configurado
    # el secreto en el entorno; si no, se omite (no rompe antes de activarlo).
    if not _verificar_turnstile(data.get('captcha_token', '')):
        return jsonify({'ok': False, 'error': 'Verificación de seguridad fallida. Recarga e intenta de nuevo.'}), 403

    doctor = data.get('doctor'); motivo = data.get('motivo')
    if doctor not in cfg['doctores'] or motivo not in cfg['motivos']:
        return jsonify({'ok': False, 'error': 'Parametros invalidos'}), 400
    # Los motivos compuestos (Estudio Integral) se reservan por su propio
    # endpoint; este solo acepta motivos con IdReason real de DentiDesk.
    if cfg['motivos'][motivo].get('compuesto') or not cfg['motivos'][motivo].get('id_reason'):
        return jsonify({'ok': False, 'error': 'Este motivo se agenda por un flujo especial'}), 400
    try:
        fecha = datetime.strptime(data['fecha'], '%Y-%m-%d').date()
    except (KeyError, ValueError):
        return jsonify({'ok': False, 'error': 'Fecha invalida'}), 400
    hora = data.get('hora', '')
    motivo_cfg = cfg['motivos'][motivo]

    # Validar RUT en backend (defensa: el frontend ya valida)
    rut = data.get('rut', '')
    if not scheduling.rut_valido(rut):
        return jsonify({'ok': False, 'error': 'RUT invalido'}), 400

    # Email es OBLIGATORIO para DentiDesk (createAgenda lo exige).
    # DEDUP: DentiDesk reconoce al paciente solo si RUT + email coinciden con su
    # ficha. Si el RUT esta en nuestra base local, usamos SU email registrado
    # (no el que escriba) para que NO se duplique la ficha.
    import pacientes
    rec = pacientes.lookup(rut) if cfg['dentidesk']['enabled'] else None
    if rec and rec.get('email'):
        email = rec['email']
    else:
        email = (data.get('email') or '').strip()
    if '@' not in email or '.' not in email:
        return jsonify({'ok': False, 'error': 'El email es obligatorio'}), 400

    # Revalidar en backend: ventana (max 60 dias) + anticipacion + disponibilidad
    if not scheduling.dentro_de_ventana(fecha, cfg):
        return jsonify({'ok': False, 'error': 'No se puede agendar con mas de 60 dias de anticipacion'}), 409
    if not scheduling.cumple_anticipacion(fecha, hora, motivo_cfg, cfg):
        return jsonify({'ok': False, 'error': 'La hora no cumple la anticipacion minima'}), 409
    libres_d, ocupados_d = dentidesk.disponibilidad_real(doctor, fecha, motivo, cfg)
    if hora not in scheduling.horas_disponibles(doctor, fecha, motivo, libres_d, ocupados_d, cfg):
        return jsonify({'ok': False, 'error': 'La hora ya no esta disponible'}), 409

    # Nombres/apellidos vienen separados; si no, se parte 'nombre'
    nombre = (data.get('nombres') or '').strip()
    apellido = (data.get('apellidos') or '').strip()
    if not nombre:
        partes = (data.get('nombre') or '').strip().split(' ', 1)
        nombre, apellido = partes[0], (partes[1] if len(partes) > 1 else '')

    # Telefono: si reconocimos al paciente, usamos el suyo registrado
    telefono = (rec.get('telefono') if rec else '') or data.get('telefono', '')
    # Nombre/apellido: si vienen vacios pero hay ficha, usamos los de la base
    if not nombre and rec:
        nombre, apellido = rec.get('nombres', ''), rec.get('apellidos', '')

    # Si es un reagendamiento (fallback por este endpoint) PARA EL DIA SIGUIENTE
    # habil, la cita nueva nace "Confirmado por WhatsApp" (32180): el paciente
    # viene interactuando por WhatsApp. Solo aplica a reagenda; una reserva
    # normal para mañana sigue naciendo "No confirmado".
    _reag_id = ''.join(c for c in str(data.get('reagenda_id_agenda') or '') if c.isdigit())
    id_status_nueva = None
    if _reag_id and scheduling.es_dia_siguiente_habil(fecha):
        id_status_nueva = cfg['dentidesk'].get('id_status_confirmado_whatsapp')

    res = dentidesk.crear_cita(
        doc_id=doctor, motivo_key=motivo, id_status=id_status_nueva,
        target_date=fecha, hora=hora,
        nombre=nombre, apellido=apellido,
        email=email, telefono=telefono,
        rut=scheduling.limpiar_rut(rut), cfg=cfg,
    )
    if not res.get('ok'):
        return jsonify({'ok': False, 'error': 'No se pudo crear la cita'}), 502
    # La hora recien tomada debe desaparecer al instante para quien este mirando.
    _refrescar_dia_reservado(doctor, fecha, cfg)

    # Registrar esta cita online como "ya confirmada" para que el barrido de
    # confirmaciones (citas presenciales/telefono) no le reenvie el correo.
    try:
        import confirmaciones
        confirmaciones.marcar_enviada(res.get('id_cita'))
    except Exception:
        pass

    doctors = read_doctor_data()
    doctor_nombre = doctors.get(doctor, {}).get('name', doctor.title())

    # Registrar el agendamiento para estadisticas. Incluye el nombre del paciente
    # (a pedido del usuario, para verlo en el panel -> Ultimas reservas).
    try:
        import stats
        stats.registrar({
            'fecha': fecha.isoformat(), 'hora': hora,
            'paciente_nombre': f"{nombre} {apellido}".strip(),
            'doctor': doctor, 'doctor_nombre': doctor_nombre,
            'motivo': motivo, 'motivo_label': motivo_cfg['label'],
            'especialidad': cfg['especialidades'].get(motivo_cfg.get('especialidad', ''), {}).get('label', motivo_cfg.get('especialidad', '')),
            'paciente_conocido': bool(rec),
        })
    except Exception:
        pass

    # "No soy yo": el paciente está en la BD pero dice que esos datos no son suyos.
    #   DentiDesk recibe el email registrado (dedup); la confirmacion va al email nuevo.
    # "Completar datos": el paciente está en la BD pero SIN email en ficha (antiguo);
    #   aporta su contacto -> se agenda con su RUT y se avisa a recepción para
    #   actualizar la ficha (y así evitar duplicados en futuras reservas).
    es_no_soy_yo    = bool(data.get('es_no_soy_yo')) and rec is not None
    es_completar    = bool(data.get('es_completar_datos')) and rec is not None and not es_no_soy_yo
    email_nuevo     = (data.get('email_nuevo') or data.get('email') or '').strip()
    telefono_nuevo  = (data.get('telefono_nuevo') or data.get('telefono') or '').strip()
    email_notif     = email_nuevo if ((es_no_soy_yo or es_completar) and '@' in email_nuevo) else email

    # Reagendamiento: si la reserva vino por el link de "Reagendar" de un
    # recordatorio (#reagendar=<id> -> reagenda_id_agenda), marcamos la cita
    # VIEJA como "Re-agendado" en DentiDesk ahora que la nueva ya quedó creada.
    # (updateAgenda solo cambia el estado -- no se puede mover ni acortar la
    # cita vieja por la API; ver dentidesk.actualizar_estado_cita.)
    # Solo dígitos (viene del hash de un link nuestro). Si falla el update, se
    # loguea pero NO se rompe la reserva nueva (que sí quedó hecha).
    reagenda_id = ''.join(c for c in str(data.get('reagenda_id_agenda') or '') if c.isdigit())
    es_reagenda = bool(reagenda_id)
    if es_reagenda:
        id_status_reag = cfg['dentidesk'].get('id_status_reagendada')
        if id_status_reag:
            try:
                dentidesk.actualizar_estado_cita(reagenda_id, id_status_reag, cfg)
            except Exception as e:
                app.logger.error('No se pudo marcar como reagendada la cita %s: %s', reagenda_id, e)
        else:
            app.logger.warning('id_status_reagendada no configurado -- no se marca la cita %s', reagenda_id)

    # Reagenda: avisar por email Y WhatsApp (el paciente vino desde WhatsApp).
    # Primera consulta: plantilla de WhatsApp propia (video de bienvenida) y
    # canal 'ambos' -- el email igual va porque lleva el .ics del calendario.
    # Reserva normal: canal automatico (email primero, WhatsApp de respaldo).
    es_primera = (motivo == 'primera_consulta')
    confirm = notify.enviar_confirmacion({
        'nombre': nombre, 'telefono': telefono_nuevo or data.get('telefono', ''),
        'email': email_notif, 'fecha': fecha,
        'fecha_legible': _fecha_legible(fecha), 'hora': hora,
        'doctor_nombre': doctor_nombre, 'motivo_label': motivo_cfg['label'],
        'dur_min': motivo_cfg['duracion_min'],
        'id_agenda': res.get('id_cita'),
    }, cfg, canal=('ambos' if (es_reagenda or es_primera) else None),
       reagenda=es_reagenda, primera=es_primera)

    # Aviso a recepción cuando el motivo lo tiene activado en el panel (ticket
    # "Avisar a recepción"). Independiente de la confirmación al paciente.
    if motivo_cfg.get('notificar_agenda'):
        notify.enviar_aviso_agendamiento({
            'nombre': f"{nombre} {apellido}".strip(),
            'rut_fmt': scheduling.formatear_rut(rut),
            'email': email_notif, 'telefono': telefono_nuevo or telefono,
            'fecha_legible': _fecha_legible(fecha), 'hora': hora,
            'doctor_nombre': doctor_nombre, 'motivo_label': motivo_cfg['label'],
        }, cfg)

    if es_no_soy_yo or es_completar:
        notify.enviar_solicitud_cambio_datos({
            'nombre': f"{rec.get('nombres','')} {rec.get('apellidos','')}".strip() or nombre,
            'rut_fmt': scheduling.formatear_rut(rut),
            'email_antiguo': rec.get('email', ''),
            'email_nuevo': email_nuevo,
            'telefono_antiguo': rec.get('telefono', ''),
            'telefono_nuevo': telefono_nuevo,
            'fecha_legible': _fecha_legible(fecha), 'hora': hora,
            'doctor_nombre': doctor_nombre,
        }, cfg)

    return jsonify({'ok': True, 'id_cita': res.get('id_cita'),
                    'confirmacion': confirm, 'mock': res.get('mock', False),
                    'solicitud_cambio': es_no_soy_yo or es_completar,
                    'reagenda': es_reagenda})

@rate_limit('10 per minute')
@app.route('/api/agenda/reservar-reagenda', methods=['POST'])
def agenda_reservar_reagenda():
    """Reagenda preservando el motivo y la duracion ORIGINALES de la cita
    vieja -- el paciente NO elige motivo (a diferencia de /api/agenda/reservar
    con reagenda_id_agenda, que crea la cita nueva con un motivo del menu
    online). Lo usa el link de reagendar cuando /api/agenda/reagendar-info
    pudo precargar doctor+motivo (ver dentidesk.id_reason_por_label)."""
    data = request.json or {}
    cfg = scheduling.load_config()

    if not _verificar_turnstile(data.get('captcha_token', '')):
        return jsonify({'ok': False, 'error': 'Verificación de seguridad fallida. Recarga e intenta de nuevo.'}), 403

    id_agenda = (data.get('id_agenda') or '').strip()
    fecha_original_str = (data.get('fecha_original') or '').strip()
    doctor = data.get('doctor')
    if not id_agenda or not fecha_original_str or doctor not in cfg['doctores']:
        return jsonify({'ok': False, 'error': 'Parametros invalidos'}), 400
    try:
        fecha_original = datetime.strptime(fecha_original_str, '%Y-%m-%d').date()
        fecha = datetime.strptime(data['fecha'], '%Y-%m-%d').date()
    except (KeyError, ValueError):
        return jsonify({'ok': False, 'error': 'Fecha invalida'}), 400
    hora = data.get('hora', '')

    rut = data.get('rut', '')
    if not scheduling.rut_valido(rut):
        return jsonify({'ok': False, 'error': 'RUT invalido'}), 400

    # Releer la cita vieja EN VIVO (no confiar en lo que junto el frontend al
    # abrir el link): motivo, duracion y estado deben ser los actuales.
    c = dentidesk.info_cita(cfg, id_agenda, fecha_original)
    if not c:
        return jsonify({'ok': False, 'error': 'No encontramos esa cita. Escríbenos por WhatsApp.'}), 404
    estado_txt = (c.get('Status') or '').lower()
    if any(s in estado_txt for s in _ESTADOS_NO_REAGENDABLES):
        return jsonify({'ok': False, 'error': 'Esta cita ya no está vigente.'}), 409
    doctor_real = dentidesk.doc_key_por_nombre(cfg, (c.get('ProfessionalName') or '').strip())
    if doctor_real != doctor:
        return jsonify({'ok': False, 'error': 'Los datos de la cita cambiaron. Intenta de nuevo.'}), 409
    motivo_label = (c.get('Reason') or '').strip()
    duracion_min = int(c.get('duration') or 30)
    id_reason = dentidesk.id_reason_por_label(cfg, doctor, motivo_label)
    if not id_reason:
        return jsonify({'ok': False, 'error': 'No pudimos reagendar automáticamente este motivo. Escríbenos por WhatsApp.'}), 409

    # Regla de almuerzo (autoritativa, re-derivada de la cita real): una cita de
    # mañana larga de ortodoncia debe mantenerse en la mañana al reagendar.
    hora_original = (c.get('time') or '')[:5]
    solo_manana = scheduling.restriccion_manana_reagenda(cfg, doctor, hora_original, duracion_min)
    corte = cfg['horario'].get('corte_pm', '14:00')
    if solo_manana and hora >= corte:
        return jsonify({'ok': False, 'error': 'Esta cita debe mantenerse en horario de mañana.'}), 409

    # Revalidar en backend: ventana + anticipacion + disponibilidad (por
    # duracion, motivo-agnostico -- igual criterio que agenda_disponibilidad_reagendar).
    if not scheduling.dentro_de_ventana(fecha, cfg):
        return jsonify({'ok': False, 'error': 'No se puede agendar con mas de 60 dias de anticipacion'}), 409
    if not scheduling.cumple_anticipacion(fecha, hora, None, cfg):
        return jsonify({'ok': False, 'error': 'La hora no cumple la anticipacion minima'}), 409
    libres15 = dentidesk.bloques_libres_15(cfg, doctor, fecha)
    libres = dentidesk.horas_que_caben(libres15, duracion_min)
    ocupados = dentidesk.bloques_ocupados(cfg, doctor, fecha)
    if hora not in scheduling.horas_disponibles_libre(doctor, fecha, libres, ocupados, cfg):
        return jsonify({'ok': False, 'error': 'La hora ya no esta disponible'}), 409

    # Email obligatorio para DentiDesk; dedup por RUT (mismo criterio que /reservar)
    import pacientes
    rec = pacientes.lookup(rut) if cfg['dentidesk']['enabled'] else None
    if rec and rec.get('email'):
        email = rec['email']
    else:
        email = (data.get('email') or '').strip()
    if '@' not in email or '.' not in email:
        return jsonify({'ok': False, 'error': 'El email es obligatorio'}), 400

    nombre = (data.get('nombres') or '').strip()
    apellido = (data.get('apellidos') or '').strip()
    telefono = (rec.get('telefono') if rec else '') or data.get('telefono', '')
    if not nombre and rec:
        nombre, apellido = rec.get('nombres', ''), rec.get('apellidos', '')

    # Si reagenda para el DIA SIGUIENTE habil, la cita nueva nace "Confirmado
    # por WhatsApp" (32180): el paciente viene interactuando por WhatsApp y
    # eligio una hora inminente -> ya esta confirmada de hecho (pedido del
    # usuario 2026-07-08). Si no, nace "No confirmado" (default de crear_cita).
    id_status_nueva = None
    if scheduling.es_dia_siguiente_habil(fecha):
        id_status_nueva = cfg['dentidesk'].get('id_status_confirmado_whatsapp')

    # enviar_duracion=False por ahora: el campo 'Duration' de createAgenda NO
    # esta confirmado en vivo. Se deja apagado hasta verificar (con una cita de
    # duracion ATIPICA) que DentiDesk lo acepta sin rechazar el createAgenda.
    # Mientras, la cita nueva toma la duracion STANDARD de su IdReason (correcto
    # en la gran mayoria de casos). TODO: activar tras verificar el campo.
    res = dentidesk.crear_cita(
        doc_id=doctor, id_reason=id_reason, duracion_min=duracion_min,
        enviar_duracion=False, id_status=id_status_nueva, target_date=fecha, hora=hora,
        nombre=nombre, apellido=apellido, email=email, telefono=telefono,
        rut=scheduling.limpiar_rut(rut), cfg=cfg,
    )
    if not res.get('ok'):
        return jsonify({'ok': False, 'error': 'No se pudo crear la cita'}), 502
    _refrescar_dia_reservado(doctor, fecha, cfg)

    try:
        import confirmaciones
        confirmaciones.marcar_enviada(res.get('id_cita'))
    except Exception:
        pass

    # Cita vieja: marcarla "Re-agendado". (updateAgenda solo cambia el estado;
    # no se puede mover ni acortar por la API -- el estado "Re-agendado" NO
    # libera el bloque en DentiDesk, decision del usuario 2026-07-08 de
    # mantener la etiqueta por sobre liberar el horario.) Si falla, se loguea
    # pero NO rompe la reserva nueva (que sí quedó hecha).
    id_status_reag = cfg['dentidesk'].get('id_status_reagendada')
    if id_status_reag:
        try:
            dentidesk.actualizar_estado_cita(id_agenda, id_status_reag, cfg)
            _refrescar_dia_reservado(doctor, fecha_original, cfg)
        except Exception as e:
            app.logger.error('No se pudo marcar como reagendada la cita %s: %s', id_agenda, e)
    else:
        app.logger.warning('id_status_reagendada no configurado -- no se marca la cita %s', id_agenda)

    doctors = read_doctor_data()
    doctor_nombre = doctors.get(doctor, {}).get('name', doctor.title())

    try:
        import stats
        stats.registrar({
            'fecha': fecha.isoformat(), 'hora': hora,
            'paciente_nombre': f"{nombre} {apellido}".strip(),
            'doctor': doctor, 'doctor_nombre': doctor_nombre,
            'motivo': motivo_label, 'motivo_label': motivo_label,
            'especialidad': cfg['doctores'][doctor].get('especialidad', ''),
            'paciente_conocido': bool(rec),
        })
    except Exception:
        pass

    # Reagenda: avisar por email Y WhatsApp (el paciente vino desde WhatsApp).
    confirm = notify.enviar_confirmacion({
        'nombre': nombre, 'telefono': telefono,
        'email': email, 'fecha': fecha,
        'fecha_legible': _fecha_legible(fecha), 'hora': hora,
        'doctor_nombre': doctor_nombre, 'motivo_label': motivo_label,
        'dur_min': duracion_min,
    }, cfg, canal='ambos', reagenda=True)

    return jsonify({'ok': True, 'id_cita': res.get('id_cita'),
                    'confirmacion': confirm, 'mock': res.get('mock', False),
                    'reagenda': True})

@rate_limit('10 per minute')
@app.route('/api/agenda/reservar-estudio', methods=['POST'])
def agenda_reservar_estudio():
    """Estudio Integral de Ortodoncia: agenda las DOS citas (Registros +
    Explicacion del Plan) en una sola operacion. Reglas:
      - SOLO pacientes ya registrados en la base (deben haber tenido una
        consulta previa en la clinica).
      - fecha2 >= fecha1 + separacion_min_dias (config, 14 dias).
      - Ambas horas se revalidan EN VIVO contra DentiDesk antes de crear.
      - Si la 2a cita falla tras crear la 1a, la 1a se cancela (rollback)
        para no dejar el estudio a medias.
    Body: {doctor, fecha1, hora1, fecha2, hora2, rut, captcha_token}."""
    data = request.json or {}
    cfg = scheduling.load_config()

    if not _verificar_turnstile(data.get('captcha_token', '')):
        return jsonify({'ok': False, 'error': 'Verificación de seguridad fallida. Recarga e intenta de nuevo.'}), 403

    comp = cfg['motivos'].get('estudio_integral') or {}
    claves = comp.get('compuesto') or ['estudio_registros', 'estudio_explicacion']
    m1_key, m2_key = claves[0], claves[1]
    sep_min = int(comp.get('separacion_min_dias') or 14)

    doctor = data.get('doctor')
    if doctor not in cfg['doctores'] or m1_key not in cfg['motivos'] or m2_key not in cfg['motivos']:
        return jsonify({'ok': False, 'error': 'Parametros invalidos'}), 400

    rut = data.get('rut', '')
    if not scheduling.rut_valido(rut):
        return jsonify({'ok': False, 'error': 'RUT invalido'}), 400
    try:
        fecha1 = datetime.strptime(data.get('fecha1', ''), '%Y-%m-%d').date()
        fecha2 = datetime.strptime(data.get('fecha2', ''), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'Fecha invalida'}), 400
    hora1 = data.get('hora1', ''); hora2 = data.get('hora2', '')

    if (fecha2 - fecha1).days < sep_min:
        return jsonify({'ok': False, 'error': f'La Explicación del Plan debe ser al menos {sep_min} días después de los Registros'}), 409

    # Gate: solo pacientes con ficha en la base local (consulta previa).
    import pacientes
    rec = pacientes.lookup(rut) if cfg['dentidesk']['enabled'] else \
        {'nombres': data.get('nombres', 'Demo'), 'apellidos': data.get('apellidos', ''),
         'email': data.get('email', 'demo@demo.cl'), 'telefono': data.get('telefono', '')}
    if not rec:
        return jsonify({'ok': False, 'error': 'El Estudio Integral está disponible solo para pacientes que ya han tenido una consulta en la clínica. Si aún no ha venido, agende primero una Primera Consulta.'}), 403

    # Contacto SIEMPRE desde la ficha registrada (dedup garantizado en DentiDesk).
    email = (rec.get('email') or '').strip()
    telefono = (rec.get('telefono') or '').strip() or (data.get('telefono') or '').strip()
    nombre = (rec.get('nombres') or '').strip()
    apellido = (rec.get('apellidos') or '').strip()
    if '@' not in email:
        return jsonify({'ok': False, 'error': 'Su ficha no tiene email registrado. Escríbanos por WhatsApp y lo agendamos.'}), 409

    # Revalidar ambas horas: reglas locales + disponibilidad FRESCA de DentiDesk.
    for f, h, mk in ((fecha1, hora1, m1_key), (fecha2, hora2, m2_key)):
        if not scheduling.dentro_de_ventana(f, cfg):
            return jsonify({'ok': False, 'error': 'No se puede agendar con mas de 60 dias de anticipacion'}), 409
        if not scheduling.cumple_anticipacion(f, h, cfg['motivos'][mk], cfg):
            return jsonify({'ok': False, 'error': 'La hora no cumple la anticipacion minima'}), 409
        libres_d, ocupados_d = dentidesk.disponibilidad_real(doctor, f, mk, cfg)
        if h not in scheduling.horas_disponibles(doctor, f, mk, libres_d, ocupados_d, cfg):
            return jsonify({'ok': False, 'error': f'La hora de las {h} del {_fecha_legible(f)} ya no está disponible'}), 409

    rut_limpio = scheduling.limpiar_rut(rut)
    res1 = dentidesk.crear_cita(doc_id=doctor, motivo_key=m1_key, target_date=fecha1, hora=hora1,
                                nombre=nombre, apellido=apellido, email=email, telefono=telefono,
                                rut=rut_limpio, cfg=cfg)
    if not res1.get('ok'):
        return jsonify({'ok': False, 'error': 'No se pudo crear la cita de Registros'}), 502

    try:
        res2 = dentidesk.crear_cita(doc_id=doctor, motivo_key=m2_key, target_date=fecha2, hora=hora2,
                                    nombre=nombre, apellido=apellido, email=email, telefono=telefono,
                                    rut=rut_limpio, cfg=cfg)
    except Exception as e:
        app.logger.error('crear_cita explicacion fallo: %s', e)
        res2 = {'ok': False}
    if not res2.get('ok'):
        # Rollback: cancelar la cita de registros para no dejar el estudio cojo.
        id_cancel = cfg['dentidesk'].get('id_status_cancelado')
        if res1.get('id_cita') and id_cancel:
            try:
                dentidesk.actualizar_estado_cita(res1['id_cita'], id_cancel, cfg)
            except Exception as e:
                app.logger.error('Rollback de registros %s fallo: %s', res1.get('id_cita'), e)
        return jsonify({'ok': False, 'error': 'No se pudo agendar la segunda cita, y la primera fue anulada. Intente nuevamente o escríbanos por WhatsApp.'}), 502

    _refrescar_dia_reservado(doctor, fecha1, cfg)
    _refrescar_dia_reservado(doctor, fecha2, cfg)

    # Que el barrido de confirmaciones no las reenvie.
    try:
        import confirmaciones
        confirmaciones.marcar_enviada(res1.get('id_cita'))
        confirmaciones.marcar_enviada(res2.get('id_cita'))
    except Exception:
        pass

    doctors = read_doctor_data()
    doctor_nombre = doctors.get(doctor, {}).get('name', doctor.title())

    # Estadisticas: una fila por cita (con su motivo real).
    try:
        import stats
        for f, h, mk in ((fecha1, hora1, m1_key), (fecha2, hora2, m2_key)):
            stats.registrar({
                'fecha': f.isoformat(), 'hora': h,
                'paciente_nombre': f"{nombre} {apellido}".strip(),
                'doctor': doctor, 'doctor_nombre': doctor_nombre,
                'motivo': mk, 'motivo_label': cfg['motivos'][mk]['label'],
                'especialidad': cfg['especialidades'].get(cfg['motivos'][mk].get('especialidad', ''), {}).get('label', ''),
                'paciente_conocido': True,
            })
    except Exception:
        pass

    # Confirmacion al paciente: una por cita (cada una con su .ics), mismo
    # pipeline que una reserva normal (email primero, WhatsApp de respaldo).
    confirmaciones_env = []
    for f, h, mk in ((fecha1, hora1, m1_key), (fecha2, hora2, m2_key)):
        mcfg = cfg['motivos'][mk]
        confirmaciones_env.append(notify.enviar_confirmacion({
            'nombre': nombre, 'telefono': telefono, 'email': email,
            'fecha': f, 'fecha_legible': _fecha_legible(f), 'hora': h,
            'doctor_nombre': doctor_nombre, 'motivo_label': mcfg['label'],
            'dur_min': mcfg['duracion_min'],
        }, cfg))

    # Aviso a recepcion (un solo correo con ambas citas).
    try:
        notify.enviar_aviso_agendamiento({
            'nombre': f"{nombre} {apellido}".strip(),
            'rut_fmt': scheduling.formatear_rut(rut),
            'email': email, 'telefono': telefono,
            'fecha_legible': f'{_fecha_legible(fecha1)} {hora1} (Registros) y {_fecha_legible(fecha2)} {hora2} (Explicación del Plan)',
            'hora': '',
            'doctor_nombre': doctor_nombre,
            'motivo_label': 'Estudio Integral de Ortodoncia (2 citas)',
        }, cfg)
    except Exception:
        pass

    return jsonify({'ok': True,
                    'id_cita1': res1.get('id_cita'), 'id_cita2': res2.get('id_cita'),
                    'confirmaciones': confirmaciones_env,
                    'mock': res1.get('mock', False)})

@app.route('/api/agenda/confirmaciones/run', methods=['POST'])
def confirmaciones_run():
    """Dispara el barrido de confirmaciones manualmente (protegido por ADMIN_TOKEN).
    La 1a corrida SIEMBRA (registra lo existente sin enviar). Util para sembrar al
    activar el sistema y para probar. ?dias=90 ajusta la ventana."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    import confirmaciones
    try:
        dias = max(1, min(180, int(request.args.get('dias', 90))))
    except (TypeError, ValueError):
        dias = 90
    return jsonify(confirmaciones.barrer_y_confirmar(dias_adelante=dias))

@app.route('/api/whatsapp/test', methods=['POST'])
def whatsapp_test():
    """Envia UNA plantilla de WhatsApp de prueba (protegido por ADMIN_TOKEN).
    Sirve para verificar la Cloud API sin agendar una cita real.
    Body JSON: { telefono, plantilla?, nombre?, doctor?, fecha?, hora? }
      plantilla: 'confirmacion_hora' (default) | 'recordatorio_semana' |
                 'recordatorio_dia' | 'inasistencia_reagendar' |
                 'conversacion_general' | 'consentimiento_informado' |
                 'reagenda_confirmada' | 'recordatorio_control_dr_vial' |
                 'primera_consulta'
    El destinatario debe estar registrado como número de prueba en Meta."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    tel = (data.get('telefono') or '').strip()
    if not tel:
        return jsonify({'ok': False, 'error': 'Falta telefono'}), 400

    nombre = data.get('nombre', 'Juan')
    doctor = data.get('doctor', 'Octavio Del Real')
    fecha  = data.get('fecha', 'martes 8 de julio')
    hora   = data.get('hora', '10:30')
    plantilla = data.get('plantilla', 'confirmacion_hora')
    # id_agenda: para pruebas sueltas (no ligadas a una cita real) alcanza un
    # valor cualquiera -- solo viaja en el payload del boton, no se usa hasta
    # que alguien lo toque de verdad.
    id_agenda = str(data.get('id_agenda', '000000'))
    # fecha_iso (YYYY-MM-DD): la fecha REAL de la cita, para que el boton
    # Reagendar arme el link con &fecha= y active la precarga de doctor+motivo.
    # Sin esto (pruebas sueltas), el link abre el wizard completo.
    fecha_iso = str(data.get('fecha_iso', ''))

    envio = {
        'confirmacion_hora':      lambda: wa_cloud.enviar_confirmacion_hora(tel, nombre, doctor, fecha, hora),
        'recordatorio_semana':    lambda: wa_cloud.enviar_recordatorio_semana(tel, nombre, doctor, fecha, hora, id_agenda, fecha_iso=fecha_iso),
        'recordatorio_dia':       lambda: wa_cloud.enviar_recordatorio_dia(tel, nombre, doctor, fecha, hora, id_agenda, fecha_iso=fecha_iso),
        'inasistencia_reagendar': lambda: wa_cloud.enviar_inasistencia_reagendar(tel, nombre, fecha, id_agenda, fecha_iso=fecha_iso),
        'conversacion_general':   lambda: wa_cloud.enviar_conversacion_general(tel, nombre, data.get('motivo', 'una consulta general')),
        'consentimiento_informado': lambda: wa_cloud.enviar_consentimiento(
            tel, nombre, data.get('tipo_label', 'Consentimiento de Ortodoncia'),
            data.get('link', 'https://ortodonciarichard.cl/consentimiento?token=PRUEBA')),
        'reagenda_confirmada':    lambda: wa_cloud.enviar_reagenda_confirmada(tel, nombre, doctor, fecha, hora),
        # Recordatorio de control (recaptacion): 'fecha' aca es la del ULTIMO
        # control, no la de una cita futura. El boton que responde es el
        # tercero ("Agendar por WhatsApp"); tocarlo dispara el webhook igual
        # que en un envio real, asi que sirve para probar el circuito completo.
        'recordatorio_control_dr_vial': lambda: wa_cloud.enviar_recordatorio_control(
            tel, nombre, doctor, fecha, id_agenda, fecha_iso=fecha_iso),
        'primera_consulta':       lambda: wa_cloud.enviar_primera_consulta(
            tel, nombre, doctor, fecha, hora, video_url=data.get('video_url'),
            id_agenda=id_agenda, fecha_iso=fecha_iso),
    }.get(plantilla)
    if not envio:
        return jsonify({'ok': False, 'error': f'Plantilla no valida: {plantilla}'}), 400

    try:
        res = envio()
        return jsonify({'ok': True, 'plantilla': plantilla, 'resultado': res})
    except wa_cloud.WhatsAppCloudError as e:
        return jsonify({'ok': False, 'error': str(e)}), 502

@app.route('/api/whatsapp/test-texto-libre', methods=['POST'])
def whatsapp_test_texto_libre():
    """Envia un mensaje de TEXTO LIBRE (no plantilla) de diagnostico (protegido
    por ADMIN_TOKEN). Solo funciona si el destinatario escribio a la clinica
    en las ultimas 24h (ventana de servicio al cliente) -- sirve para descartar
    problemas especificos de plantillas vs. problemas de conectividad/numero."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    tel = (data.get('telefono') or '').strip()
    texto = (data.get('texto') or 'Mensaje de prueba (texto libre) - Ortodoncia Richard').strip()
    if not tel:
        return jsonify({'ok': False, 'error': 'Falta telefono'}), 400
    try:
        res = wa_cloud.enviar_texto_libre(tel, texto)
        return jsonify({'ok': True, 'resultado': res})
    except wa_cloud.WhatsAppCloudError as e:
        return jsonify({'ok': False, 'error': str(e)}), 502

@app.route('/api/whatsapp/subscribed-apps', methods=['GET', 'POST'])
def whatsapp_subscribed_apps():
    """Diagnostico (protegido por ADMIN_TOKEN): consulta (GET) o suscribe (POST)
    la app actual a los webhooks de una WABA especifica via /subscribed_apps.
    Configurar la URL/token del webhook a nivel de app NO alcanza -- cada WABA
    debe tener la app suscrita explicitamente ahi para que reenvie sus eventos."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json if request.method == 'POST' else request.args
    waba_id = (data.get('waba_id') or '').strip()
    if not waba_id:
        return jsonify({'ok': False, 'error': 'Falta waba_id'}), 400
    cfg = wa_cloud._config()
    if not cfg['token']:
        return jsonify({'ok': False, 'error': 'Falta WA_TOKEN'}), 400
    url = f"https://graph.facebook.com/{cfg['api_version']}/{waba_id}/subscribed_apps"
    headers = {'Authorization': f"Bearer {cfg['token']}"}
    try:
        if request.method == 'GET':
            resp = wa_cloud.requests.get(url, headers=headers, timeout=10)
        else:
            resp = wa_cloud.requests.post(url, headers=headers, timeout=10)
        return jsonify({'ok': resp.status_code < 400, 'status_code': resp.status_code, 'body': resp.json()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 502

@app.route('/api/whatsapp/plantillas', methods=['GET'])
def whatsapp_plantillas():
    """Diagnostico (protegido por ADMIN_TOKEN): lista las plantillas de la WABA
    con su estado, categoria y el LARGO del cuerpo en caracteres.

    Motivo del largo: WhatsApp colapsa el cuerpo con un "Leer mas" cuando se
    pasa de cierto tamanio (Meta no publica el umbral). Comparar contra las
    plantillas que ya funcionan sin truncarse da el limite empirico -- es la
    unica forma honesta de saber cuanto hay que recortar. El cuerpo vive solo
    en Meta, no en este repo, por eso hay que preguntarselo a la Graph API.

    Query: waba_id (default: WA_WABA_ID, y si tampoco esta, la WABA real)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    # El ID de la WABA real queda como ultimo recurso. NO es un secreto (sin WA_TOKEN
    # no sirve de nada), pero se puede sobrescribir con la env var WA_WABA_ID para no
    # tenerlo fijo en un repo publico. Ver DATOS-PRIVADOS.md.
    waba_id = (request.args.get('waba_id') or os.environ.get('WA_WABA_ID')
               or '106738482086473').strip()
    cfg = wa_cloud._config()
    if not cfg['token']:
        return jsonify({'ok': False, 'error': 'Falta WA_TOKEN'}), 400

    url = f"https://graph.facebook.com/{cfg['api_version']}/{waba_id}/message_templates"
    params = {'fields': 'name,status,category,language,components', 'limit': 100}
    headers = {'Authorization': f"Bearer {cfg['token']}"}
    try:
        resp = wa_cloud.requests.get(url, params=params, headers=headers, timeout=15)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 502
    if resp.status_code >= 400:
        return jsonify({'ok': False, 'error': f'Meta respondio {resp.status_code}: {resp.text[:300]}'}), 502

    out = []
    for t in (resp.json() or {}).get('data', []):
        cuerpo = ''
        n_botones = 0
        for c in t.get('components', []) or []:
            if c.get('type') == 'BODY':
                cuerpo = c.get('text') or ''
            elif c.get('type') == 'BUTTONS':
                n_botones = len(c.get('buttons') or [])
        out.append({
            'nombre': t.get('name'),
            'estado': t.get('status'),
            'categoria': t.get('category'),
            'idioma': t.get('language'),
            # Largo del cuerpo CRUDO (con los {{n}} sin reemplazar): al enviarlo
            # los valores reales suelen ser mas largos que el placeholder, asi
            # que este numero es un piso, no el largo final.
            'largo_cuerpo': len(cuerpo),
            'botones': n_botones,
            'cuerpo': cuerpo,
        })
    out.sort(key=lambda x: x['largo_cuerpo'], reverse=True)
    return jsonify({'ok': True, 'waba_id': waba_id, 'plantillas': out})


@rate_limit('20 per minute')
@app.route('/api/agenda/citas-futuras', methods=['GET'])
def agenda_citas_futuras():
    """Citas activas futuras del paciente (por RUT), para avisar de doble
    agendamiento. Escaneo en segundo plano desde el frontend (tarda unos segundos)."""
    rut = request.args.get('rut', '')
    if not scheduling.rut_valido(rut):
        return jsonify({'ok': False, 'error': 'RUT invalido'}), 400
    cfg = scheduling.load_config()
    try:
        citas = dentidesk.citas_futuras_paciente(rut, cfg)
    except Exception:
        citas = []
    return jsonify({'ok': True, 'citas': citas})

@rate_limit('120 per minute')
@app.route('/api/agenda/evento', methods=['POST'])
def agenda_evento():
    """Telemetria anonima del flujo de agendamiento (para el embudo). Sin datos
    personales: solo un id de sesion anonimo, el paso, y latencia opcional."""
    data = request.json or {}
    import stats as _stats
    ok = _stats.registrar_evento(data.get('sesion', ''), data.get('paso', ''),
                                 data.get('ms'))
    return jsonify({'ok': bool(ok)})

@app.route('/api/agenda/stats', methods=['GET'])
def agenda_stats():
    """Estadisticas de agendamiento (para el panel). Protegido por ADMIN_TOKEN.
    Funciona en produccion (Render) porque la data vive en el disco persistente.
    Filtros opcionales: ?desde=YYYY-MM-DD&hasta=YYYY-MM-DD (por fecha de la cita)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    import stats as _stats
    def _parse(s):
        try:
            return datetime.strptime(s, '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return None
    desde = _parse(request.args.get('desde'))
    hasta = _parse(request.args.get('hasta'))
    return jsonify({'ok': True,
                    **_stats.resumen(desde=desde, hasta=hasta),
                    'funnel': _stats.resumen_funnel(desde=desde, hasta=hasta)})

@app.route('/api/agenda/stats/citas', methods=['GET'])
def agenda_stats_citas():
    """Ultimas N reservas registradas (protegido por ADMIN_TOKEN), para revisar
    o eliminar del registro una reserva de prueba que altera las estadisticas.
    ?n=20 (default 20)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    import stats as _stats
    try:
        n = min(max(int(request.args.get('n', 20)), 1), 200)
    except ValueError:
        n = 20
    return jsonify({'ok': True, 'citas': _stats.ultimos(n)})

@app.route('/api/agenda/stats/citas', methods=['DELETE'])
def agenda_stats_citas_eliminar():
    """Elimina una reserva del registro de estadisticas por su 'ts' (protegido
    por ADMIN_TOKEN). No toca DentiDesk ni la agenda real -- solo el archivo de
    estadisticas locales."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    import stats as _stats
    data = request.json or {}
    ts = (data.get('ts') or '').strip()
    if not ts:
        return jsonify({'ok': False, 'error': 'Falta ts'}), 400
    eliminados = _stats.eliminar(ts)
    return jsonify({'ok': True, 'eliminados': eliminados})

@app.route('/api/scheduling-config', methods=['GET'])
def get_scheduling_config():
    """Para el panel admin: devuelve doctores, motivos, especialidades y reglas."""
    cfg = scheduling.load_config()
    doctores = {k: v for k, v in cfg['doctores'].items()
                if not k.startswith('_') and isinstance(v, dict)}
    motivos = [
        {'key': k, 'label': v.get('label',''), 'especialidad': v.get('especialidad',''),
         'duracion_min': v.get('duracion_min', 15), 'urgencia': bool(v.get('urgencia')),
         'notificar_agenda': bool(v.get('notificar_agenda')),
         'id_reason': v.get('id_reason', '')}
        for k, v in cfg['motivos'].items()
        if not k.startswith('_') and isinstance(v, dict)
    ]
    especialidades = [
        {'key': k, 'label': v.get('label', '')}
        for k, v in cfg['especialidades'].items()
        if not k.startswith('_') and isinstance(v, dict)
    ]
    return jsonify({
        'doctores': doctores,
        'motivos': motivos,
        'especialidades': especialidades,
        'reglas': cfg['reglas'],
        'sabias_que': [s for s in (cfg.get('sabias_que') or []) if isinstance(s, str)],
        'dentidesk_enabled': cfg['dentidesk']['enabled'],
    })

@app.route('/api/scheduling-config', methods=['POST'])
def set_scheduling_config():
    """Guarda cambios de doctores, motivos, especialidades y reglas (sin tocar codigo)."""
    data = request.json or {}
    cfg = scheduling.load_config()

    if 'anticipacion_minima_horas' in data:
        cfg['reglas']['anticipacion_minima_horas'] = max(0, int(data['anticipacion_minima_horas']))

    if 'sabias_que' in data:
        cfg['sabias_que'] = [str(s).strip() for s in (data['sabias_que'] or [])
                             if str(s).strip()][:30]

    for doc_id, doc_changes in (data.get('doctores') or {}).items():
        if doc_id not in cfg['doctores']:
            continue
        if 'atiende' in doc_changes:
            cfg['doctores'][doc_id]['atiende'] = bool(doc_changes['atiende'])
        for franja, valor in (doc_changes.get('ocupacion') or {}).items():
            if franja in cfg['doctores'][doc_id]['ocupacion']:
                cfg['doctores'][doc_id]['ocupacion'][franja] = max(0, min(100, int(valor)))

    # Motivos: reemplazar por la lista completa recibida del panel.
    if 'motivos' in data:
        comment = cfg['motivos'].get('_comment', '')
        new_motivos = {}
        if comment:
            new_motivos['_comment'] = comment
        for m in (data['motivos'] or []):
            key = (m.get('key') or '').strip()
            if not key:
                continue
            new_motivos[key] = {
                'label':           m.get('label', ''),
                'especialidad':    m.get('especialidad', 'ortodoncia'),
                'id_reason':       str(m.get('id_reason', '')),
                'duracion_min':    max(5, int(m.get('duracion_min') or 15)),
                'urgencia':        bool(m.get('urgencia')),
                'notificar_agenda': bool(m.get('notificar_agenda')),
            }
        cfg['motivos'] = new_motivos

    # Especialidades: reemplazar por la lista recibida del panel.
    if 'especialidades' in data:
        comment = cfg['especialidades'].get('_comment', '')
        new_esp = {}
        if comment:
            new_esp['_comment'] = comment
        for e in (data['especialidades'] or []):
            key = (e.get('key') or '').strip()
            if not key:
                continue
            new_esp[key] = {'label': e.get('label', '')}
        cfg['especialidades'] = new_esp

    scheduling.save_config(cfg)
    return jsonify({'ok': True})

# ══════════════════════════════════════════════════════════════════════════════
# WHATSAPP — recordatorios automaticos (config + estado + prueba manual)
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/whatsapp/config', methods=['GET'])
def get_whatsapp_config():
    """Protegido por ADMIN_TOKEN: toggles/hora de cada recordatorio."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    return jsonify({'ok': True, 'config': recordatorios_wa.load_config()})

@app.route('/api/whatsapp/config', methods=['POST'])
def set_whatsapp_config():
    """Guarda cambios parciales (activo/hora por tipo). Toma efecto de inmediato
    -- no requiere deploy, vive en el disco persistente."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    cfg = recordatorios_wa.save_config(data)
    return jsonify({'ok': True, 'config': cfg})

@app.route('/api/whatsapp/estado', methods=['GET'])
def get_whatsapp_estado():
    """Chequeo en vivo contra Meta (sin enviar mensajes) + timestamps del
    ultimo envio de cada tipo, para el indicador del panel."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    return jsonify({'ok': True, **recordatorios_wa.estado()})

@app.route('/api/whatsapp/recordatorios/run', methods=['POST'])
def whatsapp_recordatorios_run():
    """Dispara manualmente una pasada de los 3 recordatorios (protegido por
    ADMIN_TOKEN). Sirve para probar la logica sin esperar la hora configurada
    -- ignora el toggle 'activo' de cada tipo para poder probarlos aunque
    esten apagados; SI respeta el registro anti-duplicados."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    cfg = scheduling.load_config()
    if not cfg['dentidesk']['enabled']:
        return jsonify({'ok': False, 'error': 'Modo demo: sin credenciales DentiDesk (enabled=false)'}), 400
    return jsonify({
        'ok': True,
        'semana': recordatorios_wa.enviar_recordatorios_semana(cfg),
        'dia': recordatorios_wa.enviar_recordatorios_dia(cfg),
        'inasistencia': recordatorios_wa.enviar_inasistencias(cfg),
    })

def _verificar_firma_meta(req):
    """Valida X-Hub-Signature-256 (HMAC-SHA256 del body crudo con WA_APP_SECRET).
    Sin esto, cualquiera que descubra la URL del webhook podria mandar un
    Confirmo/Anular falso y anular una cita real. Si WA_APP_SECRET no esta
    configurado, se rechaza TODO (fail-closed, no fail-open)."""
    secret = os.environ.get('WA_APP_SECRET', '').strip()
    if not secret:
        return False
    firma = req.headers.get('X-Hub-Signature-256', '')
    if not firma.startswith('sha256='):
        return False
    esperado = hmac.new(secret.encode('utf-8'), req.get_data(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(firma[len('sha256='):], esperado)

@app.route('/api/whatsapp/webhook', methods=['GET'])
def whatsapp_webhook_verify():
    """Handshake que exige Meta al configurar el webhook en su panel."""
    verify_token = os.environ.get('WA_VERIFY_TOKEN', '')
    if (verify_token and request.args.get('hub.mode') == 'subscribe'
            and request.args.get('hub.verify_token') == verify_token):
        return request.args.get('hub.challenge', ''), 200
    return 'Forbidden', 403

@app.route('/api/whatsapp/webhook', methods=['POST'])
def whatsapp_webhook_recibir():
    """Recibe los eventos de WhatsApp (botones tocados por el paciente).
    Responde 200 siempre y rapido -- si el procesamiento interno falla, se
    loguea pero NO se re-lanza (Meta reintenta agresivamente si no ve 200)."""
    if not _verificar_firma_meta(request):
        return jsonify({'ok': False, 'error': 'Firma invalida'}), 403
    data = request.get_json(silent=True) or {}
    try:
        webhook_wa.procesar_evento(data, scheduling.load_config())
    except Exception as e:
        print('[webhook whatsapp] error:', e)
    return jsonify({'ok': True})

# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# GALERÍA CLÍNICA — leer, agregar, eliminar, reordenar
# ══════════════════════════════════════════════════════════════════════════════

def _gallery_slides(soup):
    """Devuelve lista de {src, caption} de los slides del carrusel."""
    track = soup.find(id='galleryTrack')
    if not track:
        return []
    slides = []
    for slide in track.find_all(class_='gallery-slide'):
        img = slide.find('img')
        slides.append({
            'src':     img['src'] if img else '',
            'caption': slide.get('data-caption', ''),
        })
    return slides

def _gallery_write(soup, slides):
    """Reescribe los slides del carrusel y los onclick de los tags en index.html."""
    track = soup.find(id='galleryTrack')
    if not track:
        return
    track.clear()
    for i, s in enumerate(slides):
        slide_tag = soup.new_tag('div', attrs={'class': 'gallery-slide', 'data-caption': s['caption']})
        img_tag   = soup.new_tag('img', attrs={'src': s['src'], 'alt': s['caption']})
        slide_tag.append(img_tag)
        track.append(slide_tag)

    # Actualizar onclick de los tags que tenían galleryGoTo(...)
    # Buscamos divs clinic-feature que tengan onclick con galleryGoTo
    features = soup.select('.clinic-features .clinic-feature[onclick]')
    # Mapear caption → índice nuevo
    caption_to_idx = {s['caption']: i for i, s in enumerate(slides)}
    for feat in features:
        onclick = feat.get('onclick', '')
        # Extraer la caption que tenía este tag buscando el span
        span = feat.find('span')
        label = span.get_text(strip=True) if span else ''
        # Buscar si algún slide tiene caption que coincida con el label
        new_idx = next((caption_to_idx[c] for c in caption_to_idx
                        if label.lower() in c.lower() or c.lower() in label.lower()), None)
        if new_idx is not None:
            feat['onclick'] = f'galleryGoTo({new_idx})'

@app.route('/api/galeria', methods=['GET'])
def get_galeria():
    if EN_RENDER:
        return jsonify({'error': 'No disponible en producción'}), 403
    soup = read_html()
    return jsonify(_gallery_slides(soup))

@app.route('/api/galeria/agregar', methods=['POST'])
def agregar_foto_galeria():
    if EN_RENDER:
        return jsonify({'error': 'No disponible en producción'}), 403
    # Sube el archivo y lo inserta en el carrusel
    f       = request.files.get('file')
    caption = request.form.get('caption', '').strip()
    if not f or not caption:
        return jsonify({'ok': False, 'error': 'Faltan datos'})
    # Nombre de archivo seguro
    ext      = os.path.splitext(f.filename)[1].lower() or '.jpg'
    slug     = re.sub(r'[^a-z0-9-]', '', caption.lower().replace(' ', '-'))
    filename = f'clinica-{slug}{ext}'
    f.save(str(IMAGES / filename))
    soup   = read_html()
    slides = _gallery_slides(soup)
    slides.append({'src': f'images/{filename}', 'caption': caption})
    _gallery_write(soup, slides)
    write_html(soup)
    return jsonify({'ok': True, 'slides': slides})

@app.route('/api/galeria/eliminar', methods=['POST'])
def eliminar_foto_galeria():
    if EN_RENDER:
        return jsonify({'error': 'No disponible en producción'}), 403
    data  = request.json
    idx   = data.get('idx')
    soup  = read_html()
    slides = _gallery_slides(soup)
    if idx is None or not (0 <= idx < len(slides)):
        return jsonify({'ok': False, 'error': 'Índice inválido'})
    slides.pop(idx)
    _gallery_write(soup, slides)
    write_html(soup)
    return jsonify({'ok': True, 'slides': slides})

@app.route('/api/galeria/renombrar', methods=['POST'])
def renombrar_foto_galeria():
    if EN_RENDER:
        return jsonify({'error': 'No disponible en producción'}), 403
    data    = request.json
    idx     = data.get('idx')
    caption = (data.get('caption') or '').strip()
    if idx is None or not caption:
        return jsonify({'ok': False, 'error': 'Faltan datos'})
    soup   = read_html()
    slides = _gallery_slides(soup)
    if not (0 <= idx < len(slides)):
        return jsonify({'ok': False, 'error': 'Índice inválido'})
    slides[idx]['caption'] = caption
    _gallery_write(soup, slides)
    write_html(soup)
    return jsonify({'ok': True, 'slides': slides})

@app.route('/api/galeria/reordenar', methods=['POST'])
def reordenar_galeria():
    if EN_RENDER:
        return jsonify({'error': 'No disponible en producción'}), 403
    data   = request.json        # {'orden': [2, 0, 1, 3, ...]}
    orden  = data.get('orden', [])
    soup   = read_html()
    slides = _gallery_slides(soup)
    if sorted(orden) != list(range(len(slides))):
        return jsonify({'ok': False, 'error': 'Orden inválido'})
    slides = [slides[i] for i in orden]
    _gallery_write(soup, slides)
    write_html(soup)
    return jsonify({'ok': True, 'slides': slides})

# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# ASISTENTE F2 — confirmar una cita puntual al instante
# ══════════════════════════════════════════════════════════════════════════════

def _enmascarar_email(email):
    """ma***@gm***.cl — mismo criterio que pacientes.display()."""
    if not email or '@' not in email:
        return email or ''
    local, domain = email.split('@', 1)
    def mask(s, keep=2):
        return s[:keep] + '***' if len(s) > keep else s
    parts = domain.rsplit('.', 1)
    dom_masked = mask(parts[0], 2) + ('.' + parts[1] if len(parts) > 1 else '')
    return mask(local, 2) + '@' + dom_masked


@app.route('/api/reporte/evoluciones', methods=['POST'])
def reporte_evoluciones():
    """Recibe el reporte diario de revision de evoluciones (generado por el
    sistema local de las 6:15) y lo envia por email al destinatario fijo
    (env REPORTE_EVOLUCIONES_EMAIL, default alberto@delreal.cl).

    Body JSON: { "asunto": "...", "html": "..." }
    El destinatario NO viene en el body (no es un relay abierto).
    Protegido por ADMIN_TOKEN."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    asunto = (data.get('asunto') or '').strip()
    html = (data.get('html') or '').strip()
    if not asunto or not html:
        return jsonify({'ok': False, 'error': 'Faltan asunto o html'}), 400
    if notify.enviar_reporte_evoluciones(asunto, html):
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'SMTP no configurado o fallo el envio'}), 502


@app.route('/api/reporte/evoluciones-rodrigo', methods=['POST'])
def reporte_evoluciones_rodrigo():
    """Recibe el reporte diario de fichas SIN evolucion escrita del Dr. Rodrigo
    Oyonarte y lo envia por email al destinatario fijo (env
    REPORTE_EVOLUCIONES_RODRIGO_EMAIL, default royonarte@miuandes.cl). Solo
    lista de fichas faltantes, sin seccion de oportunidades de contacto.

    Body JSON: { "asunto": "...", "html": "..." }
    El destinatario NO viene en el body (no es un relay abierto).
    Protegido por ADMIN_TOKEN."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    asunto = (data.get('asunto') or '').strip()
    html = (data.get('html') or '').strip()
    if not asunto or not html:
        return jsonify({'ok': False, 'error': 'Faltan asunto o html'}), 400
    if notify.enviar_reporte_evoluciones_rodrigo(asunto, html):
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'SMTP no configurado o fallo el envio'}), 502


@app.route('/api/reporte/alineadores', methods=['POST'])
def reporte_alineadores():
    """Recibe el reporte de pacientes con alineadores (Digitrack/Invisalign) con
    9+ meses de tratamiento agendados para el dia siguiente, y lo envia por email
    al destinatario fijo (env REPORTE_ALINEADORES_EMAIL, default
    recepcion@ortodonciarichard.cl). Aviso anticipado de la politica de cuota
    mensual tras 12 meses de tratamiento.

    Body JSON: { "asunto": "...", "html": "..." }
    El destinatario NO viene en el body (no es un relay abierto).
    Protegido por ADMIN_TOKEN."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    asunto = (data.get('asunto') or '').strip()
    html = (data.get('html') or '').strip()
    if not asunto or not html:
        return jsonify({'ok': False, 'error': 'Faltan asunto o html'}), 400
    if notify.enviar_reporte_alineadores(asunto, html):
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'SMTP no configurado o fallo el envio'}), 502


@app.route('/api/asistente/confirmar-cita', methods=['POST'])
def asistente_confirmar_cita():
    """
    Envía la confirmación de una cita puntual al instante (sin esperar el barrido).
    Llamado por el asistente F2 desde el navegador de la secretaria.

    Body JSON: { "id_agenda": "13350327", "fecha": "2026-06-26", "canal": "email"|"whatsapp" }
    "canal" es opcional: si no se manda, es automatico (email, con WhatsApp de
    respaldo) — igual que el agendamiento online. Si se manda, la secretaria
    eligio el canal a mano desde el panel F2 y se fuerza ese unico canal.
    Protegido por ADMIN_TOKEN (mismo patrón que el resto de endpoints sensibles).

    Flujo:
      1. Trae las citas del día desde DentiDesk (getAgendaDay, con caché).
      2. Localiza la cita por IdAgenda.
      3. Valida que no esté inactiva (cancelada, atendida, etc.) y que tenga el
         dato que exige el canal elegido (email para 'email'/automatico,
         telefono para 'whatsapp').
      4. Llama a notify.enviar_confirmacion() con ese canal.
      5. Marca la cita como enviada (marcar_enviada) para que el barrido no la reenvíe.
      6. Devuelve { ok, canal, email_enmascarado } o { ok: false, error }.
    """
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403

    data = request.json or {}
    id_agenda = str(data.get('id_agenda', '')).strip()
    fecha_str  = (data.get('fecha') or '').strip()
    canal = (data.get('canal') or '').strip().lower() or None

    if not id_agenda:
        return jsonify({'ok': False, 'error': 'Falta id_agenda'}), 400
    if canal not in (None, 'email', 'whatsapp'):
        return jsonify({'ok': False, 'error': f'Canal no válido: {canal}'}), 400
    try:
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'Fecha inválida (esperado YYYY-MM-DD)'}), 400

    cfg = scheduling.load_config()

    # Modo mock (sin credenciales DentiDesk): confirmar con datos simulados
    if not cfg['dentidesk']['enabled']:
        import confirmaciones
        confirmaciones.marcar_enviada(id_agenda)
        return jsonify({
            'ok': True, 'mock': True,
            'canal': canal or 'email',
            'email_enmascarado': 'pa***@co***.cl',
            'mensaje': 'Modo demo: sin credenciales DentiDesk (enabled=false)',
        })

    # Traer la agenda del día FRESCA (sin caché). El asistente F2 se usa justo
    # después de editar/guardar la cita en DentiDesk; con caché podríamos leer
    # datos viejos (sin el email recién agregado) o no ver una cita recién creada.
    try:
        citas_dia = dentidesk._get_agenda_day(cfg, fecha, force=True)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Error al consultar DentiDesk: {e}'}), 502

    # Buscar la cita por IdAgenda
    cita_raw = next(
        (c for c in citas_dia if str(c.get('IdAgenda', '')) == id_agenda),
        None
    )
    if not cita_raw:
        return jsonify({'ok': False, 'error': f'No se encontró la cita {id_agenda} en la agenda del {fecha_str}'}), 404

    # Validar que no esté inactiva (cancelada, atendida, etc.)
    estado = (cita_raw.get('Status') or '').lower()
    if any(s in estado for s in dentidesk._ESTADOS_INACTIVOS):
        return jsonify({'ok': False, 'error': f'La cita está en estado "{cita_raw.get("Status")}" — no se envía confirmación'}), 409

    # Email. Prioridad: el de DentiDesk (recién guardado); si la cita no lo
    # tiene, usar el que el asistente leyó del modal (data['email']) como
    # respaldo. Esto cubre el caso de una cita antigua a la que recién se le
    # agregó el email en el modal y el cambio aún no se refleja del lado servidor.
    email = (cita_raw.get('PatientEmail') or '').strip()
    if '@' not in email:
        email = (data.get('email') or '').strip()
    telefono = (cita_raw.get('Phone') or '').strip()

    # El dato requerido depende del canal que la secretaria eligió en F2
    # (automatico/'email' necesitan email; 'whatsapp' necesita telefono).
    if canal == 'whatsapp':
        if not telefono:
            return jsonify({'ok': False, 'error': 'La cita no tiene teléfono registrado. Agrégalo en DentiDesk, guarda, y vuelve a intentar.'}), 409
    else:
        if '@' not in email or '.' not in email:
            return jsonify({'ok': False, 'error': 'La cita no tiene email registrado. Agrégalo en DentiDesk, guarda, y vuelve a intentar.'}), 409

    # Armar el dict para notify.enviar_confirmacion()
    import pacientes as _pacientes
    nombres_raw, _ = _pacientes._split_nombre(cita_raw.get('PatientName', ''))
    nombre = nombres_raw or 'Paciente'

    try:
        fch = datetime.strptime(cita_raw.get('Date', fecha_str), '%Y-%m-%d').date()
    except ValueError:
        fch = fecha

    cita_dict = {
        'nombre':        nombre,
        'telefono':      telefono,
        'email':         email,
        'fecha':         fch,
        'fecha_legible': _fecha_legible(fch),
        'hora':          (cita_raw.get('time') or '')[:5],
        'doctor_nombre': (cita_raw.get('ProfessionalName') or '').strip(),
        'motivo_label':  (cita_raw.get('Reason') or 'Cita').strip(),
        'dur_min':       int(cita_raw.get('duration') or 30),
        'id_agenda':     id_agenda,
    }

    # canal=None -> automatico (email, con WhatsApp de respaldo); si la
    # secretaria eligio uno en F2, se fuerza ese unico canal.
    # Primera consulta: usa la plantilla con video. Si la secretaria NO eligio
    # canal, se manda por ambos para que el video efectivamente salga (con el
    # automatico el email tapa al WhatsApp). Si eligio uno, se respeta.
    es_primera = dentidesk.es_primera_consulta(cfg, cita_raw.get('Reason'))
    resultado = notify.enviar_confirmacion(
        cita_dict, cfg,
        canal=('ambos' if (es_primera and not canal) else canal),
        primera=es_primera)

    # Marcar como enviada para que el barrido de 4 ciclos no la reenvíe
    if resultado.get('ok'):
        import confirmaciones
        confirmaciones.marcar_enviada(id_agenda)

    return jsonify({
        'ok':               resultado.get('ok', False),
        'canal':            resultado.get('canal'),
        'email_enmascarado': _enmascarar_email(email),
        'error':            resultado.get('error'),
        'cita': {
            'nombre':   nombre,
            'fecha':    fch.isoformat(),
            'hora':     cita_dict['hora'],
            'doctor':   cita_dict['doctor_nombre'],
            'motivo':   cita_dict['motivo_label'],
        }
    })


# ══════════════════════════════════════════════════════════════════════════════
# RECAPTACION — recordatorio de control (recordatorios_wa.py escanea solo;
# aca la secretaria dispara UN envio puntual desde la ULTIMA cita del
# paciente, abierta en DentiDesk con F2)
# ══════════════════════════════════════════════════════════════════════════════

def _resolver_cita_recordatorio_control(cfg, id_agenda, fecha, fecha_str):
    """Logica COMPARTIDA por el envio inmediato (asistente_recordatorio_control)
    y el envio PROGRAMADO (asistente_recordatorio_control_programar): trae la
    cita fresca de DentiDesk (o la simula en modo mock), saca rut/nombre/
    doctor/telefono y valida que el telefono sea un celular chileno.

    Devuelve (datos, None) si todo OK, o (None, (response, status)) si hay
    que cortar ahi mismo -- el llamador solo necesita "if err: return err".
    """
    import pacientes as _pacientes

    # Modo mock (sin credenciales DentiDesk): cita simulada -- evita llamar a
    # _get_agenda_day sin credenciales reales. Las guardas de recaptacion
    # basadas en registro (no_molestar/enviado_reciente) SI corren normal en
    # este modo -- son locales, no dependen de DentiDesk.
    if not cfg['dentidesk']['enabled']:
        rut = f'MOCK{id_agenda}'
        nombre = 'Paciente'
        doctor = 'Dr. Patricio Vial'
        telefono = '+56 9 1111 2222'
    else:
        try:
            citas_dia = dentidesk._get_agenda_day(cfg, fecha, force=True)
        except Exception as e:
            return None, (jsonify({'ok': False, 'error': f'Error al consultar DentiDesk: {e}'}), 502)

        cita_raw = next(
            (c for c in citas_dia if str(c.get('IdAgenda', '')) == id_agenda),
            None
        )
        if not cita_raw:
            return None, (jsonify({'ok': False, 'error': f'No se encontró la cita {id_agenda} en la agenda del {fecha_str}'}), 404)

        rut = (cita_raw.get('PatientDocument') or '').strip()
        nombres_raw, _ = _pacientes._split_nombre(cita_raw.get('PatientName', ''))
        nombre = nombres_raw or 'Paciente'
        doctor = (cita_raw.get('ProfessionalName') or '').strip()
        telefono = (cita_raw.get('Phone') or '').strip()

    # Telefono: celular chileno E.164 sin '+' (569XXXXXXXX, 11 digitos). Dato
    # objetivamente invalido -- ni forzar lo salta (puede_forzar: False).
    tel_norm = wa_cloud._normalizar_telefono(telefono)
    if len(tel_norm) != 11 or not tel_norm.startswith('569'):
        return None, (jsonify({
            'ok': False,
            'error': 'La cita no tiene un celular chileno válido registrado (formato 9XXXXXXXX). Agrégalo en DentiDesk, guarda, y vuelve a intentar.',
            'puede_forzar': False,
        }), 400)

    return {'rut': rut, 'nombre': nombre, 'doctor': doctor, 'telefono': telefono}, None


@app.route('/api/asistente/recordatorio-control', methods=['POST'])
def asistente_recordatorio_control():
    """
    Envia el recordatorio de control (recaptacion) de un paciente que dejo de
    venir. Llamado por el asistente F2 con la ULTIMA cita del paciente abierta
    en DentiDesk -- todos los datos (telefono, nombre, doctor) salen de esa
    cita, no hay escaneo.

    Body JSON: { "id_agenda": "13350327", "fecha": "2026-04-01", "forzar": false }
    "forzar" (opcional): salta las guardas ya_tiene_hora / enviado_reciente,
    pero NUNCA no_molestar ni la validacion de telefono -- esas dos son
    objetivas (dato invalido / decision explicita de no contactar), no
    "por si las dudas".
    Protegido por ADMIN_TOKEN (mismo patron que asistente_confirmar_cita).

    Flujo:
      1. Trae la cita del dia desde DentiDesk (getAgendaDay, FRESCA, force=True)
         -- misma razon que asistente_confirmar_cita: F2 se usa justo despues
         de abrir/revisar la cita, no hay que arriesgarse a cache vieja.
      2. Localiza la cita por IdAgenda; saca telefono/nombre/doctor/RUT.
      3. Valida el telefono (celular chileno, 569XXXXXXXX) -- si no, 400.
      4. recaptacion.evaluar(rut) -- salvo forzar, cualquier guarda bloquea
         (409); con forzar, solo no_molestar sigue bloqueando.
      5. Envia con notify.enviar_recordatorio_control() y, si sale ok,
         recaptacion.marcar_enviado() (no se marca si el envio fallo -- asi
         un reintento no queda contaminado por un "enviado" que no llego).
      6. Devuelve { ok, telefono_enmascarado, nombre, doctor, fecha_legible }
         (+ 'advertencia' si la fecha de la cita es futura -- no bloquea, es
         un dato raro para un recordatorio de CONTROL pero no un error).
    """
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403

    data = request.json or {}
    id_agenda = str(data.get('id_agenda', '')).strip()
    fecha_str = (data.get('fecha') or '').strip()
    forzar = bool(data.get('forzar', False))

    if not id_agenda:
        return jsonify({'ok': False, 'error': 'Falta id_agenda'}), 400
    try:
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'Fecha inválida (esperado YYYY-MM-DD)'}), 400

    cfg = scheduling.load_config()
    import pacientes as _pacientes

    datos, err = _resolver_cita_recordatorio_control(cfg, id_agenda, fecha, fecha_str)
    if err:
        return err
    rut, nombre, doctor, telefono = datos['rut'], datos['nombre'], datos['doctor'], datos['telefono']

    advertencia = None
    if fecha > fechas.hoy_chile():
        advertencia = 'La fecha de la cita de origen es futura -- verifica que sea la cita correcta.'

    # recaptacion.evaluar() siempre corre (para saber si no_molestar aplica);
    # con forzar=True se ignoran las otras dos guardas.
    bloqueo = recaptacion.evaluar(rut)
    if bloqueo and (not forzar or bloqueo['motivo'] == 'no_molestar'):
        return jsonify({'ok': False, **bloqueo}), 409

    fecha_legible = recaptacion.fecha_legible_larga(fecha)
    cita_dict = {
        'nombre': nombre,
        'telefono': telefono,
        'doctor_nombre': doctor,
        'fecha_legible': fecha_legible,
        'fecha': fecha.isoformat(),
        'id_agenda': id_agenda,
    }
    resultado = notify.enviar_recordatorio_control(cita_dict)
    if not resultado.get('ok'):
        return jsonify({'ok': False, 'error': resultado.get('error') or 'No se pudo enviar el WhatsApp'}), 502

    recaptacion.marcar_enviado(rut, id_agenda, doctor, nombre)

    respuesta = {
        'ok': True,
        'telefono_enmascarado': _pacientes.enmascarar_telefono(telefono),
        'nombre': nombre,
        'doctor': doctor,
        'fecha_legible': fecha_legible,
    }
    if advertencia:
        respuesta['advertencia'] = advertencia
    return jsonify(respuesta)


@app.route('/api/asistente/nps-override', methods=['POST'])
def asistente_nps_override():
    """F2: la asistente decide, para la cita abierta en DentiDesk, si se envia
    o NO la encuesta de satisfaccion (NPS) por esa cita.
      accion 'no_enviar' -> bloquea esta cita: el barrido nunca le manda.
      accion 'enviar'    -> fuerza el envio tras el tiempo planificado
                            (horas_despues + ventana horaria), aunque el
                            automatico no la habria tomado (motivo no-hito,
                            cooldown). Respeta 'no molestar'; salta la
                            elegibilidad por tipo y el cooldown.
    Body JSON: { id_agenda, fecha: 'YYYY-MM-DD', accion }. Resuelve la cita
    FRESCA de DentiDesk (telefono/nombre/doctor/hora/duracion) y guarda el
    override -- mismo criterio de lectura fresca que asistente_confirmar_cita.
    Protegido por ADMIN_TOKEN."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403

    data = request.json or {}
    id_agenda = str(data.get('id_agenda', '')).strip()
    fecha_str = (data.get('fecha') or '').strip()
    accion = (data.get('accion') or '').strip()

    if not id_agenda:
        return jsonify({'ok': False, 'error': 'Falta id_agenda'}), 400
    if accion not in ('enviar', 'no_enviar'):
        return jsonify({'ok': False, 'error': 'accion invalida (enviar|no_enviar)'}), 400
    try:
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'Fecha inválida (esperado YYYY-MM-DD)'}), 400

    cfg = scheduling.load_config()
    import pacientes as _pacientes
    c = None
    try:
        c = dentidesk.info_cita(cfg, id_agenda, fecha)
    except Exception as e:
        print('[nps-override] no se pudo leer la cita:', e)

    telefono = nombre = doctor = rut = hora_cita = ''
    duracion = 0
    if c:
        telefono = (c.get('Phone') or '').strip()
        nombres, _ = _pacientes._split_nombre(c.get('PatientName') or '')
        nombre = nombres or ''
        doctor = (c.get('ProfessionalName') or '').strip()
        rut = dentidesk.limpiar_rut(str(c.get('PatientDocument') or ''))
        hora_cita = (c.get('time') or '')[:5]
        try:
            duracion = int(c.get('duration') or 0)
        except (TypeError, ValueError):
            duracion = 0

    # 'enviar' necesita telefono (si no, no hay a quien mandarle); 'no_enviar'
    # basta con el id_agenda (es un bloqueo, no un envio).
    if accion == 'enviar' and not telefono:
        return jsonify({'ok': False,
                        'error': 'La cita no tiene teléfono registrado (no se puede programar el envío)'}), 400

    nps.registrar_override(id_agenda, accion, rut=rut, telefono=telefono,
                            nombre=nombre, doctor=doctor,
                            fecha_cita=fecha.isoformat(), hora_cita=hora_cita,
                            duracion=duracion)

    resp = {'ok': True, 'accion': accion, 'nombre': nombre, 'doctor': doctor}
    if telefono:
        resp['telefono_enmascarado'] = _pacientes.enmascarar_telefono(telefono)
    return jsonify(resp)


@app.route('/api/recaptacion/config', methods=['GET'])
def get_recaptacion_config():
    """Protegido por ADMIN_TOKEN: dias_minimos_reenvio."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    return jsonify({'ok': True, 'config': recaptacion.load_config()})


@app.route('/api/recaptacion/config', methods=['POST'])
def set_recaptacion_config():
    """Guarda cambios parciales (dias_minimos_reenvio). Toma efecto de
    inmediato -- no requiere deploy, vive en el disco persistente."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    cfg = recaptacion.save_config(data)
    return jsonify({'ok': True, 'config': cfg})


@app.route('/api/recaptacion/historial', methods=['GET'])
def get_recaptacion_historial():
    """Envios de recordatorio de control, mas reciente primero (para la
    pestania del panel)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    limite = request.args.get('limite', 100, type=int)
    # Las dos listas viajan juntas a proposito: la pestania del panel pinta el
    # historial y la lista de "no molestar" en la misma card, con una sola
    # llamada (mismo criterio que el resto del panel remoto).
    return jsonify({'ok': True,
                    'envios': recaptacion.historial(limite),
                    'no_molestar': recaptacion.lista_no_molestar()})


@app.route('/api/recaptacion/no-molestar', methods=['POST'])
def set_recaptacion_no_molestar():
    """Agrega o quita un RUT de la lista de 'no molestar' (nunca recibe
    recordatorio de control, ni con forzar). Body: {rut, quitar?: bool}."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    rut = (data.get('rut') or '').strip()
    if not rut:
        return jsonify({'ok': False, 'error': 'Falta rut'}), 400
    if data.get('quitar'):
        lista = recaptacion.quitar_no_molestar(rut)
    else:
        lista = recaptacion.agregar_no_molestar(rut)
    return jsonify({'ok': True, 'no_molestar': lista})


@app.route('/api/asistente/recordatorio-control/programar', methods=['POST'])
def asistente_recordatorio_control_programar():
    """
    Programa el recordatorio de control para una fecha FUTURA en vez de
    mandarlo al instante -- el envio real lo hace el scheduler
    (_loop_recaptacion_programados) el dia elegido, a la hora
    'hora_envio_programados' (panel).

    Body JSON: { "id_agenda", "fecha" (de la cita de ORIGEN), "fecha_programada"
    (YYYY-MM-DD, hoy o futura), "forzar": false }

    Reusa _resolver_cita_recordatorio_control (misma logica que el envio
    inmediato: cita fresca de DentiDesk + validacion de telefono) y
    recaptacion.evaluar() con el mismo formato de bloqueo 409 -- la secretaria
    ve la MISMA advertencia ya tenga hora o se le haya mandado hace poco, solo
    que aca ademas puede forzar la PROGRAMACION (el reintento de verdad, el
    que importa, ocurre igual el dia del envio -- ver
    _procesar_programados_vencidos).
    """
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403

    data = request.json or {}
    id_agenda = str(data.get('id_agenda', '')).strip()
    fecha_str = (data.get('fecha') or '').strip()
    fecha_programada_str = (data.get('fecha_programada') or '').strip()
    forzar = bool(data.get('forzar', False))

    if not id_agenda:
        return jsonify({'ok': False, 'error': 'Falta id_agenda'}), 400
    try:
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'Fecha inválida (esperado YYYY-MM-DD)'}), 400
    try:
        fecha_programada = datetime.strptime(fecha_programada_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'Fecha programada inválida (esperado YYYY-MM-DD)'}), 400
    if fecha_programada < fechas.hoy_chile():
        return jsonify({'ok': False, 'error': 'La fecha programada no puede ser anterior a hoy'}), 400

    cfg = scheduling.load_config()
    datos, err = _resolver_cita_recordatorio_control(cfg, id_agenda, fecha, fecha_str)
    if err:
        return err
    rut, nombre, doctor, telefono = datos['rut'], datos['nombre'], datos['doctor'], datos['telefono']

    # Misma guarda que el envio inmediato: corre SIEMPRE (para detectar
    # no_molestar), y con forzar solo se saltan las otras dos.
    bloqueo = recaptacion.evaluar(rut)
    if bloqueo and (not forzar or bloqueo['motivo'] == 'no_molestar'):
        return jsonify({'ok': False, **bloqueo}), 409

    import pacientes as _pacientes
    recaptacion.programar(rut, id_agenda, fecha.isoformat(), doctor, nombre, fecha_programada_str)

    return jsonify({
        'ok': True,
        'fecha_programada': fecha_programada_str,
        'nombre': nombre,
        'doctor': doctor,
        'telefono_enmascarado': _pacientes.enmascarar_telefono(telefono),
    })


@app.route('/api/recaptacion/programados', methods=['GET'])
def get_recaptacion_programados():
    """Lista completa de programados (pendiente/enviado/anulado/omitido) para
    la pestania del panel."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    return jsonify({'ok': True, 'programados': recaptacion.listar_programados()})


@app.route('/api/recaptacion/programados/anular', methods=['POST'])
def anular_recaptacion_programado():
    """Anula un recordatorio programado (body: {id}). 404 si no existe o ya
    no esta pendiente (no tiene sentido 'anular' algo ya enviado/omitido)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    id_ = (data.get('id') or '').strip()
    if not id_:
        return jsonify({'ok': False, 'error': 'Falta id'}), 400
    if not recaptacion.anular_programado(id_):
        return jsonify({'ok': False, 'error': 'No se encontró un programado pendiente con ese id'}), 404
    return jsonify({'ok': True})


# ══════════════════════════════════════════════════════════════════════════════
# CONSENTIMIENTOS INFORMADOS — firma digital (celular o tablet de recepción)
# ══════════════════════════════════════════════════════════════════════════════

import consentimientos


def _check_kiosk_token():
    """Protege los endpoints que usa la tablet de recepción. Token propio
    (KIOSK_TOKEN), distinto de ADMIN_TOKEN: la tablet no debe tener el mismo
    nivel de acceso que el panel admin. Sin KIOSK_TOKEN configurado (dev local)
    se permite, igual que _check_admin_token."""
    tok = os.environ.get('KIOSK_TOKEN')
    if not tok:
        return True
    # Solo header (no ?kiosk_token= en la URL). Tiempo constante.
    provisto = request.headers.get('X-Kiosk-Token') or ''
    return hmac.compare_digest(provisto, tok)


@app.route('/consentimiento')
def consentimiento_page():
    return send_from_directory('.', 'consentimiento.html')


@app.route('/consentimiento/imprimir')
@rate_limit('20 per minute')
def consentimiento_pdf_blanco():
    """Versión 'en blanco' del consentimiento (sin datos de paciente), con el
    mismo estilo gráfico del formulario web, para que la clínica la imprima y
    la tenga disponible en recepción. No lleva datos personales — no requiere
    token ni ADMIN_TOKEN (mismo texto que cualquiera puede leer en /consentimiento)."""
    from flask import send_file
    tipo = request.args.get('tipo', 'ortodoncia')
    if tipo not in consentimientos.TIPOS_DOCUMENTO:
        return jsonify({'ok': False, 'error': 'tipo de documento desconocido'}), 400
    buf = consentimientos.generar_pdf_blanco(tipo)
    return send_file(buf, mimetype='application/pdf', as_attachment=False,
                     download_name=f'consentimiento-{tipo}-en-blanco.pdf')


@app.route('/api/consentimiento/datos', methods=['GET'])
@rate_limit('30 per minute')
def consentimiento_datos():
    """Prellenado para el modo celular: valida el token del link y devuelve
    nombre/RUT del paciente (sin email ni teléfono — datos_paciente() es la
    versión segura para exponer en un endpoint sin autenticación de admin)."""
    token = request.args.get('token', '')
    info = consentimientos.validar_token(token)
    if not info:
        return jsonify({'ok': False, 'error': 'Link inválido o vencido'}), 400
    datos = consentimientos.datos_paciente(info['rut'])
    if not datos:
        return jsonify({'ok': False, 'error': 'Paciente no encontrado'}), 404
    return jsonify({'ok': True, **datos, 'tipo': info['tipo']})


@app.route('/api/consentimiento/tablet/buscar', methods=['GET'])
@rate_limit('20 per minute')
def consentimiento_tablet_buscar():
    """Búsqueda manual por RUT en la tablet de recepción (walk-up, sin que la
    secretaria haya disparado un envío desde F2 primero)."""
    if not _check_kiosk_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    datos = consentimientos.datos_paciente(request.args.get('rut', ''))
    if not datos:
        return jsonify({'ok': False, 'error': 'No se encontró un paciente con ese RUT'}), 404
    return jsonify({'ok': True, **datos})


@app.route('/api/consentimiento/tablet/cola', methods=['GET'])
def consentimiento_tablet_cola():
    """Polling de la tablet: si la secretaria empujó un consentimiento desde F2
    (canal='tablet'), aquí aparece {rut, tipo, id} para que la tablet salte
    directo a la pantalla de confirmación de identidad."""
    if not _check_kiosk_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    return jsonify({'ok': True, 'item': consentimientos.obtener_cola_tablet()})


@app.route('/api/consentimiento/enviar', methods=['POST'])
@rate_limit('30 per minute')
def consentimiento_enviar():
    """Disparado desde el asistente F2. Body: {rut, tipo, canal}.
    canal: 'mail' | 'whatsapp' | 'tablet'. Protegido por ADMIN_TOKEN (mismo
    patrón que /api/asistente/confirmar-cita)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403

    data = request.json or {}
    rut = (data.get('rut') or '').strip()
    tipo = (data.get('tipo') or 'ortodoncia').strip()
    canal = (data.get('canal') or '').strip()
    id_agenda = str(data.get('id_agenda', '')).strip()
    fecha_str = (data.get('fecha') or '').strip()
    if canal not in ('mail', 'whatsapp', 'tablet'):
        return jsonify({'ok': False, 'error': "canal debe ser 'mail', 'whatsapp' o 'tablet'"}), 400
    if tipo not in consentimientos.TIPOS_DOCUMENTO:
        return jsonify({'ok': False, 'error': f'tipo de documento desconocido: {tipo}'}), 400

    cfg = scheduling.load_config()
    import pacientes as _pacientes

    # Contacto del paciente: preferir DentiDesk FRESCO (via id_agenda+fecha, igual
    # que /confirmar-cita) para tomar telefono/email recien agregados en la ficha;
    # si no se puede, caer a la base local (que se sincroniza 2x/dia). Antes solo
    # usaba la base local -> si el dato era recien agregado, fallaba.
    rec = None
    if id_agenda and fecha_str and cfg['dentidesk']['enabled']:
        try:
            _fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
            _cita = next((c for c in dentidesk._get_agenda_day(cfg, _fecha, force=True)
                          if str(c.get('IdAgenda', '')) == id_agenda), None)
            if _cita:
                _nom, _ape = _pacientes._split_nombre(_cita.get('PatientName', ''))
                rec = {'nombres': _nom, 'apellidos': _ape,
                       'email': (_cita.get('PatientEmail') or '').strip(),
                       'telefono': (_cita.get('Phone') or '').strip()}
                if not rut:
                    rut = _pacientes._limpiar_rut(str(_cita.get('PatientDocument', '')))
        except Exception as e:
            app.logger.warning('consentimiento: no se pudo leer DentiDesk fresco (%s); uso base local', e)

    if rec is None:
        rec = _pacientes.lookup(rut)
    if not rec:
        return jsonify({'ok': False, 'error': 'Paciente no encontrado. Verifica el RUT en DentiDesk.'}), 404
    if not rut:
        return jsonify({'ok': False, 'error': 'Falta el RUT del paciente'}), 400

    consent_id = consentimientos.crear_registro(rut, tipo, canal)

    if canal == 'tablet':
        consentimientos.poner_en_cola_tablet(rut, tipo, consent_id)
        return jsonify({'ok': True, 'canal': 'tablet', 'id': consent_id})

    token = consentimientos.generar_token(rut, tipo, consent_id)
    link = f"{request.url_root.rstrip('/')}/consentimiento?token={token}"
    tipo_label = consentimientos.TIPOS_DOCUMENTO[tipo]
    resultado = notify.enviar_link_consentimiento(rec, link, canal, tipo_label)
    return jsonify({'ok': resultado.get('ok', False), 'canal': resultado.get('canal', canal),
                    'id': consent_id, 'error': resultado.get('error')})


@app.route('/api/consentimiento/firmar', methods=['POST'])
@rate_limit('10 per minute')
def consentimiento_firmar():
    """Recibe la firma desde consentimiento.html (celular o tablet), genera el
    PDF y marca el registro como firmado.

    Modo celular: body incluye 'token' (del link recibido por mail/WhatsApp).
    Modo tablet: sin token — requiere KIOSK_TOKEN. Si 'id' viene seteado (la
    tablet lo tomó de la cola, empujada por F2) se usa ese registro; si no
    (walk-up manual en la tablet), se crea un registro nuevo canal='tablet'."""
    data = request.json or {}
    token = data.get('token', '')

    if token:
        info = consentimientos.validar_token(token)
        if not info:
            return jsonify({'ok': False, 'error': 'Link inválido o vencido'}), 400
        rut, tipo, consent_id = info['rut'], info['tipo'], info['id']
        de_cola = False
    else:
        if not _check_kiosk_token():
            return jsonify({'ok': False, 'error': 'No autorizado'}), 403
        rut = (data.get('rut') or '').strip()
        tipo = (data.get('tipo') or 'ortodoncia').strip()
        consent_id = data.get('id') or None
        de_cola = bool(consent_id)
        if not consent_id:
            consent_id = consentimientos.crear_registro(rut, tipo, 'tablet')

    # Validar tipo SIEMPRE (no solo en /enviar): en modo tablet 'tipo' viene del
    # body sin firmar. Sin esto, un valor arbitrario se guardaría en el registro
    # y el panel admin lo renderizaría → XSS almacenado contra la sesión admin.
    if tipo not in consentimientos.TIPOS_DOCUMENTO:
        return jsonify({'ok': False, 'error': 'tipo de documento desconocido'}), 400

    # Validar que la firma sea realmente un PNG en data URL (no un blob arbitrario).
    firma_png = data.get('firma_png') or ''
    if not firma_png.startswith('data:image/png;base64,'):
        return jsonify({'ok': False, 'error': 'Firma inválida'}), 400

    datos_pac = consentimientos.datos_paciente(rut)
    if not datos_pac:
        return jsonify({'ok': False, 'error': 'Paciente no encontrado'}), 404

    # IP real del firmante. ProxyFix ya resolvió request.remote_addr a la IP que
    # puso el proxy de Render — NO se lee X-Forwarded-For crudo (falsificable por
    # el cliente). Localmente será 127.0.0.1.
    ip_origen = request.remote_addr or ''

    # Acotar los campos de texto libre que van al PDF (defensa extra sobre
    # MAX_CONTENT_LENGTH; evita PDFs desmesurados por un solo campo).
    def _cap(v, n=300):
        return (str(v or '')).strip()[:n]

    pdf_datos = {
        **datos_pac, 'tipo': tipo,
        'tratamiento':       _cap(data.get('tratamiento')),
        'dentista_actual':   _cap(data.get('dentista_actual')),
        'quien_firma':       'apoderado' if data.get('quien_firma') == 'apoderado' else 'paciente',
        'apoderado_nombre':  _cap(data.get('apoderado_nombre'), 120),
        'apoderado_rut':     _cap(data.get('apoderado_rut'), 20),
        'fecha':             _cap(data.get('fecha'), 40) or fechas.hoy_chile().isoformat(),
        'firma_png':         firma_png,
        'consent_id':        consent_id,
        'ip':                ip_origen,
    }
    try:
        ruta_pdf = consentimientos.generar_pdf(pdf_datos)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Error al generar el PDF: {e}'}), 500

    # Hash SHA-256 de los bytes reales del PDF → ancla de integridad verificable.
    pdf_sha = consentimientos.hash_pdf(ruta_pdf)
    consentimientos.marcar_firmado(consent_id, ruta_pdf, pdf_sha256=pdf_sha)
    if de_cola:
        consentimientos.limpiar_cola_tablet()

    # Respaldo en Drive — no debe tumbar la firma si falla (el PDF ya quedó
    # guardado localmente); solo se registra el resultado.
    import drive_backup
    resultado_drive = drive_backup.subir_pdf(ruta_pdf)
    consentimientos.marcar_respaldo_drive(consent_id, resultado_drive.get('ok'), resultado_drive.get('file_id'))
    if not resultado_drive.get('ok'):
        print(f"[consentimiento] Respaldo a Drive falló para {consent_id}: {resultado_drive.get('error')}")

    # Enviar copia firmada al email del paciente (si tenemos su correo real).
    # No debe tumbar la firma si el correo falla. IMPORTANTE: enviar_copia_consentimiento()
    # NUNCA lanza excepción — atrapa sus propios errores y devuelve {'ok': False, 'error': ...}.
    # Por eso hay que revisar el resultado explícitamente (antes se ignoraba y el envío
    # fallaba en silencio sin dejar rastro).
    try:
        import pacientes as _pac
        rec = _pac.lookup(rut) or {}
        email_pac = (rec.get('email') or '').strip()
        if '@' in email_pac:
            tipo_label = consentimientos.TIPOS_DOCUMENTO.get(tipo, 'Consentimiento informado')
            resultado_copia = notify.enviar_copia_consentimiento(rec, ruta_pdf, tipo_label)
            if not resultado_copia.get('ok'):
                print(f"[consentimiento] Copia por mail falló para {consent_id} ({email_pac}): {resultado_copia.get('error')}")
        else:
            print(f"[consentimiento] Sin email registrado para {consent_id} (rut={rut}) — no se envía copia")
    except Exception as e:
        print(f"[consentimiento] Envío de copia al paciente falló para {consent_id}: {e}")

    return jsonify({'ok': True, 'id': consent_id})


@app.route('/api/consentimientos', methods=['GET'])
def consentimientos_listar():
    """Lista para la futura pestaña 'Consentimientos' del panel admin.
    Protegido por ADMIN_TOKEN."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    return jsonify({'ok': True, 'items': consentimientos.listar(request.args.get('estado'))})


@app.route('/api/consentimiento/marcar-subido', methods=['POST'])
def consentimiento_marcar_subido():
    """Marca un consentimiento como subido a la ficha de DentiDesk. Lo llama el
    proceso de subida nocturna tras confirmar que el PDF quedó en Informes.
    Body: {id}. Protegido por ADMIN_TOKEN."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    consent_id = (request.json or {}).get('id', '')
    if not consent_id or not consentimientos.obtener_registro(consent_id):
        return jsonify({'ok': False, 'error': 'Consentimiento no encontrado'}), 404
    consentimientos.marcar_subido_dentidesk(consent_id)
    return jsonify({'ok': True})


@app.route('/api/consentimiento/borrar', methods=['POST'])
def consentimiento_borrar():
    """Borra un consentimiento NO firmado (estado 'enviado') del registro.
    Un consentimiento firmado NUNCA se borra (es registro clínico/legal).
    Body: {id}. Protegido por ADMIN_TOKEN."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    consent_id = (request.json or {}).get('id', '')
    ok, error = consentimientos.borrar_registro(consent_id)
    if not ok:
        return jsonify({'ok': False, 'error': error}), 409 if 'firmado' in (error or '') else 404
    return jsonify({'ok': True})


@app.route('/api/consentimiento/reenviar-copia', methods=['POST'])
@rate_limit('20 per minute')
def consentimiento_reenviar_copia():
    """Reenvía la copia en PDF de un consentimiento YA firmado al email del
    paciente. Botón "Reenviar copia" del panel — cubre los casos que se
    firmaron antes de que existiera el envío automático, o donde falló.
    Body: {id}. Protegido por ADMIN_TOKEN."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403

    consent_id = (request.json or {}).get('id', '')
    item = consentimientos.obtener_registro(consent_id)
    if not item:
        return jsonify({'ok': False, 'error': 'Consentimiento no encontrado'}), 404
    if item.get('estado') not in ('firmado', 'subido'):
        return jsonify({'ok': False, 'error': 'Este consentimiento aún no está firmado'}), 409

    ruta_pdf = item.get('pdf_path')
    if not ruta_pdf or not os.path.exists(ruta_pdf):
        return jsonify({'ok': False, 'error': 'No se encontró el archivo PDF en el servidor'}), 404

    import pacientes as _pac
    rec = _pac.lookup(item.get('rut', '')) or {}
    email_pac = (rec.get('email') or '').strip()
    if '@' not in email_pac:
        return jsonify({'ok': False, 'error': 'El paciente no tiene email registrado'}), 400

    tipo_label = consentimientos.TIPOS_DOCUMENTO.get(item.get('tipo'), 'Consentimiento informado')
    resultado = notify.enviar_copia_consentimiento(rec, ruta_pdf, tipo_label)
    if not resultado.get('ok'):
        return jsonify({'ok': False, 'error': resultado.get('error') or 'No se pudo enviar el correo'}), 502
    return jsonify({'ok': True, 'email_enmascarado': _enmascarar_email(email_pac)})


# ══════════════════════════════════════════════════════════════════════════════
# SEGUROS COMPLEMENTARIOS  (formularios de reembolso — módulo seguros.py)
# ══════════════════════════════════════════════════════════════════════════════
#
# La secretaria abre /seguro desde el asistente F2 (query params con los datos
# de la cita). La página llama a /api/seguro/* con X-Admin-Token (mismo token
# del panel, guardado en localStorage). El PDF de vista previa se sirve por URL
# con token firmado propio (un <iframe> no puede mandar headers).

import seguros


@app.route('/seguro')
def seguro_page():
    return send_from_directory('.', 'seguros_secretaria.html')


@app.route('/api/seguro/init', methods=['GET'])
def seguro_init():
    """Catálogos para poblar la página: aseguradoras activas + doctores."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    cfg = scheduling.load_config()
    doctores = [{'key': k, 'nombre': f"Dr. {v.get('professional_name', k.title())}"}
                for k, v in (cfg.get('doctores') or {}).items()
                if isinstance(v, dict)]
    aseguradoras = [{'key': a['key'], 'nombre': a.get('nombre', a['key']),
                     'tiene_plantilla': bool(a.get('plantilla_pdf'))}
                    for a in seguros.listar_aseguradoras()]
    return jsonify({'ok': True, 'aseguradoras': aseguradoras, 'doctores': doctores})


@app.route('/api/seguro/precarga', methods=['GET'])
@rate_limit('60 per minute')
def seguro_precarga():
    """Prellenado de la página: datos del paciente (base local), preferencia de
    aseguradora + datos extra guardados, y prestaciones sugeridas por motivo."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    rut = request.args.get('rut', '')
    motivo = request.args.get('motivo', '')
    import pacientes as _pac
    rec = _pac.lookup(rut)
    pref = seguros.paciente_seguro(rut) or {}
    cfg = scheduling.load_config()
    aseg = seguros.obtener_aseguradora(pref.get('ultima_aseguradora')) if pref.get('ultima_aseguradora') else None
    # datos_extra es lo que la secretaria escribio A MANO en el modulo de seguros
    # (siempre manda, puede haberlo corregido a proposito). La base local solo
    # rellena los huecos: direccion (sembrada del Excel de pacientes) y fecha de
    # nacimiento (del export de cumpleanos). Misma funcion que usa armar_valores,
    # para que la pagina muestre exactamente lo que va a salir en el PDF.
    datos_extra = seguros.completar_datos_extra(rut, pref.get('datos_extra'))
    return jsonify({
        'ok': True,
        'paciente': rec or None,
        'datos_extra': datos_extra,
        'ultima_aseguradora': pref.get('ultima_aseguradora'),
        'ultima_aseguradora_nombre': (aseg or {}).get('nombre'),
        'primera_vez': not bool(pref.get('ultima_aseguradora')),
        'sugeridas': seguros.sugerencias_por_motivo(motivo, cfg),
    })


@app.route('/api/seguro/prestaciones', methods=['GET'])
def seguro_prestaciones():
    """Catálogo interno con la traducción de la aseguradora elegida (items:
    [{codigo, descripcion}] — vacío si aún no hay mapeo para esa aseguradora)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    aseg = request.args.get('aseguradora', '')
    return jsonify({'ok': True,
                    'prestaciones': seguros.prestaciones_para_aseguradora(aseg)})


@app.route('/api/seguro/paciente', methods=['POST'])
def seguro_paciente_guardar():
    """Upsert de la preferencia del paciente (última aseguradora usada) y sus
    datos extra (fecha de nacimiento, dirección) para precargar la próxima vez."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    rut = (data.get('rut') or '').strip()
    if not rut:
        return jsonify({'ok': False, 'error': 'Falta el RUT'}), 400
    seguros.guardar_paciente_seguro(rut, aseguradora=data.get('aseguradora'),
                                    datos_extra=data.get('datos_extra'))
    return jsonify({'ok': True})


@app.route('/api/seguro/previsualizar', methods=['POST'])
@rate_limit('30 per minute')
def seguro_previsualizar():
    """Genera el PDF rellenado (plantilla oficial si está mapeada; si no, el
    PDF genérico propio) y lo registra en estado 'generado'. Devuelve form_id
    + URL del PDF con token firmado para el iframe de vista previa."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    rut = (data.get('rut') or '').strip()
    aseg_key = (data.get('aseguradora') or '').strip()
    filas = data.get('prestaciones') or []
    if not rut:
        return jsonify({'ok': False, 'error': 'Falta el RUT del paciente'}), 400
    if not aseg_key:
        return jsonify({'ok': False, 'error': 'Elige una aseguradora'}), 400
    if not filas:
        return jsonify({'ok': False, 'error': 'Agrega al menos una prestación'}), 400

    doctor_key = (data.get('doctor') or '').strip()
    cfg = scheduling.load_config()
    doc_cfg = (cfg.get('doctores') or {}).get(doctor_key)
    doctor_nombre = (f"Dr. {doc_cfg['professional_name']}"
                     if isinstance(doc_cfg, dict) and doc_cfg.get('professional_name')
                     else doctor_key)

    valores = seguros.armar_valores({
        'rut': rut,
        'nombre': data.get('nombre', ''),
        'apellido': data.get('apellido', ''),
        'email': data.get('email', ''),
        'telefono': data.get('telefono', ''),
        'datos_extra': data.get('datos_extra') or {},
        'doctor_nombre': doctor_nombre,
    }, filas)
    # RUT y especialidad del profesional (los piden varios formularios) viven
    # en el indice de firmas de seguros; especialidad cae al scheduling_config.
    doc_datos = seguros.datos_doctor(doctor_key)
    valores['doctor_rut'] = doc_datos.get('rut', '')
    especialidad = doc_datos.get('especialidad', '')
    if not especialidad and isinstance(doc_cfg, dict):
        especialidad = (doc_cfg.get('especialidad') or '').title()
    valores['doctor_especialidad'] = especialidad
    if doc_datos.get('nombre_visible'):
        valores['doctor_nombre'] = doc_datos['nombre_visible']

    try:
        pdf_path = seguros.rellenar_pdf(aseg_key, valores, firma_doctor_key=doctor_key)
    except Exception as e:
        app.logger.error('seguro: error generando PDF: %s', e)
        return jsonify({'ok': False, 'error': f'No se pudo generar el PDF: {e}'}), 500

    form_id = seguros.crear_registro({
        'rut': rut, 'aseguradora': aseg_key, 'doctor': doctor_key,
        'prestaciones': filas, 'fecha_atencion': data.get('fecha_atencion', ''),
        'id_agenda': str(data.get('id_agenda', '')), 'pdf_path': pdf_path,
        'email': (data.get('email') or '').strip(),
    })
    token = seguros.generar_token_pdf(form_id)
    return jsonify({'ok': True, 'form_id': form_id,
                    'pdf_url': f'/api/seguro/pdf?token={token}'})


@app.route('/api/seguro/pdf', methods=['GET'])
@rate_limit('60 per minute')
def seguro_pdf():
    """Sirve el PDF generado para el iframe de vista previa. Auth por token
    firmado en la URL (corta duración) porque un iframe no manda headers."""
    from flask import send_file
    info = seguros.validar_token_pdf(request.args.get('token', ''))
    if not info:
        return jsonify({'ok': False, 'error': 'Link inválido o vencido'}), 403
    item = seguros.obtener_registro(info.get('form_id', ''))
    if not item or not item.get('pdf_path') or not os.path.exists(item['pdf_path']):
        return jsonify({'ok': False, 'error': 'PDF no encontrado'}), 404
    return send_file(item['pdf_path'], mimetype='application/pdf',
                     as_attachment=False, download_name='formulario-seguro.pdf')


@app.route('/api/seguro/enviar', methods=['POST'])
@rate_limit('30 per minute')
def seguro_enviar():
    """Envía el PDF generado al email del paciente (adjunto, Cc recepción) y
    marca el registro como 'enviado'. Body: {form_id, email?} — email opcional
    para corregir el destino sin regenerar el PDF."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    form_id = (data.get('form_id') or '').strip()
    item = seguros.obtener_registro(form_id)
    if not item:
        return jsonify({'ok': False, 'error': 'Formulario no encontrado (genera la vista previa de nuevo)'}), 404
    if not item.get('pdf_path') or not os.path.exists(item['pdf_path']):
        return jsonify({'ok': False, 'error': 'El PDF ya no está en el servidor (genera la vista previa de nuevo)'}), 404

    import pacientes as _pac
    email_dest = (data.get('email') or item.get('email') or '').strip()
    rec = _pac.lookup(item.get('rut', '')) or {}
    if '@' not in email_dest:
        email_dest = (rec.get('email') or '').strip()
    if '@' not in email_dest:
        return jsonify({'ok': False, 'error': 'El paciente no tiene email registrado'}), 400

    aseg = seguros.obtener_aseguradora(item.get('aseguradora', '')) or {}
    paciente = {'nombres': rec.get('nombres', ''), 'apellidos': rec.get('apellidos', ''),
                'email': email_dest}
    resultado = notify.enviar_formulario_seguro(
        paciente, item['pdf_path'], aseg.get('nombre', item.get('aseguradora', '')))
    if not resultado.get('ok'):
        return jsonify({'ok': False, 'error': resultado.get('error') or 'No se pudo enviar el correo'}), 502
    seguros.marcar_enviado(form_id, canal='email')
    return jsonify({'ok': True, 'email_enmascarado': _enmascarar_email(email_dest)})


# ── Envío 1-clic desde la boleta (botón rápido del F2) ───────────────────────
# El F2 lee la boleta DTE del día (glosa+monto) de la web de DentiDesk con su
# propia sesión y la manda aquí. preparar = interpretación + resumen para la
# mini-confirmación en el panel F2; enviar = genera el PDF oficial y lo emailea.

@app.route('/api/seguro/preparar-desde-boleta', methods=['POST'])
@rate_limit('30 per minute')
def seguro_preparar_desde_boleta():
    """Body: {rut, glosa, monto, folio?, fecha?}. Devuelve la aseguradora del
    paciente, las filas traducidas y el resumen para confirmar en el F2."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    rut = (data.get('rut') or '').strip()
    if not rut:
        return jsonify({'ok': False, 'error': 'Falta el RUT'}), 400

    pref = seguros.paciente_seguro(rut) or {}
    aseg_key = pref.get('ultima_aseguradora')
    if not aseg_key:
        return jsonify({'ok': False, 'sin_aseguradora': True,
                        'error': 'El paciente no tiene aseguradora asignada. Usa "Elegir aseguradora" primero.'}), 409
    aseg = seguros.obtener_aseguradora(aseg_key) or {}

    filas, no_reconocido = seguros.filas_desde_boleta(
        data.get('glosa', ''), data.get('monto'), aseg_key,
        fecha=(data.get('fecha') or seguros.ahora_chile().strftime('%d-%m-%Y')))
    if no_reconocido:
        return jsonify({'ok': False,
                        'error': 'No reconocí ninguna prestación en la glosa de la boleta. '
                                 'Revisa los alias de glosa en el panel (pestaña Seguros) o usa la página para armarlo a mano.',
                        'glosa': data.get('glosa', '')}), 422

    total = sum(int(f.get('valor') or 0) for f in filas)
    return jsonify({'ok': True, 'aseguradora': aseg_key,
                    'aseguradora_nombre': aseg.get('nombre', aseg_key),
                    'filas': filas, 'total': total,
                    'datos_extra': pref.get('datos_extra', {})})


@app.route('/api/seguro/enviar-desde-boleta', methods=['POST'])
@rate_limit('20 per minute')
def seguro_enviar_desde_boleta():
    """Body: {rut, nombre, apellido, email, telefono, doctor, id_agenda, folio,
    aseguradora, filas (las confirmadas en el F2), datos_extra?}. Genera el PDF
    oficial, lo envía por email (Cc recepción) y registra el historial."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    rut = (data.get('rut') or '').strip()
    aseg_key = (data.get('aseguradora') or '').strip()
    filas = data.get('filas') or []
    if not rut or not aseg_key or not filas:
        return jsonify({'ok': False, 'error': 'Faltan rut, aseguradora o filas'}), 400

    import pacientes as _pac
    rec = _pac.lookup(rut) or {}
    email_dest = (data.get('email') or rec.get('email') or '').strip()
    if '@' not in email_dest:
        return jsonify({'ok': False, 'error': 'El paciente no tiene email registrado'}), 400
    nombre = (data.get('nombre') or rec.get('nombres', '')).strip()
    apellido = (data.get('apellido') or rec.get('apellidos', '')).strip()

    # El F2 manda el TEXTO del doctor del modal ("Dr. Octavio Del Real S."),
    # no la key; resolver contra professional_name del scheduling_config.
    doctor_txt = (data.get('doctor') or '').strip()
    cfg = scheduling.load_config()
    doctores = {k: v for k, v in (cfg.get('doctores') or {}).items() if isinstance(v, dict)}
    doctor_key = doctor_txt if doctor_txt in doctores else ''
    if not doctor_key:
        txt_low = doctor_txt.lower()
        for k, v in doctores.items():
            pn = (v.get('professional_name') or '').lower()
            if pn and (pn in txt_low or txt_low in pn):
                doctor_key = k
                break
    doc_cfg = doctores.get(doctor_key)
    doctor_nombre = (f"Dr. {doc_cfg['professional_name']}"
                     if isinstance(doc_cfg, dict) and doc_cfg.get('professional_name')
                     else (doctor_txt or doctor_key))
    valores = seguros.armar_valores({
        'rut': rut, 'nombre': nombre, 'apellido': apellido,
        'email': email_dest, 'telefono': data.get('telefono', ''),
        'datos_extra': data.get('datos_extra') or (seguros.paciente_seguro(rut) or {}).get('datos_extra', {}),
        'doctor_nombre': doctor_nombre,
        'fecha_atencion': data.get('fecha_atencion', ''),
    }, filas)
    doc_datos = seguros.datos_doctor(doctor_key)
    valores['doctor_rut'] = doc_datos.get('rut', '')
    valores['doctor_especialidad'] = (doc_datos.get('especialidad')
                                      or ((doc_cfg or {}).get('especialidad', '') or '').title())
    if doc_datos.get('nombre_visible'):
        valores['doctor_nombre'] = doc_datos['nombre_visible']

    try:
        pdf_path = seguros.rellenar_pdf(aseg_key, valores, firma_doctor_key=doctor_key)
    except Exception as e:
        app.logger.error('seguro boleta: error generando PDF: %s', e)
        return jsonify({'ok': False, 'error': f'No se pudo generar el PDF: {e}'}), 500

    aseg = seguros.obtener_aseguradora(aseg_key) or {}
    paciente = {'nombres': nombre, 'apellidos': apellido, 'email': email_dest}
    resultado = notify.enviar_formulario_seguro(paciente, pdf_path,
                                                aseg.get('nombre', aseg_key))
    if not resultado.get('ok'):
        return jsonify({'ok': False, 'error': resultado.get('error') or 'No se pudo enviar el correo'}), 502

    form_id = seguros.crear_registro({
        'rut': rut, 'aseguradora': aseg_key, 'doctor': doctor_key,
        'prestaciones': filas, 'fecha_atencion': data.get('fecha_atencion', ''),
        'id_agenda': str(data.get('id_agenda', '')), 'pdf_path': pdf_path,
        'email': email_dest,
    })
    seguros.marcar_enviado(form_id, canal='email')
    return jsonify({'ok': True, 'form_id': form_id,
                    'email_enmascarado': _enmascarar_email(email_dest)})


# ── Auto-envío: la extensión vigila las boletas nuevas y las manda aquí ──────
# Envía SOLO si es "limpio" (aseguradora asignada + glosa reconocida + email);
# si no, avisa a la clínica por correo y deja la boleta para el botón del F2.
# Todo se resuelve server-side desde el RUT (la vigilancia no tiene el modal
# abierto): email/nombre de la base local, doctor del doctor_default configurado.

@app.route('/api/seguro/auto-desde-boleta', methods=['POST'])
@rate_limit('120 per minute')
def seguro_auto_desde_boleta():
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    rut = (data.get('rut') or '').strip()
    glosa = data.get('glosa', '')
    folio = str(data.get('folio') or '').strip()
    if not rut:
        return jsonify({'ok': False, 'error': 'Falta el RUT'}), 400

    # Anti-duplicado: si esta boleta ya generó un envío, no repetir.
    if folio and seguros.folio_ya_enviado(folio):
        return jsonify({'ok': True, 'ya_enviado': True})

    import pacientes as _pac
    rec = _pac.lookup(rut) or {}
    nombre_pac = f"{rec.get('nombres', '')} {rec.get('apellidos', '')}".strip() or rut

    def _pendiente(motivo, http=200):
        notify.avisar_recepcion_seguro_no_enviado(motivo, rut, glosa, folio, nombre_pac)
        return jsonify({'ok': False, 'pendiente': True, 'motivo': motivo}), http

    pref = seguros.paciente_seguro(rut) or {}
    aseg_key = pref.get('ultima_aseguradora')
    if not aseg_key:
        return _pendiente('sin_aseguradora')

    filas, no_reconocido = seguros.filas_desde_boleta(
        glosa, data.get('monto'), aseg_key,
        fecha=(data.get('fecha') or seguros.ahora_chile().strftime('%d-%m-%Y')))
    if no_reconocido:
        return _pendiente('glosa')

    email_dest = (rec.get('email') or '').strip()
    if '@' not in email_dest:
        return _pendiente('sin_email')

    cfg = scheduling.load_config()
    doctor_key = seguros.get_auto_config().get('doctor_default', '')
    doc_cfg = (cfg.get('doctores') or {}).get(doctor_key) if doctor_key else None
    doctor_nombre = (f"Dr. {doc_cfg['professional_name']}"
                     if isinstance(doc_cfg, dict) and doc_cfg.get('professional_name')
                     else '')
    valores = seguros.armar_valores({
        'rut': rut, 'nombre': rec.get('nombres', ''), 'apellido': rec.get('apellidos', ''),
        'email': email_dest, 'telefono': rec.get('telefono', ''),
        'datos_extra': pref.get('datos_extra', {}),
        'doctor_nombre': doctor_nombre,
        'fecha_atencion': (data.get('fecha') or ''),
    }, filas)
    if doctor_key:
        doc_datos = seguros.datos_doctor(doctor_key)
        valores['doctor_rut'] = doc_datos.get('rut', '')
        valores['doctor_especialidad'] = (doc_datos.get('especialidad')
                                          or ((doc_cfg or {}).get('especialidad', '') or '').title())
        if doc_datos.get('nombre_visible'):
            valores['doctor_nombre'] = doc_datos['nombre_visible']

    try:
        pdf_path = seguros.rellenar_pdf(aseg_key, valores, firma_doctor_key=doctor_key or None)
    except Exception as e:
        app.logger.error('seguro auto: error PDF: %s', e)
        return _pendiente('error_pdf')

    aseg = seguros.obtener_aseguradora(aseg_key) or {}
    paciente = {'nombres': rec.get('nombres', ''), 'apellidos': rec.get('apellidos', ''),
                'email': email_dest}
    resultado = notify.enviar_formulario_seguro(paciente, pdf_path,
                                                aseg.get('nombre', aseg_key))
    if not resultado.get('ok'):
        return _pendiente('error_envio')

    form_id = seguros.crear_registro({
        'rut': rut, 'aseguradora': aseg_key, 'doctor': doctor_key,
        'prestaciones': filas, 'fecha_atencion': data.get('fecha', ''),
        'folio': folio, 'origen': 'auto', 'pdf_path': pdf_path, 'email': email_dest,
    })
    seguros.marcar_enviado(form_id, canal='email')
    app.logger.info('seguro auto enviado: folio %s -> %s (%s)', folio, nombre_pac, aseg_key)
    return jsonify({'ok': True, 'form_id': form_id,
                    'email_enmascarado': _enmascarar_email(email_dest)})


@app.route('/api/seguro/auto-config', methods=['GET', 'POST'])
def seguro_auto_config():
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    if request.method == 'POST':
        data = request.json or {}
        seguros.set_auto_config(activo=data.get('activo'),
                                doctor_default=data.get('doctor_default'))
    return jsonify({'ok': True, **seguros.get_auto_config()})


# ── Administración (pestaña "Seguros" del panel) ─────────────────────────────

@app.route('/api/seguro/admin/aseguradoras', methods=['GET', 'POST'])
def seguro_admin_aseguradoras():
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    if request.method == 'GET':
        return jsonify({'ok': True, 'aseguradoras': seguros.listar_aseguradoras(solo_activas=False)})
    data = request.json or {}
    key = re.sub(r'[^a-z0-9_]', '', (data.get('key') or '').strip().lower().replace(' ', '_'))
    if not key:
        return jsonify({'ok': False, 'error': 'Falta la key de la aseguradora'}), 400
    campos = {k: v for k, v in data.items()
              if k in ('nombre', 'activa', 'tipo_plantilla', 'mapeo_campos',
                       'max_prestaciones_por_form')}
    seguros.guardar_aseguradora(key, campos)
    return jsonify({'ok': True, 'key': key})


@app.route('/api/seguro/admin/aseguradora/plantilla', methods=['POST'])
def seguro_admin_plantilla():
    """Sube el PDF oficial de una aseguradora (multipart: file + key)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    f = request.files.get('file')
    key = re.sub(r'[^a-z0-9_]', '', (request.form.get('key') or '').strip().lower())
    if not f or not key:
        return jsonify({'ok': False, 'error': 'Faltan el archivo o la key'}), 400
    if not (f.filename or '').lower().endswith('.pdf'):
        return jsonify({'ok': False, 'error': 'La plantilla debe ser un PDF'}), 400
    seguros.PLANTILLAS_DIR.mkdir(parents=True, exist_ok=True)
    nombre = f'{key}.pdf'  # una plantilla por aseguradora, se reemplaza al subir otra
    f.save(str(seguros.PLANTILLAS_DIR / nombre))
    seguros.guardar_aseguradora(key, {'plantilla_pdf': nombre})
    return jsonify({'ok': True, 'plantilla_pdf': nombre})


@app.route('/api/seguro/admin/aseguradora/campos-acroform', methods=['GET'])
def seguro_admin_campos_acroform():
    """Lista los campos AcroForm reales del PDF subido, para armar el mapeo
    con <select> en el panel (sin coordenadas)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    campos = seguros.campos_acroform(request.args.get('aseguradora', ''))
    if campos is None:
        return jsonify({'ok': False, 'error': 'Esa aseguradora no tiene plantilla subida'}), 404
    return jsonify({'ok': True, 'campos': campos})


@app.route('/api/seguro/admin/prestaciones', methods=['GET', 'POST'])
def seguro_admin_prestaciones():
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    if request.method == 'GET':
        return jsonify({'ok': True,
                        'prestaciones': seguros.listar_prestaciones(solo_activas=False),
                        'mapeo': seguros.mapeo_prestaciones()})
    data = request.json or {}
    prest_id = seguros.guardar_prestacion(data.get('id'), {
        k: v for k, v in data.items()
        if k in ('nombre', 'precio_arancel', 'activa', 'motivo_scheduling_key',
                 'glosas_boleta', 'absorbe_saldo')})
    return jsonify({'ok': True, 'id': prest_id})


@app.route('/api/seguro/admin/prestaciones/seed-desde-motivos', methods=['POST'])
def seguro_admin_seed():
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    cfg = scheduling.load_config()
    creados = seguros.seed_desde_motivos(cfg)
    return jsonify({'ok': True, 'creados': creados})


@app.route('/api/seguro/admin/mapeo-prestaciones', methods=['POST'])
def seguro_admin_mapeo_prestaciones():
    """Body: {prest_id, aseguradora, items: [{codigo, descripcion}, ...]}."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    if not data.get('prest_id') or not data.get('aseguradora'):
        return jsonify({'ok': False, 'error': 'Faltan prest_id o aseguradora'}), 400
    seguros.guardar_mapeo_prestacion(data['prest_id'], data['aseguradora'],
                                     data.get('items') or [])
    return jsonify({'ok': True})


@app.route('/api/seguro/admin/mapeo-motivos', methods=['GET', 'POST'])
def seguro_admin_mapeo_motivos():
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    if request.method == 'GET':
        return jsonify({'ok': True, 'mapeo': seguros.mapeo_motivos()})
    data = request.json or {}
    if not data.get('motivo'):
        return jsonify({'ok': False, 'error': 'Falta el motivo'}), 400
    seguros.guardar_mapeo_motivo(data['motivo'], data.get('prestaciones') or [])
    return jsonify({'ok': True})


@app.route('/api/seguro/admin/firma', methods=['GET', 'POST'])
def seguro_admin_firma():
    """POST multipart: file + doctor (key) + nombre_visible. GET: lista firmas."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    if request.method == 'GET':
        return jsonify({'ok': True, 'firmas': seguros.listar_firmas()})
    f = request.files.get('file')
    doctor = re.sub(r'[^a-z0-9_]', '', (request.form.get('doctor') or '').strip().lower())
    if not f or not doctor:
        return jsonify({'ok': False, 'error': 'Faltan el archivo o el doctor'}), 400
    ext = os.path.splitext(f.filename or '')[1].lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.webp'):
        return jsonify({'ok': False, 'error': 'La firma debe ser una imagen (png/jpg/webp)'}), 400
    seguros.FIRMAS_DIR.mkdir(parents=True, exist_ok=True)
    nombre = f'{doctor}{ext}'
    f.save(str(seguros.FIRMAS_DIR / nombre))
    seguros.guardar_firma(doctor, request.form.get('nombre_visible', doctor.title()), nombre,
                          rut=request.form.get('rut') or None,
                          especialidad=request.form.get('especialidad') or None)
    return jsonify({'ok': True, 'imagen': nombre})


@app.route('/api/seguro/admin/historial', methods=['GET'])
def seguro_admin_historial():
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    return jsonify({'ok': True, 'items': seguros.listar_registros(
        estado=request.args.get('estado') or None,
        rut=request.args.get('rut') or None)})


# ══════════════════════════════════════════════════════════════════════════════
# CONTROL DENTAL  (recordatorio de control dental — módulo control_dental.py)
# ══════════════════════════════════════════════════════════════════════════════
#
# Recordatorio por EMAIL (no WhatsApp -- no requiere plantilla de Meta ni tiene
# tope de frecuencia) a pacientes con aparatos fijos/alineadores para que vayan
# a su dentista general cada ~6 meses (limpieza/revision de caries). A
# diferencia de recaptacion.py (dispara a mano desde el F2 por cada paciente),
# aca la inscripcion es AUTOMATICA: un barrido diario de la agenda de
# DentiDesk detecta solo instalaciones y retiros (ver control_dental.py).

@app.route('/api/control-dental/paciente', methods=['GET'])
def control_dental_paciente_get():
    """F2: estado del paciente en control dental (inscrito, tipo, proximo
    envio, frecuencia, historial de envios, no_molestar). Si no esta
    inscrito devuelve {'ok': True, 'inscrito': False} -- NO un error -- para
    que el F2 ofrezca el boton 'Inscribir'."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    rut = request.args.get('rut', '')
    if not rut:
        return jsonify({'ok': False, 'error': 'Falta el RUT'}), 400
    clave = control_dental._rut_key(rut)
    reg = control_dental._load_registro()
    no_molestar = clave in (reg.get('no_molestar') or [])
    p = (reg.get('inscritos') or {}).get(clave)
    if not p:
        return jsonify({'ok': True, 'inscrito': False, 'no_molestar': no_molestar})
    return jsonify({'ok': True, 'inscrito': True, 'rut': clave, 'no_molestar': no_molestar, **p})


@app.route('/api/control-dental/paciente', methods=['POST'])
def control_dental_paciente_post():
    """F2: ajusta a mano un inscrito (activar/desactivar, frecuencia, o
    correr la fecha base -- ej. 'el paciente fue al dentista el 2026-04-15')
    o inscribe uno nuevo manualmente si 'inscribir' viene en true. Cualquier
    llamada aca marca bloqueo_manual=True (lo hace control_dental.set_manual)
    -- es, por definicion, la asistente tocando al paciente a mano."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    rut = (data.get('rut') or '').strip()
    if not rut:
        return jsonify({'ok': False, 'error': 'Falta el RUT'}), 400

    clave = control_dental._rut_key(rut)
    reg = control_dental._load_registro()
    existe = clave in (reg.get('inscritos') or {})

    if not existe:
        if not data.get('inscribir'):
            return jsonify({'ok': False, 'error': 'El paciente no esta inscrito en control dental'}), 404
        hoy = fechas.hoy_chile().isoformat()
        control_dental.inscribir(
            rut, data.get('nombre', ''), data.get('email', ''), 'manual',
            hoy, '', 'Inscripcion manual (F2)', '', manual=True)

    p = control_dental.set_manual(
        rut,
        activo=data.get('activo'),
        frecuencia_meses=data.get('frecuencia_meses'),
        fecha_base=data.get('fecha_base'),
    )
    if p is None:
        return jsonify({'ok': False, 'error': 'No se pudo actualizar el registro'}), 500
    return jsonify({'ok': True, 'rut': clave, **p})


@app.route('/api/control-dental/no-molestar', methods=['POST'])
def control_dental_no_molestar():
    """F2/panel: boton 'No volver a recordar' (y su reverso)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    rut = (data.get('rut') or '').strip()
    if not rut:
        return jsonify({'ok': False, 'error': 'Falta el RUT'}), 400
    if data.get('quitar'):
        lista = control_dental.quitar_no_molestar(rut)
    else:
        lista = control_dental.agregar_no_molestar(rut)
    return jsonify({'ok': True, 'no_molestar': lista})


@app.route('/api/control-dental/config', methods=['GET', 'POST'])
def control_dental_config():
    """Panel: ver/editar la config (activo, frecuencia, hora de envio, tope
    diario, meses sin actividad antes de pausar)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    if request.method == 'POST':
        cfg = control_dental.save_config(request.json or {})
    else:
        cfg = control_dental.load_config()
    return jsonify({'ok': True, 'config': cfg})


@app.route('/api/control-dental/inscritos', methods=['GET'])
def control_dental_inscritos():
    """Panel: tabla de inscritos, opcionalmente filtrada por estado (activo,
    dado_de_baja, sin_email, pausado_inactivo, desactivado_manual)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    return jsonify({'ok': True, 'items': control_dental.listar(request.args.get('estado') or None)})


@app.route('/api/control-dental/historial', methods=['GET'])
def control_dental_historial():
    """Panel: envios recientes (mas nuevo primero)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    try:
        limite = int(request.args.get('limite', 100))
    except (TypeError, ValueError):
        limite = 100
    return jsonify({'ok': True, 'items': control_dental.historial(limite)})


@app.route('/api/control-dental/backfill', methods=['POST'])
def control_dental_backfill():
    """Panel: 'Inscribir cartera actual (N meses atras)'. Son ~130 consultas
    a DentiDesk (barre N meses hacia atras, dias habiles) -- correrlo dentro
    del request bloquearia al cliente varios segundos/minutos, asi que se
    lanza en un hilo daemon y se responde de inmediato; el resultado queda
    en el log de Render."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    cfg_dd = scheduling.load_config()
    if not cfg_dd['dentidesk']['enabled']:
        return jsonify({'ok': False, 'error': 'Modo demo: sin credenciales DentiDesk'}), 400
    data = request.json or {}
    try:
        meses = int(data.get('meses', 6))
    except (TypeError, ValueError):
        meses = 6

    def job():
        try:
            r = control_dental.backfill(meses=meses)
            print('[control-dental] backfill:', r)
        except Exception as e:
            print('[control-dental] error en backfill:', e)
    _threading.Thread(target=job, daemon=True).start()
    return jsonify({'ok': True, 'iniciado': True})


@app.route('/api/control-dental/run', methods=['POST'])
def control_dental_run():
    """Corre a mano el barrido + envio (para probar) -- respeta el mismo
    anti-duplicados que el loop automatico (_loop_control_dental)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    cfg_cd = control_dental.load_config()
    r = _procesar_control_dental(cfg_cd, fechas.hoy_chile())
    return jsonify({'ok': True, **r})


@app.route('/api/control-dental/motivo', methods=['POST'])
def control_dental_motivo():
    """Panel: clasificar un Reason visto en la agenda que no calzo con
    ninguna categoria (queda en cfg['motivos_extra'], sin esperar un
    deploy)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    reason = (data.get('reason') or '').strip()
    categoria = (data.get('categoria') or '').strip()
    if not reason or not categoria:
        return jsonify({'ok': False, 'error': 'Falta reason o categoria'}), 400
    control_dental.clasificar_motivo_desconocido(reason, categoria)
    return jsonify({'ok': True})


@app.route('/api/control-dental/test', methods=['POST'])
def control_dental_test():
    """Envia el email de control dental de prueba a una direccion cualquiera
    (protegido por ADMIN_TOKEN). Mismo espiritu que /api/whatsapp/test: poder
    ver como queda el correo SIN inscribir a un paciente real ni esperar a que
    a alguien le toque su ciclo de 6 meses.

    Body JSON: { email, nombre?, frecuencia_meses? }

    Manda el correo DE VERDAD (no es un simulacro), asi que el destinatario
    tiene que ser de la clinica. Usa exactamente la misma funcion que el
    scheduler, para que lo que se ve en la prueba sea lo que le llega al
    paciente."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    email = (data.get('email') or '').strip()
    if '@' not in email:
        return jsonify({'ok': False, 'error': 'Falta un email valido'}), 400
    try:
        frecuencia = int(data.get('frecuencia_meses') or 6)
    except (TypeError, ValueError):
        frecuencia = 6
    paciente = {
        'rut': (data.get('rut') or '').strip(),
        'nombre': (data.get('nombre') or 'Paciente de Prueba').strip(),
        'email': email,
        'frecuencia_meses': frecuencia,
    }
    resultado = notify.enviar_recordatorio_control_dental(paciente)
    if not resultado.get('ok'):
        return jsonify({'ok': False, 'error': resultado.get('error') or 'No se pudo enviar'}), 502
    # Se devuelve el saludo que se uso (y si se resolvio contra una ficha real)
    # porque sin eso la prueba engaña: sin RUT el saludo cae al generico
    # "Estimado/a" y parece que al paciente le fuera a llegar asi, cuando en un
    # envio real el RUT siempre va y sale "Estimado" o "Estimada".
    import pacientes  # perezoso, igual que el resto de server.py
    sufijo = pacientes.saludo(paciente['rut']) if paciente['rut'] else ''
    return jsonify({
        'ok': True,
        'enviado_a': email,
        'saludo': f'Estimad{sufijo}' if sufijo else 'Estimado/a',
        'ficha_encontrada': bool(paciente['rut'] and pacientes.lookup(paciente['rut'])),
    })


@app.route('/api/control-dental/proximos', methods=['GET'])
def control_dental_proximos():
    """Pacientes a los que se les ENVIARA el recordatorio dental en/antes de
    'fecha' (query, YYYY-MM-DD; default hoy), opcionalmente de un 'doctor'
    (query, subcadena del profesional que instalo los aparatos). Lo usa el
    reporte diario de evoluciones del Dr. Alberto para incluir "a estos
    pacientes tuyos les llega el recordatorio dental manana". Solo lectura."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    from datetime import date as _date
    fecha_arg = (request.args.get('fecha') or '').strip()
    try:
        fecha = _date.fromisoformat(fecha_arg) if fecha_arg else None
    except ValueError:
        return jsonify({'ok': False, 'error': 'fecha invalida (usar YYYY-MM-DD)'}), 400
    doctor = (request.args.get('doctor') or '').strip() or None
    items = control_dental.proximos_envios(fecha=fecha, doctor=doctor)
    # Se devuelve solo lo que el reporte necesita (sin volcar todo el registro).
    campos = ('rut', 'nombre', 'email', 'doctor', 'tipo', 'motivo_inicio',
              'fecha_inicio', 'proximo_envio', 'frecuencia_meses')
    return jsonify({'ok': True, 'items': [{k: p.get(k) for k in campos} for p in items]})


@app.route('/api/control-dental/motivos-desconocidos', methods=['GET'])
def control_dental_motivos_desconocidos():
    """Panel: los Reason que el barrido vio en la agenda y no supo clasificar,
    con cuantas veces aparecieron y cuando fue la ultima. Alimenta la card que
    deja clasificarlos desde el panel (POST /api/control-dental/motivo), asi
    los motivos ambiguos del diccionario de DentiDesk (Aligner/Essix, Placa,
    Disyuntor, Cementar Bracket, Reinicio) se van resolviendo con datos reales
    en vez de a puro criterio."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    desconocidos = control_dental.motivos_desconocidos() or {}
    # Los mas frecuentes primero: son los que mas rinde clasificar.
    items = sorted(
        ({'reason': r, 'n': (info or {}).get('n', 0), 'ultima': (info or {}).get('ultima', '')}
         for r, info in desconocidos.items()),
        key=lambda x: x['n'], reverse=True)
    return jsonify({'ok': True, 'motivos': items})


# ══════════════════════════════════════════════════════════════════════════════
# CUMPLEANOS  (equipo + pacientes — modulo cumpleanos.py)
# ══════════════════════════════════════════════════════════════════════════════
#
# Alimenta la seccion de cumpleanos del reporte diario que recibe el Dr. Alberto
# (revision-evoluciones/INSTRUCCIONES.md, Paso 4.8). Dos fuentes: la lista propia
# del equipo (doctores + staff) y la fecha_nacimiento de la base de pacientes,
# sembrada desde el export "Listado de Cumpleanos" de DentiDesk.
#
# Es SOLO LECTURA e informativo: el sistema no saluda a nadie, el saludo lo
# decide y lo manda una persona.

@app.route('/api/cumpleanos/proximos', methods=['GET'])
def cumpleanos_proximos():
    """Cumpleanos de una fecha (query 'fecha' YYYY-MM-DD; default MANANA).

    Devuelve {ok, fecha, fecha_legible, equipo:[{nombre, edad, ...}],
    pacientes:[{rut, nombre, edad, id_paciente, telefono, ...}]}, donde 'edad'
    son los anios que la persona CUMPLE ese dia."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    import cumpleanos as _cumple
    from datetime import date as _date
    fecha_arg = (request.args.get('fecha') or '').strip()
    try:
        fecha = _date.fromisoformat(fecha_arg) if fecha_arg else None
    except ValueError:
        return jsonify({'ok': False, 'error': 'fecha invalida (usar YYYY-MM-DD)'}), 400
    return jsonify({'ok': True, **_cumple.proximos(fecha)})


@app.route('/api/cumpleanos/equipo', methods=['GET'])
def cumpleanos_equipo_listar():
    """Lista del equipo con su fecha de nacimiento (para revisar/editar)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    import cumpleanos as _cumple
    lista = _cumple.equipo()
    return jsonify({'ok': True, 'equipo': lista, 'total': len(lista),
                    'pendientes': [p['nombre'] for p in lista if p.get('pendiente')]})


@app.route('/api/cumpleanos/equipo/importar', methods=['POST'])
def cumpleanos_equipo_importar():
    """Carga la lista del equipo desde el texto de la tabla
    ('cumpleanos doctores.txt'), body {texto, reemplazar?}.

    El archivo NO viaja por git a proposito: este repo es PUBLICO (sirve el
    sitio por GitHub Pages) y son fechas de nacimiento de personas reales. Por
    eso la lista se carga por aca y vive en el disco persistente."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    import cumpleanos as _cumple
    data = request.get_json(silent=True) or {}
    texto = data.get('texto') or ''
    if not texto.strip():
        return jsonify({'ok': False, 'error': 'Falta el texto de la tabla'}), 400
    res = _cumple.importar_equipo(texto, reemplazar=bool(data.get('reemplazar', True)))
    if not res['total']:
        return jsonify({'ok': False, 'error': 'No se reconocio ninguna fila en la tabla'}), 400
    return jsonify({'ok': True, **res})


# ══════════════════════════════════════════════════════════════════════════════
# NPS / SATISFACCION  (encuesta de satisfaccion por WhatsApp — modulo nps.py)
# ══════════════════════════════════════════════════════════════════════════════
#
# Barrido de citas ATENDIDAS (ayer y hoy) que, unas horas despues de terminada
# la atencion y dentro de una ventana horaria configurable, manda por WhatsApp
# la encuesta de satisfaccion (plantilla notify.enviar_nps). Mismo espiritu que
# CONTROL DENTAL (barrido automatico) pero con las guardas de recaptacion.py
# (no_molestar, cooldown, frecuencia periodica) y anti-oleada al encenderse.

@app.route('/api/nps/config', methods=['GET'])
def nps_config_get():
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    return jsonify({'ok': True, 'config': nps.load_config()})


@app.route('/api/nps/config', methods=['POST'])
def nps_config_post():
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    cfg = nps.save_config(request.json or {})
    return jsonify({'ok': True, 'config': cfg})


@app.route('/api/nps/resumen', methods=['GET'])
def nps_resumen():
    """Panel: metricas (volumen, tasa de respuesta, NPS, resenas vs baseline,
    mediana de dias envio->respuesta). Forma exacta: ver nps.resumen()."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    return jsonify({'ok': True, 'resumen': nps.resumen()})


@app.route('/api/nps/pacientes', methods=['GET'])
def nps_pacientes():
    """Panel: respuestas filtradas por categoria (promotor|pasivo|detractor)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    return jsonify({'ok': True, 'items': nps.lista_por_categoria(request.args.get('categoria', ''))})


@app.route('/api/nps/historial', methods=['GET'])
def nps_historial():
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    try:
        limite = int(request.args.get('limite', 100))
    except (TypeError, ValueError):
        limite = 100
    return jsonify({'ok': True, 'items': nps.historial(limite)})


@app.route('/api/nps/run', methods=['POST'])
def nps_run():
    """Corre a mano el barrido + envio (prueba manual) -- respeta el registro
    (no reenvia lo ya visto/enviado/bloqueado) pero NO exige cfg['activo'],
    a diferencia del loop automatico."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    ahora = fechas.ahora_chile_aware()
    r = _procesar_nps(nps.load_config(), scheduling.load_config(), ahora)
    return jsonify({'ok': True, 'resultado': r})


@app.route('/api/nps/no-molestar', methods=['POST'])
def nps_no_molestar():
    """F2/panel: boton 'No volver a preguntar' (y su reverso)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    rut = (data.get('rut') or '').strip()
    if not rut:
        return jsonify({'ok': False, 'error': 'Falta el RUT'}), 400
    if data.get('quitar'):
        lista = nps.quitar_no_molestar(rut)
    else:
        lista = nps.agregar_no_molestar(rut)
    return jsonify({'ok': True, 'no_molestar': lista})


@app.route('/api/nps/metrica-mensual', methods=['POST'])
def nps_metrica_mensual():
    """Panel: carga manual de resenas de Google de un mes (no hay API de
    Google Reviews en este proyecto)."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    mes = (data.get('mes') or '').strip()
    if not mes:
        return jsonify({'ok': False, 'error': 'Falta el mes (YYYY-MM)'}), 400
    nps.set_metrica_mensual(mes, data.get('resenas'), data.get('rating'))
    return jsonify({'ok': True})


@app.route('/api/nps/baseline', methods=['POST'])
def nps_baseline():
    """Panel: guarda el promedio historico (antes de automatizar) para medir
    el impacto real del sistema mas adelante."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    data = request.json or {}
    nps.set_baseline(data.get('resenas_mensuales_prom'), data.get('rating'), data.get('meses'))
    return jsonify({'ok': True})


# ══════════════════════════════════════════════════════════════════════════════
# COMPRAS / GASTOS / STOCK  (módulo autónomo, funciona en producción — Render)
# ══════════════════════════════════════════════════════════════════════════════
#
# App online multi-usuario (login propio, roles admin/registro/lectura). Sirve su
# propio frontend en /compras y su API en /api/compras/*. Datos en SQLite en el
# disco persistente (compras.py). NO usa ADMIN_TOKEN (ese protege la parte del
# sitio/agenda); acá la sesión es por usuario, header X-Compras-Token.

import compras as _compras

try:
    _compras.init_db()
    # Semilla opcional del primer admin desde env (para Render, sin llamar /setup).
    if _compras.contar_usuarios() == 0:
        _se, _sp = os.environ.get('COMPRAS_SEED_EMAIL'), os.environ.get('COMPRAS_SEED_PASSWORD')
        if _se and _sp:
            _compras.crear_usuario(_se, os.environ.get('COMPRAS_SEED_NOMBRE', 'Administrador'),
                                   _sp, rol='admin')
            print('[compras] primer usuario admin sembrado desde env')
except Exception as _e:
    print('[compras] init_db error:', _e)

def _compras_user():
    """Usuario dueño del X-Compras-Token, o None."""
    tok = request.headers.get('X-Compras-Token') or ''
    return _compras.usuario_por_sesion(tok)


def _require_compras(cap):
    """Devuelve (usuario, None) si el usuario tiene la CAPACIDAD 'cap', o
    (None, respuesta_error) si no. Ver compras.CAPS para el mapa rol→capacidades."""
    u = _compras_user()
    if not u:
        return None, (jsonify({'ok': False, 'error': 'No autenticado'}), 401)
    if not _compras.tiene_cap(u['rol'], cap):
        return None, (jsonify({'ok': False, 'error': 'Sin permiso para esta acción'}), 403)
    return u, None


def _print_autorizado():
    """El agente de impresión se autentica con X-Print-Token (env PRINT_TOKEN) o
    con una sesión admin. Sin PRINT_TOKEN configurado (local) se permite."""
    tok_env = os.environ.get('PRINT_TOKEN')
    if tok_env:
        if hmac.compare_digest(request.headers.get('X-Print-Token') or '', tok_env):
            return True
    else:
        return True
    u = _compras_user()
    return bool(u and u['rol'] == 'admin')


# ── Frontend + assets (disponibles también en producción) ─────────────────────

@app.route('/compras')
def compras_app():
    return send_from_directory('.', 'compras.html')

@app.route('/compras.js')
def compras_js():
    return send_from_directory('.', 'compras.js')


# ── Autenticación ──────────────────────────────────────────────────────────────

@app.route('/api/compras/setup', methods=['POST'])
@rate_limit("10 per hour")
def compras_setup():
    """Crea el PRIMER usuario admin. Solo funciona si no hay ningún usuario todavía."""
    if _compras.contar_usuarios() > 0:
        return jsonify({'ok': False, 'error': 'El sistema ya está configurado'}), 409
    d = request.json or {}
    try:
        uid = _compras.crear_usuario(d.get('email', ''), d.get('nombre', ''),
                                     d.get('password', ''), rol='admin')
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    token = _compras.crear_sesion(uid)
    return jsonify({'ok': True, 'token': token, 'usuario': _con_caps(
        {'email': d.get('email'), 'nombre': d.get('nombre'), 'rol': 'admin'})})

def _con_caps(u):
    """Adjunta al usuario sus capacidades (para que el frontend sepa qué mostrar) +
    el contador de pendientes por comprar (badge de solicitudes)."""
    return {**u, 'caps': sorted(_compras.CAPS.get(u['rol'], set())),
            'pendientes': _compras.contar_pendientes()}

@app.route('/api/compras/login', methods=['POST'])
@rate_limit("30 per hour")
def compras_login():
    d = request.json or {}
    u = _compras.verificar_login(d.get('email', ''), d.get('password', ''))
    if not u:
        return jsonify({'ok': False, 'error': 'Email o contraseña incorrectos'}), 401
    token = _compras.crear_sesion(u['id'])
    return jsonify({'ok': True, 'token': token, 'usuario': _con_caps(u)})

@app.route('/api/compras/logout', methods=['POST'])
def compras_logout():
    _compras.cerrar_sesion(request.headers.get('X-Compras-Token') or '')
    return jsonify({'ok': True})

@app.route('/api/compras/me', methods=['GET'])
def compras_me():
    u = _compras_user()
    if not u:
        return jsonify({'ok': False, 'configurado': _compras.contar_usuarios() > 0}), 401
    return jsonify({'ok': True, 'usuario': _con_caps(u)})


# ── Usuarios (admin) ───────────────────────────────────────────────────────────

@app.route('/api/compras/usuarios', methods=['GET'])
def compras_usuarios():
    _, err = _require_compras('admin')
    if err:
        return err
    return jsonify({'ok': True, 'usuarios': _compras.listar_usuarios()})

@app.route('/api/compras/usuarios', methods=['POST'])
def compras_usuarios_crear():
    _, err = _require_compras('admin')
    if err:
        return err
    d = request.json or {}
    try:
        uid = _compras.crear_usuario(d.get('email', ''), d.get('nombre', ''),
                                     d.get('password', ''), rol=d.get('rol', 'registro'))
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'id': uid})

@app.route('/api/compras/usuarios/actualizar', methods=['POST'])
def compras_usuarios_actualizar():
    _, err = _require_compras('admin')
    if err:
        return err
    d = request.json or {}
    try:
        _compras.actualizar_usuario(d.get('id'), nombre=d.get('nombre'), rol=d.get('rol'),
                                    activo=d.get('activo'), password=d.get('password') or None)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True})


# ── Categorías ─────────────────────────────────────────────────────────────────

@app.route('/api/compras/categorias', methods=['GET'])
def compras_categorias():
    _, err = _require_compras('stock')
    if err:
        return err
    return jsonify({'ok': True, 'categorias': _compras.listar_categorias(
        incluir_archivadas=request.args.get('todas') == '1')})

@app.route('/api/compras/categorias', methods=['POST'])
def compras_categorias_crear():
    _, err = _require_compras('admin')
    if err:
        return err
    try:
        cid = _compras.crear_categoria((request.json or {}).get('nombre', ''))
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'id': cid})

@app.route('/api/compras/categorias/actualizar', methods=['POST'])
def compras_categorias_actualizar():
    _, err = _require_compras('admin')
    if err:
        return err
    d = request.json or {}
    if 'nombre' in d and d.get('nombre'):
        _compras.renombrar_categoria(d.get('id'), d['nombre'])
    if 'archivada' in d:
        _compras.archivar_categoria(d.get('id'), bool(d['archivada']))
    return jsonify({'ok': True})


# ── Proveedores ────────────────────────────────────────────────────────────────

@app.route('/api/compras/proveedores', methods=['GET'])
def compras_proveedores():
    _, err = _require_compras('compras_ver')
    if err:
        return err
    return jsonify({'ok': True, 'proveedores': _compras.listar_proveedores(
        buscar=request.args.get('buscar', ''),
        incluir_archivados=request.args.get('todos') == '1')})

@app.route('/api/compras/proveedores', methods=['POST'])
def compras_proveedores_crear():
    _, err = _require_compras('registrar')
    if err:
        return err
    d = request.json or {}
    try:
        pid = _compras.crear_proveedor(d.get('nombre', ''), d.get('rut', ''),
                                       d.get('contacto', ''), d.get('notas', ''))
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'id': pid})

@app.route('/api/compras/proveedores/actualizar', methods=['POST'])
def compras_proveedores_actualizar():
    _, err = _require_compras('registrar')
    if err:
        return err
    d = request.json or {}
    _compras.actualizar_proveedor(d.pop('id', None), **{k: v for k, v in d.items()})
    return jsonify({'ok': True})


# ── Productos + códigos ────────────────────────────────────────────────────────

@app.route('/api/compras/productos', methods=['GET'])
def compras_productos():
    _, err = _require_compras('stock')
    if err:
        return err
    prods = _compras.listar_productos(buscar=request.args.get('buscar', ''),
                                      incluir_archivados=request.args.get('todos') == '1')
    # adjuntar última compra si se pide (para la vista de stock)
    if request.args.get('detalle') == '1':
        for p in prods:
            p['ultima_compra'] = _compras.ultima_compra_producto(p['id'])
    return jsonify({'ok': True, 'productos': prods})

@app.route('/api/compras/productos/<int:pid>', methods=['GET'])
def compras_producto_detalle(pid):
    _, err = _require_compras('stock')
    if err:
        return err
    p = _compras.obtener_producto(pid)
    if not p:
        return jsonify({'ok': False, 'error': 'Producto no encontrado'}), 404
    p['ultima_compra'] = _compras.ultima_compra_producto(pid)
    p['historial_precios'] = _compras.historial_precios(pid)
    p['movimientos'] = _compras.movimientos_producto(pid)
    return jsonify({'ok': True, 'producto': p})

@app.route('/api/compras/productos', methods=['POST'])
def compras_productos_crear():
    _, err = _require_compras('registrar')
    if err:
        return err
    d = request.json or {}
    try:
        pid = _compras.crear_producto(
            d.get('nombre', ''), d.get('categoria_prod', ''),
            d.get('unidad', 'unidad'), d.get('stock_minimo', 0),
            d.get('notas', ''), d.get('stock_inicial', 0))
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    # opcional: mapear un código escaneado al crear
    if d.get('codigo'):
        try:
            _compras.agregar_codigo(pid, d['codigo'], d.get('codigo_origen', 'fabricante'))
        except ValueError:
            pass
    return jsonify({'ok': True, 'id': pid})

@app.route('/api/compras/productos/actualizar', methods=['POST'])
def compras_productos_actualizar():
    _, err = _require_compras('registrar')
    if err:
        return err
    d = request.json or {}
    _compras.actualizar_producto(d.pop('id', None), **{k: v for k, v in d.items()})
    return jsonify({'ok': True})

@app.route('/api/compras/productos/codigo', methods=['POST'])
def compras_producto_codigo():
    """Mapea un código (barras/QR) a un producto (mapeo-al-primer-escaneo)."""
    _, err = _require_compras('registrar')
    if err:
        return err
    d = request.json or {}
    try:
        nuevo = _compras.agregar_codigo(d.get('producto_id'), d.get('codigo', ''),
                                        d.get('origen', 'fabricante'))
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'nuevo': nuevo})

@app.route('/api/compras/productos/generar-codigo', methods=['POST'])
def compras_producto_generar_codigo():
    """Genera un código propio para un producto sin código de fabricante y (opcional)
    lo encola para imprimir su etiqueta."""
    _, err = _require_compras('registrar')
    if err:
        return err
    d = request.json or {}
    pid = d.get('producto_id')
    codigo = _compras.generar_codigo_propio(pid)
    if d.get('imprimir'):
        _compras.encolar_impresion(pid, codigo, int(d.get('cantidad', 1)))
    return jsonify({'ok': True, 'codigo': codigo})


# ── Escaneo / salida de stock ──────────────────────────────────────────────────

@app.route('/api/compras/codigo/<path:codigo>', methods=['GET'])
def compras_resolver_codigo(codigo):
    """Resuelve un código escaneado a su producto. 404 = código no mapeado (el
    frontend ofrece asociarlo a un producto)."""
    _, err = _require_compras('escanear')
    if err:
        return err
    p = _compras.producto_por_codigo(codigo)
    if not p:
        return jsonify({'ok': False, 'error': 'Código no reconocido'}), 404
    return jsonify({'ok': True, 'producto': p})

@app.route('/api/compras/salida', methods=['POST'])
def compras_salida():
    """Descuenta stock. Acepta {codigo} (escaneo) o {producto_id}."""
    u, err = _require_compras('escanear')
    if err:
        return err
    d = request.json or {}
    cant = float(d.get('cantidad', 1) or 1)
    motivo = d.get('motivo', 'Consumo')
    if d.get('codigo'):
        prod, nuevo = _compras.salida_por_codigo(d['codigo'], cant, motivo, u['id'])
        if not prod:
            return jsonify({'ok': False, 'error': 'Código no reconocido'}), 404
    elif d.get('producto_id'):
        nuevo = _compras.registrar_movimiento(d['producto_id'], 'salida', cant, motivo, u['id'])
        prod = _compras.obtener_producto(d['producto_id'])
    else:
        return jsonify({'ok': False, 'error': 'Falta código o producto'}), 400
    return jsonify({'ok': True, 'producto': {'id': prod['id'], 'nombre': prod['nombre'],
                    'unidad': prod['unidad']}, 'stock_actual': nuevo})


# ── Movimientos de stock (entrada/salida/ajuste manual) ────────────────────────

@app.route('/api/compras/movimiento', methods=['POST'])
def compras_movimiento():
    u, err = _require_compras('registrar')
    if err:
        return err
    d = request.json or {}
    try:
        nuevo = _compras.registrar_movimiento(d.get('producto_id'), d.get('tipo'),
                                              d.get('cantidad'), d.get('motivo', ''), u['id'])
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'stock_actual': nuevo})

@app.route('/api/compras/alertas', methods=['GET'])
def compras_alertas():
    _, err = _require_compras('stock')
    if err:
        return err
    return jsonify({'ok': True, 'productos': _compras.productos_bajo_minimo()})


# ── Compras (cabecera + ítems) ─────────────────────────────────────────────────

@app.route('/api/compras/compras', methods=['GET'])
def compras_listar():
    _, err = _require_compras('compras_ver')
    if err:
        return err
    a = request.args
    return jsonify({'ok': True, 'compras': _compras.listar_compras(
        desde=a.get('desde') or None, hasta=a.get('hasta') or None,
        proveedor_id=a.get('proveedor_id') or None, categoria_id=a.get('categoria_id') or None,
        tipo_gasto=a.get('tipo_gasto') or None)})

@app.route('/api/compras/compras/<int:cid>', methods=['GET'])
def compras_obtener(cid):
    _, err = _require_compras('compras_ver')
    if err:
        return err
    c = _compras.obtener_compra(cid)
    if not c:
        return jsonify({'ok': False, 'error': 'Compra no encontrada'}), 404
    return jsonify({'ok': True, 'compra': c})

@app.route('/api/compras/compras', methods=['POST'])
def compras_crear():
    u, err = _require_compras('registrar')
    if err:
        return err
    d = request.json or {}
    try:
        cid = _compras.crear_compra(d.get('cabecera', {}), d.get('items', []), u['id'])
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'id': cid})

@app.route('/api/compras/compras/actualizar', methods=['POST'])
def compras_actualizar():
    """Edita la cabecera de una compra (agregar costo de importación que llega después,
    ajustar despacho/tipo de cambio/moneda, etc.). Recalcula el total."""
    _, err = _require_compras('registrar')
    if err:
        return err
    d = request.json or {}
    try:
        res = _compras.actualizar_compra(d.pop('id', None), d)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, **res})

@app.route('/api/compras/compras/eliminar', methods=['POST'])
def compras_eliminar():
    _, err = _require_compras('admin')
    if err:
        return err
    _compras.eliminar_compra((request.json or {}).get('id'))
    return jsonify({'ok': True})


# ── Foto de la factura/boleta (respaldo; OCR es Fase 3) ────────────────────────

@app.route('/api/compras/foto', methods=['POST'])
def compras_foto_subir():
    _, err = _require_compras('registrar')
    if err:
        return err
    f = request.files.get('file')
    if not f:
        return jsonify({'ok': False, 'error': 'Falta el archivo'}), 400
    _compras.FOTOS_DIR.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(f.filename or '')[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.pdf'):
        ext = '.jpg'
    nombre = f"{_compras.ahora_cl().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}{ext}"
    f.save(str(_compras.FOTOS_DIR / nombre))
    return jsonify({'ok': True, 'foto_path': nombre})

@app.route('/api/compras/foto/<path:nombre>', methods=['GET'])
def compras_foto_ver(nombre):
    _, err = _require_compras('compras_ver')
    if err:
        return err
    if '/' in nombre or '\\' in nombre or '..' in nombre:
        return jsonify({'ok': False, 'error': 'Nombre inválido'}), 400
    return send_from_directory(str(_compras.FOTOS_DIR), nombre)


# ── Reportes + export Excel ────────────────────────────────────────────────────

@app.route('/api/compras/reportes', methods=['GET'])
def compras_reportes():
    _, err = _require_compras('reportes')
    if err:
        return err
    a = request.args
    return jsonify({'ok': True, 'reporte': _compras.resumen_gastos(
        desde=a.get('desde') or None, hasta=a.get('hasta') or None)})

@app.route('/api/compras/export.xlsx', methods=['GET'])
def compras_export():
    _, err = _require_compras('reportes')
    if err:
        return err
    from openpyxl import Workbook
    from io import BytesIO
    a = request.args
    filas = _compras.filas_export(desde=a.get('desde') or None, hasta=a.get('hasta') or None)
    wb = Workbook()
    ws = wb.active
    ws.title = 'Compras'
    cols = ['Fecha', 'Proveedor', 'Tipo doc', 'N° doc', 'Forma pago', 'Tipo gasto',
            'Categoría', 'Producto', 'Marca', 'Cantidad', 'Precio unitario', 'Subtotal',
            'Moneda', 'Tipo cambio', 'Despacho', 'Importación (CLP)', 'Total (moneda)', 'Total CLP']
    ws.append(cols)
    for r in filas:
        ws.append([r.get('fecha'), r.get('proveedor'), r.get('tipo_doc'), r.get('nro_doc'),
                   r.get('forma_pago'), r.get('tipo_gasto'), r.get('categoria'), r.get('producto'),
                   r.get('marca'), r.get('cantidad'), r.get('precio_unitario'), r.get('subtotal'),
                   r.get('moneda'), r.get('tipo_cambio'), r.get('costo_despacho'),
                   r.get('costo_importacion'), r.get('total'), r.get('total_clp')])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    from flask import Response
    return Response(buf.read(),
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': 'attachment; filename=compras.xlsx'})


# ── QR + cola de impresión (agente en el PC de la clínica) ─────────────────────

@app.route('/api/compras/qr/<path:codigo>.png', methods=['GET'])
def compras_qr(codigo):
    """Genera el PNG del QR de un código, para mostrar/imprimir la etiqueta."""
    try:
        import segno
    except ImportError:
        return jsonify({'ok': False, 'error': 'segno no instalado'}), 500
    from io import BytesIO
    from flask import Response
    buf = BytesIO()
    segno.make(codigo, error='m').save(buf, kind='png', scale=6, border=2)
    buf.seek(0)
    return Response(buf.read(), mimetype='image/png')

@app.route('/api/compras/impresion/cola', methods=['GET'])
def compras_impresion_cola():
    if not _print_autorizado():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    return jsonify({'ok': True, 'trabajos': _compras.cola_pendiente()})

@app.route('/api/compras/impresion/marcar', methods=['POST'])
def compras_impresion_marcar():
    if not _print_autorizado():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    d = request.json or {}
    _compras.marcar_impresion(d.get('id'), d.get('estado', 'impreso'))
    return jsonify({'ok': True})

@app.route('/api/compras/impresion/encolar', methods=['POST'])
def compras_impresion_encolar():
    _, err = _require_compras('registrar')
    if err:
        return err
    d = request.json or {}
    jid = _compras.encolar_impresion(d.get('producto_id'), d.get('codigo', ''),
                                     int(d.get('cantidad', 1)))
    return jsonify({'ok': True, 'id': jid})


# ── Solicitudes de compra (pendientes + sugerencias por consumo) ───────────────

@app.route('/api/compras/solicitudes', methods=['GET'])
def compras_solicitudes():
    _, err = _require_compras('solicitar')
    if err:
        return err
    return jsonify({'ok': True, 'pendientes': _compras.listar_pendientes()})

@app.route('/api/compras/solicitudes/sugerencias', methods=['GET'])
def compras_solicitudes_sugerencias():
    _, err = _require_compras('solicitar')
    if err:
        return err
    cob = int(request.args.get('cobertura', 60) or 60)
    return jsonify({'ok': True, 'sugerencias': _compras.productos_sugeridos(cob)})

@app.route('/api/compras/solicitudes/sugerir', methods=['GET'])
def compras_solicitudes_sugerir():
    """Sugiere una cantidad para UN producto, según su consumo/historial."""
    _, err = _require_compras('solicitar')
    if err:
        return err
    pid = request.args.get('producto_id')
    if not pid:
        return jsonify({'ok': False, 'error': 'Falta producto_id'}), 400
    cob = int(request.args.get('cobertura', 60) or 60)
    return jsonify({'ok': True, 'sugerencia': _compras.sugerir_cantidad(int(pid), cob)})

@app.route('/api/compras/solicitudes', methods=['POST'])
def compras_solicitudes_crear():
    u, err = _require_compras('solicitar')
    if err:
        return err
    d = request.json or {}
    try:
        n = _compras.crear_solicitud(d.get('items', []), u['id'], d.get('nota', ''))
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    _notificar_solicitud_admins(u, d.get('items', []))
    return jsonify({'ok': True, 'n': n})

@app.route('/api/compras/solicitudes/cancelar', methods=['POST'])
def compras_solicitudes_cancelar():
    _, err = _require_compras('solicitar')
    if err:
        return err
    _compras.cancelar_pendiente((request.json or {}).get('id'))
    return jsonify({'ok': True})


def _notificar_solicitud_admins(usuario, items):
    """Notifica a los administradores (correo de la clínica) que hay una nueva solicitud
    de compra. Best-effort: si el SMTP no está configurado, no rompe nada."""
    try:
        import notify
        prods = _compras.listar_productos()
        by_id = {p['id']: p for p in prods}
        filas = ''
        for it in (items or []):
            p = by_id.get(it.get('producto_id'), {})
            filas += (f"<tr><td style='padding:6px 10px'>{p.get('nombre', '—')}</td>"
                      f"<td style='padding:6px 10px'>{it.get('cantidad', '')} {p.get('unidad', '')}</td></tr>")
        html = (f"<h2>Nueva solicitud de compra</h2>"
                f"<p>Solicitada por <b>{usuario.get('nombre', '')}</b> "
                f"({usuario.get('email', '')}).</p>"
                f"<table style='border-collapse:collapse'>"
                f"<tr><th style='text-align:left;padding:6px 10px'>Producto</th>"
                f"<th style='text-align:left;padding:6px 10px'>Cantidad sugerida</th></tr>"
                f"{filas}</table>"
                f"<p>Revísala en el sistema de compras → pestaña «Solicitudes».</p>")
        notify._enviar_email_recepcion('🛒 Nueva solicitud de compra', html)
    except Exception as e:
        print('[compras] no se pudo notificar solicitud:', e)


# ── Cargos recurrentes (suscripciones) ──────────────────────────────────────────

@app.route('/api/compras/suscripciones', methods=['GET'])
def compras_suscripciones():
    _, err = _require_compras('registrar')
    if err:
        return err
    return jsonify({'ok': True, 'suscripciones': _compras.listar_suscripciones(
        solo_activas=request.args.get('activas') == '1')})

@app.route('/api/compras/suscripciones', methods=['POST'])
def compras_suscripciones_crear():
    u, err = _require_compras('registrar')
    if err:
        return err
    d = request.json or {}
    try:
        sid, cid = _compras.crear_suscripcion(d, u['id'])
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'id': sid, 'compra_id': cid})

@app.route('/api/compras/suscripciones/actualizar', methods=['POST'])
def compras_suscripciones_actualizar():
    _, err = _require_compras('registrar')
    if err:
        return err
    d = request.json or {}
    try:
        _compras.actualizar_suscripcion(d.pop('id', None), d)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True})

@app.route('/api/compras/suscripciones/cortar', methods=['POST'])
def compras_suscripciones_cortar():
    _, err = _require_compras('registrar')
    if err:
        return err
    d = request.json or {}
    _compras.cortar_suscripcion(d.get('id'), d.get('fecha_fin'))
    return jsonify({'ok': True})


# ══════════════════════════════════════════════════════════════════════════════
# REFRESCO AUTOMÁTICO DE PACIENTES (2x/día) + BARRIDO DE CONFIRMACIONES (4 ciclos)
# ══════════════════════════════════════════════════════════════════════════════

_SCHEDULER_INICIADO = False

def _loop_refresco_pacientes():
    import time, pacientes
    primera = True
    while True:
        try:
            cfg = scheduling.load_config()
            if cfg['dentidesk']['enabled']:
                # En el primer ciclo solo construye si la base esta vacia
                # (evita un barrido completo en cada redeploy si ya hay datos).
                if not primera or pacientes.total() == 0:
                    pacientes.construir_desde_agenda(cfg, dias_atras=180, dias_adelante=120)
        except Exception as e:
            print('[refresco pacientes] error:', e)
        primera = False
        time.sleep(12 * 3600)  # cada 12 horas

_HORARIOS_CONFIRMACION = ['11:00', '13:30', '17:00', '19:45']  # hora de Chile

def _loop_confirmaciones():
    """Dispara el barrido de confirmaciones a horas fijas (Chile). La 1a corrida
    siembra sin enviar; luego solo envia a citas nuevas (presenciales/telefono)."""
    import time
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo('America/Santiago')
    except Exception:
        tz = None
    ya_corrio = {}
    while True:
        try:
            ahora = datetime.now(tz) if tz else datetime.now()
            slot = ahora.strftime('%H:%M')
            if slot in _HORARIOS_CONFIRMACION and ya_corrio.get(slot) != ahora.date():
                ya_corrio[slot] = ahora.date()
                cfg = scheduling.load_config()
                if cfg['dentidesk']['enabled']:
                    import confirmaciones
                    r = confirmaciones.barrer_y_confirmar(cfg)
                    print('[confirmaciones]', slot, r)
        except Exception as e:
            print('[confirmaciones] error:', e)
        time.sleep(40)

def _loop_recordatorios():
    """Dispara los 3 recordatorios de WhatsApp (semana/dia/inasistencia), cada
    uno a la hora que tenga configurada en recordatorios_wa (panel admin ->
    pestania WhatsApp). Mismo esqueleto que _loop_confirmaciones: revisa cada
    40s, un solo disparo por (tipo, dia)."""
    import time
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo('America/Santiago')
    except Exception:
        tz = None
    ya_corrio = {}  # {tipo: date}
    while True:
        try:
            ahora = datetime.now(tz) if tz else datetime.now()
            slot = ahora.strftime('%H:%M')
            cfg_dd = scheduling.load_config()
            if cfg_dd['dentidesk']['enabled']:
                wa_cfg = recordatorios_wa.load_config()
                pasadas = (
                    ('semana', wa_cfg['recordatorio_semana'],
                     lambda: recordatorios_wa.enviar_recordatorios_semana(cfg_dd)),
                    ('dia', wa_cfg['recordatorio_dia'],
                     lambda: recordatorios_wa.enviar_recordatorios_dia(cfg_dd)),
                    ('inasistencia', wa_cfg['inasistencia_reagendar'],
                     lambda: recordatorios_wa.enviar_inasistencias(cfg_dd)),
                )
                for tipo, tipo_cfg, fn in pasadas:
                    if not tipo_cfg.get('activo'):
                        continue
                    if slot == tipo_cfg.get('hora') and ya_corrio.get(tipo) != ahora.date():
                        ya_corrio[tipo] = ahora.date()
                        r = fn()
                        print('[recordatorios]', tipo, slot, r)
        except Exception as e:
            print('[recordatorios] error:', e)
        time.sleep(40)

def _procesar_programados_vencidos(cfg_dd, hoy):
    """Procesa los recordatorios de control programados que ya vencieron
    (recaptacion.pendientes_vencidos). Por cada uno:
      1. Relee la cita en DentiDesk con fecha_cita+id_agenda (dentidesk.info_cita)
         para tomar el TELEFONO FRESCO -- pudo cambiar desde que se programo.
         Si la cita ya no esta ese dia -> omitido.
      2. Vuelve a correr recaptacion.evaluar(rut) -- este es el punto central
         de la feature: el paciente pudo agendar solo en el intertanto
         (ya_tiene_hora) o ya se le mando otro recordatorio (enviado_reciente),
         y mandarle el programado igual seria absurdo. Si bloquea -> omitido
         con el motivo.
      3. Si pasa, envia con notify.enviar_recordatorio_control(); si sale ok,
         marca 'enviado' + recaptacion.marcar_enviado() (mismo registro
         anti-duplicados que el envio manual).
    Los fallos de RED (DentiDesk o el envio del WhatsApp) NO marcan nada --
    el programado sigue 'pendiente' y se reintenta manana (pendientes_vencidos
    lo vuelve a traer porque su fecha_programada ya paso). Dentro del MISMO
    dia no hay reintento infinito porque el loop llamador solo dispara una vez
    por dia (guardia 'ya_corrio')."""
    import pacientes as _pacientes
    stats = {'enviados': 0, 'omitidos': 0, 'con_error': 0}
    for p in recaptacion.pendientes_vencidos(hoy):
        rut = p.get('rut', '')
        id_agenda = p.get('id_agenda', '')
        try:
            fecha_cita = date.fromisoformat((p.get('fecha_cita') or '')[:10])
        except (TypeError, ValueError):
            recaptacion.marcar_programado(p['id'], 'omitido', 'fecha_cita invalida en el registro')
            stats['omitidos'] += 1
            continue

        try:
            cita_raw = dentidesk.info_cita(cfg_dd, id_agenda, fecha_cita)
        except Exception as e:
            print('[recaptacion-programados] error releyendo cita', id_agenda, e)
            stats['con_error'] += 1
            continue  # no se marca nada -- reintenta manana

        if not cita_raw:
            recaptacion.marcar_programado(p['id'], 'omitido', 'la cita de origen ya no existe en DentiDesk')
            stats['omitidos'] += 1
            continue

        telefono = (cita_raw.get('Phone') or '').strip()
        doctor = (cita_raw.get('ProfessionalName') or '').strip() or p.get('doctor', '')
        nombres_raw, _ = _pacientes._split_nombre(cita_raw.get('PatientName', ''))
        nombre = nombres_raw or p.get('nombre') or 'Paciente'

        tel_norm = wa_cloud._normalizar_telefono(telefono)
        if len(tel_norm) != 11 or not tel_norm.startswith('569'):
            recaptacion.marcar_programado(p['id'], 'omitido', 'la cita ya no tiene un celular chileno valido')
            stats['omitidos'] += 1
            continue

        bloqueo = recaptacion.evaluar(rut)
        if bloqueo:
            # Se guarda el 'detalle' (texto legible), NO el 'motivo' (slug
            # interno): este campo lo muestra tal cual la pestania del panel, y
            # "ya_tiene_hora" no le dice nada a quien lo lee. El detalle ademas
            # trae la fecha de la hora que el paciente saco por su cuenta, que
            # es justo el dato que explica por que no se envio.
            recaptacion.marcar_programado(p['id'], 'omitido', bloqueo['detalle'])
            stats['omitidos'] += 1
            continue

        cita_dict = {
            'nombre': nombre,
            'telefono': telefono,
            'doctor_nombre': doctor,
            'fecha_legible': recaptacion.fecha_legible_larga(fecha_cita),
            'fecha': fecha_cita.isoformat(),
            'id_agenda': id_agenda,
        }
        resultado = notify.enviar_recordatorio_control(cita_dict)
        if not resultado.get('ok'):
            print('[recaptacion-programados] fallo al enviar', id_agenda, resultado.get('error'))
            stats['con_error'] += 1
            continue  # no se marca nada -- reintenta manana

        recaptacion.marcar_enviado(rut, id_agenda, doctor, nombre)
        recaptacion.marcar_programado(p['id'], 'enviado')
        stats['enviados'] += 1
    return stats


# Hora tope para procesar programados atrasados (ver la ventana en el loop).
# Pasada esta hora se deja para el dia siguiente: un recordatorio que llega al
# final de la tarde ya no alcanza a ser contestado por recepcion el mismo dia,
# y el paciente que toca "Agendar por WhatsApp" abre una ventana de 24h que
# conviene que empiece con alguien disponible para responderle.
_LIMITE_PROGRAMADOS = '17:00'


def _loop_recaptacion_programados():
    """Dispara los recordatorios de control PROGRAMADOS (recaptacion.programar),
    una vez por dia a la hora 'hora_envio_programados' (panel). Mismo
    esqueleto que _loop_recordatorios (poll 40s, un disparo por dia con
    'ya_corrio'). Se gatilla con el MISMO criterio que _loop_recordatorios
    (cfg_dd['dentidesk']['enabled']): procesar un programado exige releer la
    cita en DentiDesk y volver a evaluar citas_futuras_paciente, que sin
    DentiDesk habilitado no tiene con que trabajar."""
    import time
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo('America/Santiago')
    except Exception:
        tz = None
    ya_corrio = None
    while True:
        try:
            ahora = datetime.now(tz) if tz else datetime.now()
            slot = ahora.strftime('%H:%M')
            cfg_dd = scheduling.load_config()
            rcfg = recaptacion.load_config()
            hora_cfg = rcfg.get('hora_envio_programados', '10:00')
            # VENTANA, no minuto exacto (a diferencia de _loop_recordatorios):
            # dispara en el primer tick a partir de la hora configurada y hasta
            # _LIMITE_PROGRAMADOS. Con igualdad exacta bastaba que Render
            # reiniciara a las 10:01 para que ese dia no saliera NADA y nadie se
            # enterara. La cota de arriba evita el otro extremo: que tras una
            # caida larga el sistema despierte a media tarde y mande los
            # recordatorios cuando ya nadie alcanza a contestarlos. Si se pierde
            # la ventana completa, no se pierde el envio: pendientes_vencidos()
            # usa <=, asi que el programado sale al dia siguiente.
            if (cfg_dd['dentidesk']['enabled']
                    and hora_cfg <= slot < _LIMITE_PROGRAMADOS
                    and ya_corrio != ahora.date()):
                ya_corrio = ahora.date()
                r = _procesar_programados_vencidos(cfg_dd, ahora.date())
                print('[recaptacion-programados]', slot, r)
        except Exception as e:
            print('[recaptacion-programados] error:', e)
        time.sleep(40)

def _procesar_control_dental(cfg_cd, hoy):
    """Barre la agenda y envia (email) los recordatorios de control dental
    vencidos. Mismo criterio que _procesar_programados_vencidos: los fallos
    de RED (SMTP) NO marcan nada -- el paciente sigue con su proximo_envio
    viejo y se reintenta en el proximo barrido, asi un problema de SMTP no le
    come el ciclo de 6 meses al paciente.

    1. control_dental.barrer() -- una sola pasada por la agenda (-7/+45 dias)
       que resuelve inscripciones/bajas/señal de vida para TODA la cartera.
    2. control_dental.pendientes_hoy(hoy) -- ya viene ordenado por
       proximo_envio ASCENDENTE (los mas vencidos primero).
    3. Respeta cfg_cd['max_envios_por_dia'] (anti-oleada): corta ahi y
       LOGUEA cuantos quedaron para mañana (no se silencia).
    4. Por cada uno, control_dental.evaluar(rut) -- si bloquea, NO se envia;
       si el motivo es 'sin_email' se acumula para el aviso agrupado a
       recepcion; si es 'pausado_inactivo' (u otro) se deja el estado tal
       como quedo (evaluar() no muta nada, solo informa).
    5. Si evaluar() no bloquea, se envia con
       notify.enviar_recordatorio_control_dental(); SOLO si 'ok' es True se
       llama a control_dental.marcar_enviado(rut).
    6. Al final, un UNICO aviso agrupado a recepcion con los sin_email
       (nunca uno por paciente).

    Devuelve {'enviados','omitidos','sin_email','pendientes_manana'}."""
    control_dental.barrer(cfg_cd)
    pendientes = control_dental.pendientes_hoy(hoy)

    max_envios = cfg_cd.get('max_envios_por_dia', 30)
    a_procesar = pendientes[:max_envios]
    pendientes_manana = len(pendientes) - len(a_procesar)
    if pendientes_manana > 0:
        print(f'[control-dental] {pendientes_manana} pendiente(s) quedan para '
              f'manana (tope {max_envios}/dia)')

    stats = {'enviados': 0, 'omitidos': 0, 'sin_email': 0, 'pendientes_manana': pendientes_manana}
    sin_email = []
    for p in a_procesar:
        rut = p.get('rut', '')
        bloqueo = control_dental.evaluar(rut, cfg_cd)
        if bloqueo:
            if bloqueo.get('motivo') == 'sin_email':
                sin_email.append(p)
                stats['sin_email'] += 1
            else:
                stats['omitidos'] += 1
            continue

        resultado = notify.enviar_recordatorio_control_dental(p, cfg_cd)
        if not resultado.get('ok'):
            print('[control-dental] fallo al enviar a', rut, resultado.get('error'))
            stats['omitidos'] += 1
            continue  # no se marca nada -- reintenta manana, no consume el ciclo

        control_dental.marcar_enviado(rut)
        stats['enviados'] += 1

    if sin_email:
        notify.avisar_recepcion_control_dental_sin_email(sin_email)

    return stats


def _loop_control_dental():
    """Barrido diario de control dental. Mismo esqueleto que
    _loop_recaptacion_programados, INCLUYENDO el patron de VENTANA
    (hora_envio <= slot < '17:00') en vez de igualdad exacta de minuto: con
    igualdad exacta bastaba que Render reiniciara justo en ese minuto para
    que ese dia no saliera nada y nadie se enterara. Respeta
    cfg_cd['activo'] (False por defecto -- se enciende solo cuando la
    clinica reviso la cartera inscrita por el backfill) y
    cfg_dd['dentidesk']['enabled'] (el barrido necesita leer getAgendaDay)."""
    import time
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo('America/Santiago')
    except Exception:
        tz = None
    ya_corrio = None
    while True:
        try:
            ahora = datetime.now(tz) if tz else datetime.now()
            slot = ahora.strftime('%H:%M')
            cfg_dd = scheduling.load_config()
            cfg_cd = control_dental.load_config()
            hora_cfg = cfg_cd.get('hora_envio', '11:00')
            if (cfg_cd.get('activo')
                    and cfg_dd['dentidesk']['enabled']
                    and hora_cfg <= slot < '17:00'
                    and ya_corrio != ahora.date()):
                ya_corrio = ahora.date()
                r = _procesar_control_dental(cfg_cd, ahora.date())
                print('[control-dental]', slot, r)
        except Exception as e:
            print('[control-dental] error:', e)
        time.sleep(40)


def _procesar_nps(cfg_nps, cfg_dd, ahora):
    """Barre las citas ATENDIDAS de ayer y hoy y manda la encuesta de
    satisfaccion (WhatsApp) a las que corresponda. 'ahora' es un datetime con
    tz de Santiago (mismo criterio que _procesar_programados_vencidos).

    Anti-oleada: la primera corrida (nps.esta_sembrado() False) solo marca
    como vistas las citas ya atendidas de ayer/hoy, SIN enviar -- mismo
    criterio que la primera corrida de confirmaciones.barrer_y_confirmar():
    sin esto, encender el sistema mandaria encuestas a cientos de pacientes
    que se atendieron antes de que existiera.

    Timing y ventana: una cita recien atendida espera
    cfg_nps['horas_despues_atencion'] antes de calificar, y solo se envia
    dentro de la ventana horaria configurada -- si termino tarde, el barrido
    de mañana la vuelve a mirar (como 'ayer') y sale en la ventana de la
    mañana. Ninguno de estos dos casos marca 'visto': se reintentan solos en
    el proximo barrido, no hay que reprocesar toda la agenda a mano.

    Devuelve {'ok': True, 'sembrado': True} en la siembra, o
    {'ok': True, 'enviados': N} en corridas normales."""
    import pacientes as _pac
    scfg = cfg_dd
    hoy = ahora.date()
    ayer = hoy - timedelta(days=1)

    if not nps.esta_sembrado():
        for target in (ayer, hoy):
            try:
                citas = dentidesk._get_agenda_day(scfg, target)
            except Exception:
                continue
            for c in citas:
                ida = str(c.get('IdAgenda') or '')
                if not ida:
                    continue
                estado_txt = (c.get('Status') or '').lower()
                if 'atendid' not in estado_txt:
                    continue
                nps.marcar_visto(ida, target.isoformat())
        nps.marcar_sembrado()
        return {'ok': True, 'sembrado': True}

    enviados = 0
    max_envios = cfg_nps.get('max_envios_por_dia', 30)
    dentro_ventana = nps.dentro_de_ventana(cfg_nps, ahora.strftime('%H:%M'))
    tope_alcanzado = False

    # ── Fase 1: overrides 'enviar' forzados a mano desde el F2 ──────────────
    # Se procesan directo del registro (no del scan de la agenda): la cita pudo
    # caer fuera de la ventana ayer/hoy para cuando llega la hora real de
    # enviar. Un 'enviar' salta la elegibilidad por tipo y el cooldown (la
    # asistente manda), pero respeta 'no molestar' (opt-out del paciente) y el
    # timing (horas_despues + ventana). Los datos (telefono/nombre/doctor/hora/
    # duracion) se guardaron al hacer el click, resueltos frescos de DentiDesk.
    for o in nps.overrides_enviar_pendientes():
        if tope_alcanzado:
            break
        ida = str(o.get('id_agenda') or '')
        if not ida:
            continue
        if nps.ya_visto(ida):
            nps.marcar_override(ida, 'enviado')  # ya salio por otra via
            continue
        telefono = (o.get('telefono') or '').strip()
        if not telefono:
            nps.marcar_override(ida, 'omitido')
            continue
        rut = o.get('rut') or ''
        if nps.en_no_molestar(rut):
            nps.marcar_override(ida, 'omitido')
            nps.marcar_visto(ida, o.get('fecha_cita') or hoy.isoformat())
            continue
        # Timing: fin = fecha_cita + hora_cita + duracion. Si no se puede
        # parsear, se considera lista (el override es una decision explicita).
        fin = None
        try:
            f_cita = date.fromisoformat((o.get('fecha_cita') or '')[:10])
            hh, mm = (o.get('hora_cita') or '')[:5].split(':')
            base = datetime(f_cita.year, f_cita.month, f_cita.day,
                             int(hh), int(mm), tzinfo=ahora.tzinfo)
            fin = base + timedelta(minutes=int(o.get('duracion') or 0))
        except (ValueError, TypeError):
            fin = None
        terminada = True
        if fin is not None:
            terminada = (ahora - fin) >= timedelta(hours=cfg_nps.get('horas_despues_atencion', 3))
        if not terminada or not dentro_ventana:
            continue  # aun no -- queda pendiente, se reintenta solo
        if enviados >= max_envios:
            tope_alcanzado = True
            break
        # 'cuando' ({{2}}): dias entre la atencion y hoy. 0->hoy, 1->ayer,
        # 2+->'hace unos dias' (un override viejo aprobado con retraso).
        cuando = 'hoy'
        try:
            fc = date.fromisoformat((o.get('fecha_cita') or '')[:10])
            delta = (hoy - fc).days
            cuando = 'hoy' if delta <= 0 else ('ayer' if delta == 1 else 'hace unos días')
        except (ValueError, TypeError):
            cuando = 'hoy'
        cita = {
            'nombre': o.get('nombre') or 'paciente',
            'telefono': telefono,
            'doctor_nombre': o.get('doctor') or '',
            'id_agenda': ida,
            'fecha': o.get('fecha_cita') or '',
            'cuando': cuando,
        }
        r = notify.enviar_nps(cita)
        if r.get('ok'):
            nps.registrar_envio(rut, ida, cita['doctor_nombre'])
            nps.marcar_visto(ida, o.get('fecha_cita') or hoy.isoformat())
            nps.marcar_override(ida, 'enviado')
            enviados += 1
        else:
            print('[nps] fallo al enviar override a', rut, r.get('error'))
            # queda pendiente -- reintenta en el proximo barrido

    # ── Fase 2: barrido automatico de citas atendidas ──────────────────────
    for target in (ayer, hoy):
        if tope_alcanzado:
            break
        try:
            citas = dentidesk._get_agenda_day(scfg, target)
        except Exception:
            continue
        for c in citas:
            ida = str(c.get('IdAgenda') or '')
            if not ida or nps.ya_visto(ida):
                continue

            estado_txt = (c.get('Status') or '').lower()
            if 'atendid' not in estado_txt:
                continue  # puede atenderse mas tarde -- NO marcar visto

            # Override manual del F2: 'no_enviar' bloquea esta cita para
            # siempre; 'enviar' ya lo maneja la fase 1 (saltar aca para no
            # duplicar ni pisar su timing forzado).
            ov = nps.get_override(ida)
            if ov:
                if ov.get('accion') == 'no_enviar':
                    nps.marcar_visto(ida, target.isoformat())
                    continue
                if ov.get('accion') == 'enviar':
                    continue

            disparo = nps.clasificar_disparo(c.get('Reason'))
            if disparo is None:
                # atendida pero motivo no encuestable -- no reconsiderar
                nps.marcar_visto(ida, target.isoformat())
                continue

            es_hito = (disparo == 'hito')
            if disparo == 'periodico' and not cfg_nps.get('periodico_activo', True):
                nps.marcar_visto(ida, target.isoformat())
                continue

            telefono = (c.get('Phone') or '').strip()
            if not telefono:
                # sin telefono no se puede -- no reintentar
                nps.marcar_visto(ida, target.isoformat())
                continue

            # Timing: hora de termino = inicio (time "HH:MM") + duration min,
            # tz-aware en 'target'. Si no se puede parsear, se trata la cita
            # como ya terminada (no bloquear por un dato faltante).
            terminada = True
            fin = None
            hora_ini = (c.get('time') or '')[:5]
            try:
                hh, mm = hora_ini.split(':')
                base = datetime(target.year, target.month, target.day,
                                 int(hh), int(mm), tzinfo=ahora.tzinfo)
                dur = int(c.get('duration') or 0)
                fin = base + timedelta(minutes=dur)
            except (ValueError, TypeError):
                terminada = True
            if fin is not None:
                terminada = (ahora - fin) >= timedelta(hours=cfg_nps.get('horas_despues_atencion', 3))
            if not terminada:
                continue  # muy pronto -- reintenta en el proximo barrido, sin marcar visto

            if not dentro_ventana:
                continue  # fuera de la ventana -- reintenta (mañana la toma como 'ayer')

            rut = dentidesk.limpiar_rut(str(c.get('PatientDocument') or ''))

            bloqueo = nps.evaluar(rut, es_hito, cfg_nps)
            if bloqueo:
                # evaluado y bloqueado (ej. cooldown) -- no reintentar esta atencion
                nps.marcar_visto(ida, target.isoformat())
                continue

            if enviados >= max_envios:
                print(f'[nps] tope de {max_envios} envios/dia alcanzado, '
                      f'se retoma en el proximo barrido')
                tope_alcanzado = True
                break

            nombres, _ = _pac._split_nombre(c.get('PatientName') or '')
            cita = {
                'nombre': nombres or 'paciente',
                'telefono': telefono,
                'doctor_nombre': (c.get('ProfessionalName') or '').strip(),
                'id_agenda': ida,
                'fecha': target.isoformat(),
                # 'hoy' si la atencion es de hoy, 'ayer' si el envio cae al dia
                # siguiente (atencion de la tarde pasada la ventana) -> {{2}}.
                'cuando': 'hoy' if target == hoy else 'ayer',
            }
            r = notify.enviar_nps(cita)
            if r.get('ok'):
                nps.registrar_envio(rut, ida, cita['doctor_nombre'])
                nps.marcar_visto(ida, target.isoformat())
                enviados += 1
            else:
                print('[nps] fallo al enviar a', rut, r.get('error'))
                # no se marca visto -- reintenta en el proximo barrido

    return {'ok': True, 'enviados': enviados}


def _loop_nps():
    """Encuestas de satisfaccion: a diferencia de control dental/recordatorios
    (un disparo diario), este loop corre VARIAS VECES AL DIA dentro de la
    ventana horaria configurada (una cita recien atendida a las 11:20 no
    tiene por que esperar hasta mañana para calificar). Se espacia con
    'ultima_corrida' (>= 30 min entre corridas) para no barrer la agenda en
    cada poll de 40s."""
    import time
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo('America/Santiago')
    except Exception:
        tz = None
    ultima_corrida = None
    while True:
        try:
            ahora = datetime.now(tz) if tz else datetime.now()
            slot = ahora.strftime('%H:%M')
            cfg_nps = nps.load_config()
            cfg_dd = scheduling.load_config()
            if (cfg_nps.get('activo')
                    and cfg_dd['dentidesk']['enabled']
                    and nps.dentro_de_ventana(cfg_nps, slot)
                    and (ultima_corrida is None
                         or (ahora - ultima_corrida) >= timedelta(minutes=30))):
                ultima_corrida = ahora
                r = _procesar_nps(cfg_nps, cfg_dd, ahora)
                print('[nps]', slot, r)
        except Exception as e:
            print('[nps] error:', e)
        time.sleep(40)


def _loop_calentador():
    """Mantiene tibio el cache de disponibilidad: cada ~20 min refresca los
    slots libres de cada doctor para los proximos ~15 dias habiles (mas la
    agenda de cada dia, compartida). Espaciado ~0.5s entre llamadas para no
    saturar DentiDesk. Con esto, la primera consulta de cualquier paciente
    sale del cache (~0.2s) en vez de pagar el frio (~4-15s). La primera pasada
    corre apenas arranca el servicio (tras 20s de gracia para el boot)."""
    import time
    time.sleep(20)
    while True:
        try:
            cfg_dd = scheduling.load_config()
            if cfg_dd['dentidesk']['enabled']:
                # 21 dias habiles (~1 mes): cubre tambien a los doctores con pocos
                # dias, cuyo escaneo con min_dias llega mas alla del dia 15.
                dias = scheduling.dias_habiles_ventana(fechas.hoy_chile(), cfg_dd)[:21]
                docs = [k for k, v in cfg_dd['doctores'].items()
                        if not k.startswith('_') and isinstance(v, dict)]
                t0 = datetime.now()
                for d in dias:
                    try:
                        dentidesk._get_agenda_day(cfg_dd, d, force=True)
                    except Exception:
                        pass
                    time.sleep(0.5)
                    for doc in docs:
                        try:
                            _slots15_dia(doc, d, cfg_dd, force=True)
                        except Exception:
                            pass
                        time.sleep(0.5)
                print(f'[calentador] pasada completa: {len(dias)} dias x {len(docs)} doctores '
                      f'en {(datetime.now() - t0).seconds}s')
        except Exception as e:
            print('[calentador] error:', e)
        time.sleep(20 * 60)

def _loop_recurrentes():
    """Barrido diario de cargos recurrentes (compras.suscripciones): a las 09:00 hora
    Chile revisa cuáles ya llegaron a su día de cobro este mes y genera la compra sola
    (compras.generar_recurrentes_pendientes ya evita duplicar: una vez por mes por
    suscripción). Independiente de DentiDesk — corre siempre que el scheduler esté
    activo. Mismo esqueleto que _loop_confirmaciones (poll cada 60s, un disparo/día)."""
    import time
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo('America/Santiago')
    except Exception:
        tz = None
    ya_corrio = None
    while True:
        try:
            ahora = datetime.now(tz) if tz else datetime.now()
            if ahora.strftime('%H:%M') == '09:00' and ya_corrio != ahora.date():
                ya_corrio = ahora.date()
                import compras as _c
                gen = _c.generar_recurrentes_pendientes()
                if gen:
                    print('[recurrentes]', len(gen), 'cargo(s) generado(s):', gen)
        except Exception as e:
            print('[recurrentes] error:', e)
        time.sleep(60)

def _iniciar_scheduler():
    """Arranca el refresco de pacientes + el barrido de confirmaciones en segundo
    plano. Activo en Render (o si se define RUN_PATIENT_SYNC=true). En local no
    corre salvo que se pida explicitamente."""
    global _SCHEDULER_INICIADO
    if _SCHEDULER_INICIADO:
        return
    activar = bool(os.environ.get('RENDER')) or \
        os.environ.get('RUN_PATIENT_SYNC', '').strip().lower() in ('1', 'true', 'yes', 'on')
    if not activar:
        return
    import threading
    _SCHEDULER_INICIADO = True
    threading.Thread(target=_loop_refresco_pacientes, daemon=True).start()
    threading.Thread(target=_loop_confirmaciones, daemon=True).start()
    threading.Thread(target=_loop_recordatorios, daemon=True).start()
    threading.Thread(target=_loop_recaptacion_programados, daemon=True).start()
    threading.Thread(target=_loop_calentador, daemon=True).start()
    threading.Thread(target=_loop_recurrentes, daemon=True).start()
    threading.Thread(target=_loop_control_dental, daemon=True).start()
    threading.Thread(target=_loop_nps, daemon=True).start()
    print('[refresco pacientes] scheduler iniciado (cada 12h)')
    print('[recordatorios] scheduler iniciado (semana/dia/inasistencia, horas configurables en el panel)')
    print('[recaptacion-programados] scheduler iniciado (hora configurable en el panel)')
    print('[confirmaciones] scheduler iniciado (11:00, 13:30, 17:00, 19:45)')
    print('[calentador] scheduler iniciado (disponibilidad, cada 20 min)')
    print('[recurrentes] scheduler iniciado (barrido diario 09:00, cargos recurrentes)')
    print('[nps] scheduler iniciado (encuestas de satisfaccion, ventana configurable en el panel)')

_iniciar_scheduler()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '5001'))
    print("\nPanel de administracion iniciado")
    print(f"Abre tu navegador en: http://localhost:{port}\n")
    app.run(port=port, debug=False)
