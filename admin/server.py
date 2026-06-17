"""
Panel de Administración — Ortodoncia Richard
Ejecutar: python admin/server.py
Abrir: http://localhost:5001
"""

import os
import re
import json
import shutil
import subprocess
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from bs4 import BeautifulSoup

app = Flask(__name__, static_folder='.')

# CORS: permitir el sitio en GitHub Pages, dominio propio y desarrollo local.
_ALLOWED_ORIGINS = {
    'https://delreal90.github.io',
    'https://ortodonciarichard.cl',
    'https://www.ortodonciarichard.cl',
    'http://localhost:3000',
    'http://127.0.0.1:3000',
}
@app.after_request
def _cors(resp):
    origin = request.headers.get('Origin', '')
    if origin in _ALLOWED_ORIGINS:
        resp.headers['Access-Control-Allow-Origin'] = origin
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
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
                    'doctores': doctores, 'mock': not cfg['dentidesk']['enabled']})

@app.route('/api/agenda/paciente', methods=['GET'])
def agenda_paciente():
    """Valida el RUT y lo cruza con DentiDesk. Devuelve si existe + datos precargados."""
    rut = request.args.get('rut', '')
    if not scheduling.rut_valido(rut):
        return jsonify({'ok': False, 'error': 'RUT invalido'}), 400
    info = dentidesk.buscar_paciente(rut)
    return jsonify({'ok': True, 'rut': scheduling.formatear_rut(rut),
                    'existe': info['existe'], 'datos': info['datos']})

# Cache corto de disponibilidad por dia: evita re-consultar DentiDesk si el
# paciente navega adelante/atras. Clave (doctor, motivo, fecha) -> (ts, horas).
_DISPO_CACHE = {}
_DISPO_TTL = 90  # segundos

def _horas_de_dia(doctor, motivo, d, cfg):
    import time as _t
    key = (doctor, motivo, d.isoformat())
    hit = _DISPO_CACHE.get(key)
    if hit and (_t.time() - hit[0]) < _DISPO_TTL:
        return hit[1]
    ocupadas = dentidesk.horas_ocupadas(doctor, d, motivo, cfg)
    horas = scheduling.horas_disponibles(doctor, d, motivo, ocupadas, cfg)
    _DISPO_CACHE[key] = (_t.time(), horas)
    return horas

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
    habiles = scheduling.dias_habiles_desde(hoy, cfg['reglas']['dias_a_mostrar'], cfg)

    def trabajo(d):
        try:
            return d, _horas_de_dia(doctor, motivo, d, cfg)
        except Exception:
            return d, []

    dias = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for d, horas in sorted(pool.map(trabajo, habiles), key=lambda x: x[0]):
            if horas:
                dias.append({'fecha': d.isoformat(), 'legible': _fecha_legible(d), 'horas': horas})
    return jsonify({'ok': True, 'dias': dias})

@app.route('/api/agenda/reservar', methods=['POST'])
def agenda_reservar():
    """Crea la cita en DentiDesk y dispara la confirmacion (WhatsApp / email)."""
    data = request.json or {}
    cfg = scheduling.load_config()
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

    # Revalidar en backend: anticipacion + que la hora siga disponible
    if not scheduling.cumple_anticipacion(fecha, hora, motivo_cfg, cfg):
        return jsonify({'ok': False, 'error': 'La hora no cumple la anticipacion minima'}), 409
    ocupadas = dentidesk.horas_ocupadas(doctor, fecha, motivo, cfg)
    if hora not in scheduling.horas_disponibles(doctor, fecha, motivo, ocupadas, cfg):
        return jsonify({'ok': False, 'error': 'La hora ya no esta disponible'}), 409

    # Nombres/apellidos vienen separados; si no, se parte 'nombre'
    nombre = (data.get('nombres') or '').strip()
    apellido = (data.get('apellidos') or '').strip()
    if not nombre:
        partes = (data.get('nombre') or '').strip().split(' ', 1)
        nombre, apellido = partes[0], (partes[1] if len(partes) > 1 else '')

    res = dentidesk.crear_cita(
        doc_id=doctor, motivo_key=motivo, target_date=fecha, hora=hora,
        nombre=nombre, apellido=apellido,
        email=data.get('email', ''), telefono=data.get('telefono', ''),
        rut=scheduling.limpiar_rut(rut), cfg=cfg,
    )
    if not res.get('ok'):
        return jsonify({'ok': False, 'error': 'No se pudo crear la cita'}), 502

    doctors = read_doctor_data()
    doctor_nombre = doctors.get(doctor, {}).get('name', doctor.title())
    confirm = notify.enviar_confirmacion({
        'nombre': nombre, 'telefono': data.get('telefono', ''),
        'email': data.get('email', ''), 'fecha': fecha,
        'fecha_legible': _fecha_legible(fecha), 'hora': hora,
        'doctor_nombre': doctor_nombre, 'motivo_label': motivo_cfg['label'],
        'dur_min': motivo_cfg['duracion_min'],
    }, cfg)

    return jsonify({'ok': True, 'id_cita': res.get('id_cita'),
                    'confirmacion': confirm, 'mock': res.get('mock', False)})

@app.route('/api/scheduling-config', methods=['GET'])
def get_scheduling_config():
    """Para el panel admin: devuelve doctores + sus % de ocupacion por franja."""
    cfg = scheduling.load_config()
    doctores = {k: v for k, v in cfg['doctores'].items()
                if not k.startswith('_') and isinstance(v, dict)}
    return jsonify({
        'doctores': doctores,
        'reglas': cfg['reglas'],
        'dentidesk_enabled': cfg['dentidesk']['enabled'],
    })

@app.route('/api/scheduling-config', methods=['POST'])
def set_scheduling_config():
    """Guarda cambios de % de ocupacion por doctor/franja (sin tocar codigo)."""
    data = request.json or {}
    cfg = scheduling.load_config()
    for doc_id, doc_changes in (data.get('doctores') or {}).items():
        if doc_id not in cfg['doctores']:
            continue
        if 'atiende' in doc_changes:
            cfg['doctores'][doc_id]['atiende'] = bool(doc_changes['atiende'])
        for franja, rango in (doc_changes.get('ocupacion') or {}).items():
            if franja in cfg['doctores'][doc_id]['ocupacion']:
                cfg['doctores'][doc_id]['ocupacion'][franja] = {
                    'min': int(rango['min']), 'max': int(rango['max'])
                }
    scheduling.save_config(cfg)
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

if __name__ == '__main__':
    print("\nPanel de administracion iniciado")
    print("Abre tu navegador en: http://localhost:5001\n")
    app.run(port=5001, debug=False)
