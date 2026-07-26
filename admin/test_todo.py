"""
test_todo.py - Corre TODAS las pruebas del proyecto de una vez.

    cd admin && python test_todo.py

Cero red, cero correo, cero WhatsApp, cero DentiDesk: cada suite intercepta lo
que corresponda y escribe en archivos temporales. Se puede correr en cualquier
momento, incluso con el backend de produccion andando.

Cada archivo se ejecuta en un PROCESO APARTE a proposito: las suites fijan sus
variables de entorno (PATIENT_INDEX_PATH, COMPRAS_DB_PATH, ...) ANTES de
importar los modulos, asi que compartir intérprete las haria pisarse entre si.
"""

import sys
import subprocess
from pathlib import Path

SUITES = [
    ('fechas',      'test_fechas.py',      'hora de Chile vs. el reloj UTC de Render'),
    ('jsonstore',   'test_jsonstore.py',   'el guardado de datos que usan todos'),
    ('seguridad',   'test_seguridad.py',   'endpoints cerrados y saneo de subidas'),
    ('stats',       'test_stats.py',       'el registro de reservas no pierde datos'),
    ('cumpleanos',  'test_cumpleanos.py',  'importacion de fechas de nacimiento'),
    ('webhook_wa',  'test_webhook_wa.py',  'el webhook que cancela citas reales'),
    ('avisos',      'test_avisos.py',      'guardas de recaptacion / control dental / NPS'),
    ('compras',     'test_compras.py',     'recurrentes, stock y migraciones'),
]

AQUI = Path(__file__).parent


def main():
    fallaron, total = [], 0
    for nombre, archivo, descripcion in SUITES:
        r = subprocess.run([sys.executable, archivo], cwd=str(AQUI),
                           capture_output=True, text=True)
        salida = (r.stdout or '') + (r.stderr or '')
        corridas = 0
        for linea in salida.splitlines():
            if linea.startswith('Ran ') and ' test' in linea:
                try:
                    corridas = int(linea.split()[1])
                except (ValueError, IndexError):
                    pass
        total += corridas
        if r.returncode == 0:
            print(f'  OK    {nombre:<12} {corridas:>3} pruebas  — {descripcion}')
        else:
            fallaron.append((nombre, salida))
            print(f'  FALLA {nombre:<12} {corridas:>3} pruebas  — {descripcion}')

    print('-' * 72)
    if fallaron:
        for nombre, salida in fallaron:
            print(f'\n===== detalle de {nombre} =====\n{salida}')
        print(f'{len(fallaron)} suite(s) con fallas, {total} pruebas corridas')
        return 1
    print(f'TODO OK — {total} pruebas en {len(SUITES)} suites')
    return 0


if __name__ == '__main__':
    sys.exit(main())
