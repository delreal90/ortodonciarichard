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
BASE = Path(__file__).parent.parent  # carpeta ortodonciarichard/
INDEX = BASE / 'index.html'
IMAGES = BASE / 'images'
MAINJS = BASE / 'js' / 'main.js'

# ── Utilidades ─────────────────────────────────────────────────────────────

def read_html():
    return BeautifulSoup(INDEX.read_text(encoding='utf-8'), 'html.parser')

def write_html(soup):
    INDEX.write_text(str(soup), encoding='utf-8')

# ── Rutas estáticas ─────────────────────────────────────────────────────────

@app.route('/')
def panel():
    return send_from_directory('.', 'panel.html')

@app.route('/images/<path:filename>')
def serve_image(filename):
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
                img = photo_div.find('img') if photo_div else None
                if img:
                    img['src'] = data['foto_nueva']
                    img['alt'] = data.get('nombre_nuevo', data['nombre_actual'])
            break

    write_html(soup)
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

    new_card = BeautifulSoup(f'''
    <div class="team-card reveal">
        {photo_div}
        <div class="team-info">
            <h3>{data["nombre"]}</h3>
            <p class="team-role">{data.get("rol", TAB_ROLES.get(data["tab"], ""))}</p>
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

if __name__ == '__main__':
    print("\nPanel de administracion iniciado")
    print("Abre tu navegador en: http://localhost:5001\n")
    app.run(port=5001, debug=False)
