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
DEFAULT_FOLDER_ID = '0AKiV1nLsqi2dUk9PVA'  # raíz de la Unidad compartida "Consentimientos Ortodoncia Richard"
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


def subir_pdf(ruta_local, nombre_archivo=None):
    """Sube el PDF a la carpeta de Drive. Devuelve {'ok': bool, 'file_id'?, 'error'?}."""
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
            'parents': [_folder_id()],
        }
        media = MediaFileUpload(str(ruta_local), mimetype='application/pdf', resumable=False)
        archivo = service.files().create(
            body=metadata, media_body=media, fields='id', supportsAllDrives=True
        ).execute()
        return {'ok': True, 'file_id': archivo.get('id')}
    except Exception as e:
        log.error('Error subiendo a Drive: %s', e)
        return {'ok': False, 'error': str(e)}
