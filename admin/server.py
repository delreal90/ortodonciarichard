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
import shutil
import subprocess
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
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
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Admin-Token, X-Kiosk-Token'
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
RUTAS_SOLO_LOCAL = {'/api/info', '/api/equipo', '/api/casos', '/api/faq',
                    '/api/doctores', '/api/equipo/agregar', '/api/equipo/eliminar',
                    '/api/publicar', '/api/scheduling-config'}

@app.before_request
def bloquear_admin_en_produccion():
    if EN_RENDER and request.path in RUTAS_SOLO_LOCAL:
        return jsonify({'error': 'No disponible en producción'}), 403

# ── Utilidades ─────────────────────────────────────────────────────────────

def read_html():
    return BeautifulSoup(INDEX.read_text(encoding='utf-8'), 'html.parser')

def write_html(soup):
    INDEX.write_text(str(soup), encoding='utf-8')

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
    dest = IMAGES / target
    f.save(str(dest))
    return jsonify({'ok': True, 'path': f'images/{target}'})

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

import scheduling
import dentidesk
import notify
import wa_cloud
import recordatorios_wa
import webhook_wa
from datetime import date, datetime

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
                'especialidad': v.get('especialidad', '')}
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

# Cache de disponibilidad por dia. Clave (doctor, motivo, fecha) -> (ts, horas).
# Estrategia "stale-while-revalidate": si el cache esta algo viejo (> TTL) pero no
# demasiado (< MAX_STALE), se devuelve al instante y se refresca en segundo plano.
# Asi el paciente casi nunca espera los ~3s de una consulta en frio a DentiDesk.
# (La reserva SIEMPRE valida contra datos frescos, no contra este cache.)
_DISPO_CACHE = {}
_DISPO_TTL = 300          # 5 min: las horas a futuro cambian lento
_DISPO_MAX_STALE = 1800   # 30 min: mas alla de esto NO servir viejo (traer sincrono)
_DISPO_INFLIGHT = set()
_DISPO_LOCK = _threading.Lock()

# Límite GLOBAL de consultas simultáneas a DentiDesk. Sin esto, varios pacientes
# (o la precarga) disparan decenas de llamadas a la vez y la instancia 0.5 CPU +
# DentiDesk se saturan: cada request frío salta de ~6s a ~16s. Con el tope, las
# llamadas se ordenan y ninguna se ahoga.
_DENTI_SEM = _threading.BoundedSemaphore(10)

def _fetch_horas(doctor, motivo, d, cfg):
    with _DENTI_SEM:
        libres, ocupados = dentidesk.disponibilidad_real(doctor, d, motivo, cfg)
    return scheduling.horas_disponibles(doctor, d, motivo, libres, ocupados, cfg)

def _refrescar_async(doctor, motivo, d, cfg, key):
    """Refresca una entrada del cache en segundo plano (evita duplicar trabajo)."""
    import time as _t
    def job():
        try:
            _DISPO_CACHE[key] = (_t.time(), _fetch_horas(doctor, motivo, d, cfg))
        except Exception:
            pass
        finally:
            _DISPO_INFLIGHT.discard(key)
    _threading.Thread(target=job, daemon=True).start()

def _horas_de_dia(doctor, motivo, d, cfg):
    import time as _t
    key = (doctor, motivo, d.isoformat())
    hit = _DISPO_CACHE.get(key)
    if hit and (_t.time() - hit[0]) < _DISPO_MAX_STALE:
        # Cache utilizable. Si paso el TTL, refrescar en segundo plano (sin esperar).
        if (_t.time() - hit[0]) >= _DISPO_TTL:
            with _DISPO_LOCK:
                if key not in _DISPO_INFLIGHT:
                    _DISPO_INFLIGHT.add(key)
                    _refrescar_async(doctor, motivo, d, cfg, key)
        return hit[1]
    # Sin cache (o demasiado viejo): traer sincrono.
    horas = _fetch_horas(doctor, motivo, d, cfg)
    _DISPO_CACHE[key] = (_t.time(), horas)
    return horas

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

    hoy = date.today()
    todos = scheduling.dias_habiles_ventana(hoy, cfg)
    # Paginacion: se cargan de a PAGE dias habiles (evita decenas de llamadas
    # a DentiDesk de una sola vez). Pagina chica = carga inicial mas rapida; el
    # frontend pide mas con 'offset'.
    PAGE = 6
    offset = max(0, int(request.args.get('offset', 0) or 0))
    pagina = todos[offset:offset + PAGE]

    def trabajo(d):
        try:
            return d, _horas_de_dia(doctor, motivo, d, cfg)
        except Exception:
            return d, []

    dias = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for d, horas in sorted(pool.map(trabajo, pagina), key=lambda x: x[0]):
            if horas:
                dias.append({'fecha': d.isoformat(), 'legible': _fecha_legible(d), 'horas': horas})
    return jsonify({'ok': True, 'dias': dias,
                    'offset_siguiente': offset + PAGE,
                    'hay_mas': (offset + PAGE) < len(todos)})

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
    import pacientes
    return jsonify({'ok': True, 'total': pacientes.total()})

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

    # Captcha Cloudflare Turnstile (anti-bot). Solo se valida si esta configurado
    # el secreto en el entorno; si no, se omite (no rompe antes de activarlo).
    if not _verificar_turnstile(data.get('captcha_token', '')):
        return jsonify({'ok': False, 'error': 'Verificación de seguridad fallida. Recarga e intenta de nuevo.'}), 403

    doctor = data.get('doctor'); motivo = data.get('motivo')
    if doctor not in cfg['doctores'] or motivo not in cfg['motivos']:
        return jsonify({'ok': False, 'error': 'Parametros invalidos'}), 400
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

    res = dentidesk.crear_cita(
        doc_id=doctor, motivo_key=motivo, target_date=fecha, hora=hora,
        nombre=nombre, apellido=apellido,
        email=email, telefono=telefono,
        rut=scheduling.limpiar_rut(rut), cfg=cfg,
    )
    if not res.get('ok'):
        return jsonify({'ok': False, 'error': 'No se pudo crear la cita'}), 502

    # Registrar esta cita online como "ya confirmada" para que el barrido de
    # confirmaciones (citas presenciales/telefono) no le reenvie el correo.
    try:
        import confirmaciones
        confirmaciones.marcar_enviada(res.get('id_cita'))
    except Exception:
        pass

    doctors = read_doctor_data()
    doctor_nombre = doctors.get(doctor, {}).get('name', doctor.title())

    # Registrar el agendamiento para estadisticas (sin datos personales sensibles).
    try:
        import stats
        stats.registrar({
            'fecha': fecha.isoformat(), 'hora': hora,
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

    confirm = notify.enviar_confirmacion({
        'nombre': nombre, 'telefono': telefono_nuevo or data.get('telefono', ''),
        'email': email_notif, 'fecha': fecha,
        'fecha_legible': _fecha_legible(fecha), 'hora': hora,
        'doctor_nombre': doctor_nombre, 'motivo_label': motivo_cfg['label'],
        'dur_min': motivo_cfg['duracion_min'],
    }, cfg)

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
                    'solicitud_cambio': es_no_soy_yo or es_completar})

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
                 'recordatorio_dia' | 'inasistencia_reagendar'
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

    envio = {
        'confirmacion_hora':      lambda: wa_cloud.enviar_confirmacion_hora(tel, nombre, doctor, fecha, hora),
        'recordatorio_semana':    lambda: wa_cloud.enviar_recordatorio_semana(tel, nombre, doctor, fecha, hora, id_agenda),
        'recordatorio_dia':       lambda: wa_cloud.enviar_recordatorio_dia(tel, nombre, doctor, fecha, hora, id_agenda),
        'inasistencia_reagendar': lambda: wa_cloud.enviar_inasistencia_reagendar(tel, nombre, fecha, id_agenda),
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
    """Protegido por ADMIN_TOKEN: toggles/hora de cada recordatorio + feriados."""
    if not _check_admin_token():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    return jsonify({'ok': True, 'config': recordatorios_wa.load_config()})

@app.route('/api/whatsapp/config', methods=['POST'])
def set_whatsapp_config():
    """Guarda cambios parciales (activo/hora por tipo, feriados). Toma efecto
    de inmediato -- no requiere deploy, vive en el disco persistente."""
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
    wa_cfg = recordatorios_wa.load_config()
    return jsonify({
        'ok': True,
        'semana': recordatorios_wa.enviar_recordatorios_semana(cfg),
        'dia': recordatorios_wa.enviar_recordatorios_dia(cfg, wa_cfg['feriados']),
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
    }

    # canal=None -> automatico (email, con WhatsApp de respaldo); si la
    # secretaria eligio uno en F2, se fuerza ese unico canal.
    resultado = notify.enviar_confirmacion(cita_dict, cfg, canal=canal)

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
    if canal not in ('mail', 'whatsapp', 'tablet'):
        return jsonify({'ok': False, 'error': "canal debe ser 'mail', 'whatsapp' o 'tablet'"}), 400
    if tipo not in consentimientos.TIPOS_DOCUMENTO:
        return jsonify({'ok': False, 'error': f'tipo de documento desconocido: {tipo}'}), 400

    import pacientes as _pacientes
    rec = _pacientes.lookup(rut)
    if not rec:
        return jsonify({'ok': False, 'error': 'Paciente no encontrado en la base local'}), 404

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
        'fecha':             _cap(data.get('fecha'), 40) or date.today().isoformat(),
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
                     lambda: recordatorios_wa.enviar_recordatorios_dia(cfg_dd, wa_cfg['feriados'])),
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
    print('[refresco pacientes] scheduler iniciado (cada 12h)')
    print('[recordatorios] scheduler iniciado (semana/dia/inasistencia, horas configurables en el panel)')
    print('[confirmaciones] scheduler iniciado (11:00, 13:30, 17:00, 19:45)')

_iniciar_scheduler()

if __name__ == '__main__':
    print("\nPanel de administracion iniciado")
    print("Abre tu navegador en: http://localhost:5001\n")
    app.run(port=5001, debug=False)
