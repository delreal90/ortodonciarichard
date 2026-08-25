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
    ('consentimientos', 'test_consentimientos.py', 'no duplicar consentimientos + aviso del dia'),
    ('compras',     'test_compras.py',     'recurrentes, stock y migraciones'),
    ('dolar',       'test_dolar.py',       'dolar observado en compras USD: fin de semana y API caida'),
    ('fichas',      'test_fichas.py',      'ficha primera consulta (Google Form) -> base'),
    ('seguimiento_pc', 'test_seguimiento_pc.py', 'seguimiento de primeras consultas que no avanzaron'),
    ('reactivacion', 'test_reactivacion.py', 'reactivacion de inactivos (terminados / abandonados)'),
    ('reporte_semanal', 'test_reporte_semanal.py', 'reporte semanal de KPIs de negocio'),
    ('backup',      'test_backup.py',      'respaldo de datos a Google Drive'),
    ('paciente_estado', 'test_paciente_estado.py', 'en que esta el paciente -> menu de motivos filtrado'),
    ('link_agenda', 'test_link_agenda.py', 'links de agenda pre-cargados desde el F2'),
    ('paciente_estado_api', 'test_paciente_estado_api.py', 'endpoints del menu filtrado (agenda + F2)'),
    ('link_agenda_api', 'test_link_agenda_api.py', 'endpoints del link pre-cargado (F2 + paciente)'),
    ('reagenda_diag', 'test_reagenda_diagnostico.py', 'el link de reagendar: motivo/doctor y por que falla'),
    ('reagenda_pend', 'test_reagenda_pendientes.py', 'el aviso a recepcion espera y no sale si ya agendo'),
    ('seguros',     'test_seguros.py',     'el prellenado nuevo: copia la glosa / agrupa por patron / renombre por aseguradora'),
    ('link_aseguradora', 'test_link_aseguradora.py', 'link para que el paciente actualice su aseguradora'),
    ('seguros_api',  'test_seguros_api.py',  'el interruptor central del auto-envio (panel, no cada F2)'),
    ('psq',          'test_psq.py',          'cuestionario de sueño: puntaje, corte y destinatario (doctor/recepcion)'),
    ('transversal',  'test_transversal.py',  'percentiles de Bishara: curva continua, sin inventar fuera de rango'),
    ('stopbang',     'test_stopbang.py',     'STOP-BANG: umbrales y el puntaje incompleto es un piso'),
    ('fairest',      'test_fairest.py',      'FAIREST 6/6+4: el item 6 con el P15 y las frases prohibidas'),
    ('informe_pc',   'test_informe_pc.py',   'informe de evaluacion: documento, seguimiento, oclusion y limites'),
    ('tamizaje_link','test_tamizaje_link.py','QR del cuestionario de sueno: token, que instrumento toca y el borrador'),
    ('kpi',          'test_kpi.py',          'datamart de KPIs: destino de la primera consulta, fugas y ocupacion'),
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
