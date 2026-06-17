"""
actualizar_pacientes.py — Refresca la base local de pacientes (2x/dia).

Barre la agenda de DentiDesk (getAgendaDay) y agrega/actualiza pacientes nuevos
en patient_index.json. Pensado para correr de forma programada.

USO MANUAL:
    python admin/actualizar_pacientes.py

PROGRAMAR 2x/DIA (Windows, en el PC que tenga las credenciales):
    Programador de tareas -> Crear tarea basica -> Diaria, repetir cada 12h ->
    Accion: Iniciar programa -> python  -> argumento: la ruta de este archivo.

SIEMBRA INICIAL desde el Excel del panel (una sola vez):
    python admin/actualizar_pacientes.py --excel "Listado de Pacientes Totales ....xlsx"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import scheduling
import pacientes


def main():
    cfg = scheduling.load_config()
    if not cfg['dentidesk']['enabled']:
        print('⚠ Modo demo (sin credenciales DentiDesk). Define las credenciales en '
              'scheduling_secrets.json o variables de entorno y enabled=true.')
        return

    # Siembra desde Excel si se pasa --excel
    if '--excel' in sys.argv:
        ruta = sys.argv[sys.argv.index('--excel') + 1]
        print(f'Importando export: {ruta} ...')
        res = pacientes.importar_export_excel(ruta)
        print(f'  Export importado: total={res["total"]} nuevos={res["nuevos"]}')

    print('Barriendo agenda DentiDesk para actualizar pacientes...')
    res = pacientes.construir_desde_agenda(cfg, dias_atras=180, dias_adelante=120)
    print(f'  Listo: total={res["total"]} nuevos={res["nuevos"]} (dias barridos={res["dias"]})')


if __name__ == '__main__':
    main()
