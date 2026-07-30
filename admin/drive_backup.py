"""
drive_backup.py - Respaldo de consentimientos firmados en Google Drive.

Usa una cuenta de servicio de Google (Workspace) con acceso de Editor a una
carpeta compartida — no requiere OAuth interactivo, funciona sin supervisión
tanto en este PC como en Render.

Config (env vars):
  GOOGLE_SERVICE_ACCOUNT_JSON  — contenido completo del JSON de la cuenta de
                                 servicio (para producción/Render: pegar el
                                 JSON entero como valor de la env var).
  GOOGLE_SERVICE_ACCOUNT_PATH  — alternativa local: ruta al archivo JSON
                                 (default: admin/drive_service_account.json).
  DRIVE_FOLDER_ID              — ID de la carpeta de Drive compartida con la
                                 cuenta de servicio (default: la carpeta
                                 "Consentimientos Ortodoncia Richard").

Si no hay credenciales configuradas, subir_pdf() no falla: devuelve
{'ok': False, 'error': ...} para que el flujo de firma no se caiga por un
problema de Drive (el PDF ya quedó guardado localmente de todos modos).
"""

import os
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive']
# Raíz de la Unidad compartida "Consentimientos Ortodoncia Richard". No es un secreto
# (sin las credenciales de la cuenta de servicio el ID no sirve de nada), pero se puede
# sobrescribir con la env var DRIVE_FOLDER_ID para no tenerlo fijo en un repo público.
DEFAULT_FOLDER_ID = os.environ.get('DRIVE_FOLDER_ID', '0AKiV1nLsqi2dUk9PVA')
DEFAULT_CRED_PATH = Path(__file__).parent / 'drive_service_account.json'


def _credenciales():
    from google.oauth2 import service_account

    raw = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    if raw:
        info = json.loads(raw)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    path = Path(os.environ.get('GOOGLE_SERVICE_ACCOUNT_PATH', DEFAULT_CRED_PATH))
    if not path.exists():
        return None
    return service_account.Credentials.from_service_account_file(str(path), scopes=SCOPES)


def _folder_id():
    return os.environ.get('DRIVE_FOLDER_ID', DEFAULT_FOLDER_ID)


def subir_archivo(ruta_local, nombre_archivo=None, mimetype='application/octet-stream',
                  folder_id=None):
    """Sube CUALQUIER archivo a una carpeta de Drive. Devuelve
    {'ok': bool, 'file_id'?, 'error'?}. Generalizacion de subir_pdf() para que
    el respaldo (backup.py) pueda subir un .zip a su propia carpeta sin
    duplicar el manejo de credenciales / Unidad compartida."""
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    ruta_local = Path(ruta_local)
    if not ruta_local.exists():
        return {'ok': False, 'error': f'Archivo no encontrado: {ruta_local}'}

    creds = _credenciales()
    if not creds:
        return {'ok': False, 'error': 'Sin credenciales de Google Drive configuradas'}

    try:
        service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        metadata = {
            'name': nombre_archivo or ruta_local.name,
            'parents': [folder_id or _folder_id()],
        }
        media = MediaFileUpload(str(ruta_local), mimetype=mimetype, resumable=False)
        archivo = service.files().create(
            body=metadata, media_body=media, fields='id', supportsAllDrives=True
        ).execute()
        return {'ok': True, 'file_id': archivo.get('id')}
    except Exception as e:
        log.error('Error subiendo a Drive: %s', e)
        return {'ok': False, 'error': str(e)}


def subir_pdf(ruta_local, nombre_archivo=None):
    """Sube el PDF a la carpeta de Drive. Devuelve {'ok': bool, 'file_id'?, 'error'?}.
    Delega en subir_archivo() (mismo comportamiento que antes)."""
    return subir_archivo(ruta_local, nombre_archivo, mimetype='application/pdf')


def listar_archivos(folder_id=None, prefijo=None):
    """Lista archivos (id + name) de una carpeta de Drive, opcionalmente
    filtrando por prefijo de nombre. Para la rotacion de respaldos (backup.py).
    Devuelve {'ok': bool, 'archivos': [{'id','name'}], 'error'?}."""
    from googleapiclient.discovery import build

    creds = _credenciales()
    if not creds:
        return {'ok': False, 'error': 'Sin credenciales de Google Drive configuradas', 'archivos': []}
    try:
        service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        q = f"'{folder_id or _folder_id()}' in parents and trashed = false"
        if prefijo:
            q += f" and name contains '{prefijo}'"
        archivos = []
        page_token = None
        while True:
            resp = service.files().list(
                q=q, fields='nextPageToken, files(id, name)', pageSize=1000,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
                pageToken=page_token,
            ).execute()
            archivos.extend(resp.get('files', []))
            page_token = resp.get('nextPageToken')
            if not page_token:
                break
        return {'ok': True, 'archivos': archivos}
    except Exception as e:
        log.error('Error listando en Drive: %s', e)
        return {'ok': False, 'error': str(e), 'archivos': []}


def eliminar_archivo(file_id):
    """Elimina un archivo de Drive por id (rotacion de respaldos viejos).
    Devuelve {'ok': bool, 'error'?}."""
    from googleapiclient.discovery import build

    creds = _credenciales()
    if not creds:
        return {'ok': False, 'error': 'Sin credenciales de Google Drive configuradas'}
    try:
        service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
        return {'ok': True}
    except Exception as e:
        log.error('Error eliminando en Drive: %s', e)
        return {'ok': False, 'error': str(e)}
