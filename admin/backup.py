"""
backup.py - Respaldo / punto de restauracion de los DATOS del backend.

El codigo ya esta a salvo en GitHub (historial completo, roll-back a cualquier
commit; Render redespliega desde ahi). Lo que NO esta versionado son los DATOS:
todos los registros/config que viven en el disco persistente de Render (junto a
PATIENT_INDEX_PATH) porque son datos personales y el repo es PUBLICO (ver
.gitignore). Si ese disco se borrara o se eliminara el servicio, se perderian.

Este modulo junta esos datos en un .zip con fecha y lo sube a una carpeta de la
Unidad compartida de Google Drive, reusando la MISMA cuenta de servicio que ya
respalda los consentimientos (drive_backup.py). Rota: conserva los ultimos N.

NO respalda los directorios de media grandes (PDFs de consentimientos ya
firmados -> ya se suben uno a uno a Drive; fotos de facturas de compras;
seguros_generados) para mantener el zip liviano. Respalda lo IRREEMPLAZABLE y
estructurado: los .json/.jsonl de registro, la base SQLite de compras y las
firmas de los doctores (seguros_firmas/).

Sin credenciales de Drive configuradas, respaldar() no revienta: crea el zip
igual y devuelve el detalle con el error de subida (para que se vea en el panel/
log), mismo criterio que drive_backup.subir_pdf.

Config (env vars):
  BACKUP_DRIVE_FOLDER_ID  — carpeta de Drive donde dejar los zips (default: la
                            misma DRIVE_FOLDER_ID de consentimientos).
  BACKUP_RETENER          — cuantos respaldos conservar en Drive (default 30).
"""

import os
import zipfile
import threading
from pathlib import Path

import fechas       # ahora_chile(): Render corre en UTC. Ver fechas.py.
import jsonstore    # guardado atomico con lock. Ver jsonstore.py.
import drive_backup

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                 Path(__file__).parent / 'patient_index.json')).parent
REGISTRO_PATH = Path(os.environ.get('BACKUP_REGISTRO_PATH', _BASE_DIR / 'backup_registro.json'))

_LOCK = threading.Lock()

# Prefijo del nombre del zip -> tambien es la marca por la que se listan/rotan en Drive.
_PREFIJO = 'backup_ortodoncia_'

# Nombres que NUNCA van al zip (el propio registro del backup y los temporales).
_EXCLUIR = {'backup_registro.json'}

_STORE = jsonstore.JsonStore(REGISTRO_PATH, indent=2,
                             default={'ultimo': {}, 'historial': []},
                             claves={'ultimo': {}, 'historial': []})


def _retener():
    try:
        n = int(os.environ.get('BACKUP_RETENER', '30'))
        return max(1, n)
    except (TypeError, ValueError):
        return 30


def _folder_id():
    return os.environ.get('BACKUP_DRIVE_FOLDER_ID') or drive_backup._folder_id()


def archivos_a_respaldar(base_dir=None):
    """Lista de (ruta_absoluta, arcname) a incluir en el zip. arcname es relativo
    a base_dir para que la restauracion los deje de vuelta en su lugar."""
    base = Path(base_dir or _BASE_DIR)
    out = []
    if not base.exists():
        return out
    # Registros/config y datos de runtime (top-level).
    for patron in ('*.json', '*.jsonl'):
        for p in sorted(base.glob(patron)):
            if p.name in _EXCLUIR or p.name.endswith('.tmp'):
                continue
            out.append((p, p.name))
    # Base SQLite de compras (+ WAL/SHM si estan).
    for nombre in ('compras.db', 'compras.db-wal', 'compras.db-shm'):
        p = base / nombre
        if p.exists():
            out.append((p, nombre))
    # Firmas de los doctores (imagenes chicas, irreemplazables).
    firmas = base / 'seguros_firmas'
    if firmas.is_dir():
        for p in sorted(firmas.rglob('*')):
            if p.is_file():
                out.append((p, str(p.relative_to(base)).replace('\\', '/')))
    return out


def crear_zip(destino_dir=None, base_dir=None, nombre=None):
    """Arma el zip en destino_dir (default: base_dir). Devuelve
    {'ruta', 'nombre', 'archivos': [arcname...], 'tamano'}."""
    base = Path(base_dir or _BASE_DIR)
    destino = Path(destino_dir or base)
    destino.mkdir(parents=True, exist_ok=True)
    if not nombre:
        sello = fechas.ahora_chile().strftime('%Y-%m-%d_%H%M')
        nombre = f'{_PREFIJO}{sello}.zip'
    ruta = destino / nombre
    archivos = archivos_a_respaldar(base)
    with zipfile.ZipFile(ruta, 'w', zipfile.ZIP_DEFLATED) as z:
        for origen, arcname in archivos:
            z.write(origen, arcname)
    return {'ruta': str(ruta), 'nombre': nombre,
            'archivos': [a for _, a in archivos], 'tamano': ruta.stat().st_size}


def _rotar_en_drive(retener=None):
    """Deja solo los 'retener' respaldos mas nuevos en la carpeta de Drive
    (borra los mas viejos por nombre, que empieza con la fecha). Devuelve
    cuantos elimino. Best-effort: si Drive falla, no rompe el respaldo."""
    retener = retener or _retener()
    listado = drive_backup.listar_archivos(_folder_id(), prefijo=_PREFIJO)
    if not listado.get('ok'):
        return 0
    zips = sorted((a for a in listado['archivos'] if a.get('name', '').startswith(_PREFIJO)),
                  key=lambda a: a.get('name', ''))
    sobran = zips[:-retener] if len(zips) > retener else []
    eliminados = 0
    for a in sobran:
        if drive_backup.eliminar_archivo(a['id']).get('ok'):
            eliminados += 1
    return eliminados


def respaldar(subir=True, base_dir=None):
    """Crea el zip, lo sube a Drive, rota los viejos, registra y limpia el zip
    local. Devuelve el detalle. NUNCA revienta: cualquier fallo (armado del zip,
    Drive, disco) queda capturado en 'error' -- un respaldo que falla no debe
    tumbar el endpoint ni el scheduler, solo dejar constancia para revisarlo."""
    import traceback
    resultado = {
        'ok': False, 'nombre': '', 'archivos': 0, 'tamano': 0,
        'fecha': fechas.ahora_chile().isoformat(timespec='seconds'),
        'subido': False, 'file_id': '', 'eliminados_rotacion': 0, 'error': '',
    }
    ruta_zip = None
    try:
        info = crear_zip(base_dir=base_dir)
        ruta_zip = info['ruta']
        resultado.update({'nombre': info['nombre'], 'archivos': len(info['archivos']),
                          'tamano': info['tamano']})
        if subir:
            r = drive_backup.subir_archivo(info['ruta'], info['nombre'],
                                           mimetype='application/zip', folder_id=_folder_id())
            resultado['subido'] = bool(r.get('ok'))
            resultado['file_id'] = r.get('file_id', '')
            if r.get('ok'):
                resultado['ok'] = True
                try:
                    resultado['eliminados_rotacion'] = _rotar_en_drive()
                except Exception as e:   # la rotacion no debe invalidar un respaldo OK
                    resultado['error'] = f'respaldo OK, rotacion fallo: {e}'
            else:
                resultado['error'] = r.get('error', 'error de subida')
        else:
            resultado['ok'] = True
    except Exception:
        resultado['error'] = traceback.format_exc()[-800:]
    finally:
        # El zip local se sube y se borra: en Render el disco es chico y el
        # respaldo ya vive en Drive. Si algo fallo, igual se borra (el proximo
        # intento arma uno nuevo) para no llenar el disco.
        if ruta_zip:
            try:
                Path(ruta_zip).unlink(missing_ok=True)
            except OSError:
                pass

    try:
        with _LOCK:
            reg = _STORE.load()
            reg['ultimo'] = resultado
            reg.setdefault('historial', []).append(resultado)
            reg['historial'] = reg['historial'][-60:]   # no crecer sin techo
            _STORE.save(reg)
    except Exception:
        pass
    return resultado


def estado():
    """Ultimo respaldo + un poco de historial, para el panel/diagnostico."""
    reg = _STORE.load()
    return {'ultimo': reg.get('ultimo') or {}, 'historial': (reg.get('historial') or [])[-10:]}
