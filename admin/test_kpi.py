"""
test_kpi.py — Pruebas del datamart de KPIs. CERO RED.

Todo corre contra una base SQLite temporal poblada con citas sintéticas: no toca
DentiDesk, no manda correo, no lee la base real. Se puede correr con producción
andando, igual que el resto de las suites del proyecto.

Lo que se prueba con más cuidado es el reparto de destinos de la primera consulta
(`destino_primeras_consultas`), porque es la métrica nueva y la que tiene los casos
borde que importan: el paciente que volvió a un control (NO es fuga), el que nunca
volvió (sí lo es), y el que consultó hace dos semanas (todavía no se puede saber).
"""

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

# La base tiene que apuntar a un archivo temporal ANTES de importar kpi, porque la
# ruta se resuelve al importar el módulo (mismo patrón que test_compras.py).
_TMP = tempfile.mkdtemp(prefix='kpi_test_')
os.environ['KPI_DB_PATH'] = os.path.join(_TMP, 'kpi_test.db')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kpi          # noqa: E402
import fechas       # noqa: E402


HOY = fechas.hoy_chile()


def _cita(id_agenda, fecha, motivo='Control Fijo', rut='111111111',
          estado='Atendido', id_status='2125', doctor='Alberto Del Real',
          duracion=15, hora='10:00:00', creada='2020-01-01 09:00:00', booked=''):
    """Una cita cruda con la forma EXACTA que devuelve getAgendaDay (campos
    verificados en vivo el 2026-08-21, incluidos IdStatus y BookedBy)."""
    return {
        'IdAgenda': str(id_agenda),
        'Date': fecha if isinstance(fecha, str) else fecha.isoformat(),
        'time': hora, 'duration': duracion,
        'ProfessionalName': doctor, 'Reason': motivo,
        'IdStatus': id_status, 'Status': estado,
        'PatientDocument': rut, 'CreateDate': creada, 'BookedBy': booked,
    }


def _dia(delta):
    return (HOY + timedelta(days=delta)).isoformat()


class BaseKpi(unittest.TestCase):
    """Base limpia por prueba: cada test parte de cero."""

    def setUp(self):
        con = kpi._conn()
        for t in ('citas', 'disponibilidad', 'ingresos', 'snapshots'):
            con.execute(f'DELETE FROM {t}')
        con.commit()
        con.close()
        self.cfg = {'doctores': {
            'alberto':  {'professional_name': 'Alberto Del Real'},
            'rodrigo':  {'professional_name': 'Rodrigo Oyonarte'},
            'octavio':  {'professional_name': 'Octavio Del Real'},
            'patricio': {'professional_name': 'Patricio Vial'},
        }}

    def guardar(self, citas):
        return kpi.guardar_citas(citas, self.cfg)


# ── Normalizadores ───────────────────────────────────────────────────────────

class TestEstados(unittest.TestCase):

    def test_id_status_manda_sobre_el_nombre(self):
        """El IdStatus numérico es exacto; el nombre puede renombrarse en DentiDesk."""
        self.assertEqual(kpi.estado_norm('2125', 'cualquier cosa'), 'atendido')
        self.assertEqual(kpi.estado_norm('25991', ''), 'no_llega')

    def test_respaldo_por_nombre_si_el_id_es_desconocido(self):
        self.assertEqual(kpi.estado_norm('99999', 'Hora Cancelada'), 'cancelada')
        self.assertEqual(kpi.estado_norm('', 'Paciente no llega'), 'no_llega')

    def test_desconocido_no_se_adivina(self):
        self.assertEqual(kpi.estado_norm('99999', 'Estado Nuevo Raro'), 'otro')

    def test_quiere_reagendar_sigue_vigente(self):
        """33579 'Pidió cambiar su hora' NO cancela la cita: sigue ocupando su bloque
        hasta que el paciente concrete la nueva (mismo criterio que el resto del
        proyecto). Si se contara como fuga, la agenda se leería vacía cuando no lo está."""
        self.assertEqual(kpi.estado_norm('33579', ''), 'quiere_reagendar')
        self.assertIn('quiere_reagendar', kpi.ESTADOS_VIGENTE)

    def test_en_sillon_cuenta_como_ocurrio(self):
        self.assertIn('en_sillon', kpi.ESTADOS_OCURRIO)


class TestDoctores(unittest.TestCase):

    def setUp(self):
        self.cfg = {'doctores': {'alberto': {'professional_name': 'Alberto Del Real'}}}

    def test_acepta_los_dos_vocabularios(self):
        """La API devuelve 'Alberto Del Real'; el export histórico 'Dr. Alberto Del
        Real'. Los dos tienen que caer en la misma key o las series se parten."""
        self.assertEqual(kpi.doc_key('Alberto Del Real', self.cfg), 'alberto')
        self.assertEqual(kpi.doc_key('Dr. Alberto Del Real', self.cfg), 'alberto')

    def test_radiologia_no_es_doctor(self):
        self.assertEqual(kpi.doc_key('S. Intraoral y Rayos COCRL', self.cfg), 'rx')

    def test_desconocido_no_se_inventa(self):
        self.assertEqual(kpi.doc_key('Dr. Fulano Perez', self.cfg), '')


class TestCategorias(unittest.TestCase):

    def test_estudio_en_los_dos_vocabularios(self):
        """'Inicio' (export histórico) y 'Registros para el Estudio Integral' (API) son
        el MISMO paso clínico. Si no caen juntos, la conversión se parte en dos series
        que no se pueden comparar."""
        for m in ('Inicio', 'Inicia Tratamiento',
                  'Registros para el Estudio Integral de Ortodoncia',
                  'Explicación Plan Tratamiento'):
            self.assertEqual(kpi.categoria_motivo(m, {}), 'estudio', m)

    def test_montaje_e_instalacion_son_inicio(self):
        self.assertEqual(kpi.categoria_motivo('Montaje Total', {}), 'inicio_fijos')
        self.assertEqual(kpi.categoria_motivo('Instalar Invisalign', {}), 'inicio_alineadores')

    def test_refinamiento_no_es_inicio_de_tratamiento(self):
        """Un refinamiento es un ajuste a mitad de camino, no un tratamiento nuevo.
        Misma convención que control_dental y que el script histórico."""
        self.assertNotIn(kpi.categoria_motivo('Instalar Refinamiento Invisalign', {}),
                         kpi.CATEGORIAS_INICIO)

    def test_segunda_consulta_no_cuenta_como_inicio(self):
        """Decisión explícita: el script histórico la contaba como 'avance', y eso
        mezcla al que arrancó tratamiento con el que volvió a que lo evaluaran."""
        self.assertEqual(kpi.categoria_motivo('Segunda Consulta', {}), 'segunda_consulta')
        self.assertNotIn('segunda_consulta', kpi.CATEGORIAS_INICIO)

    def test_motivo_vacio(self):
        self.assertEqual(kpi.categoria_motivo('', {}), '')


# ── Ingesta ──────────────────────────────────────────────────────────────────

class TestIngesta(BaseKpi):

    def test_upsert_es_idempotente(self):
        c = [_cita(1, _dia(-10))]
        self.assertEqual(self.guardar(c), 1)
        self.guardar(c)
        con = kpi._conn()
        self.assertEqual(con.execute('SELECT COUNT(*) FROM citas').fetchone()[0], 1)
        con.close()

    def test_descarta_filas_sin_id_o_sin_fecha(self):
        self.assertEqual(self.guardar([_cita('', _dia(-1)), _cita(9, '')]), 0)

    def test_el_estado_se_actualiza_al_recosechar(self):
        """El motivo por el que la cosecha vuelve a mirar 30 días hacia atrás: la
        clínica marca 'Atendido' DESPUÉS de la visita."""
        self.guardar([_cita(1, _dia(-1), estado='No confirmado', id_status='2120')])
        self.guardar([_cita(1, _dia(-1), estado='Atendido', id_status='2125')])
        con = kpi._conn()
        self.assertEqual(con.execute(
            'SELECT estado_norm FROM citas WHERE id_agenda="1"').fetchone()[0], 'atendido')
        con.close()

    def test_el_historico_no_pisa_lo_que_trajo_la_api(self):
        """La API trae estado, IdStatus y BookedBy; el export histórico no. Si el
        import del parquet pisara una fila de la API, se perderían esos campos."""
        self.guardar([_cita(1, _dia(-5), estado='Atendido', id_status='2125')])
        kpi.guardar_citas([_cita(1, _dia(-5), estado='', id_status='')],
                          self.cfg, fuente='historico')
        con = kpi._conn()
        r = con.execute('SELECT estado_norm, fuente FROM citas WHERE id_agenda="1"').fetchone()
        con.close()
        self.assertEqual(r['estado_norm'], 'atendido')
        self.assertEqual(r['fuente'], 'api')

    def test_la_api_si_pisa_al_historico(self):
        kpi.guardar_citas([_cita(1, _dia(-5), estado='', id_status='')],
                          self.cfg, fuente='historico')
        self.guardar([_cita(1, _dia(-5), estado='Atendido', id_status='2125')])
        con = kpi._conn()
        r = con.execute('SELECT estado_norm, fuente FROM citas WHERE id_agenda="1"').fetchone()
        con.close()
        self.assertEqual(r['estado_norm'], 'atendido')
        self.assertEqual(r['fuente'], 'api')

    def test_dias_habiles_excluye_fin_de_semana(self):
        """Verificado en vivo: la clínica no tiene citas los sábados. Barrerlos sería
        un 29% de llamadas de más a DentiDesk."""
        d = kpi._dias_habiles(date(2026, 8, 17), date(2026, 8, 23))
        self.assertEqual(len(d), 5)
        self.assertTrue(all(x.weekday() < 5 for x in d))

    def test_rut_se_guarda_limpio(self):
        self.guardar([_cita(1, _dia(-1), rut='12.345.678-K')])
        con = kpi._conn()
        self.assertEqual(con.execute('SELECT rut FROM citas').fetchone()[0], '12345678K')
        con.close()


class TestReclasificar(BaseKpi):

    def test_recalcula_desde_el_dato_crudo_sin_red(self):
        """La salida de emergencia del módulo: si un mapa queda corto, se corrige la
        constante y se reclasifica — sin volver a barrer 5 años de agenda."""
        self.guardar([_cita(1, _dia(-1), estado='Estado Inventado', id_status='777777')])
        con = kpi._conn()
        self.assertEqual(con.execute('SELECT estado_norm FROM citas').fetchone()[0], 'otro')
        con.close()

        kpi.ESTADO_POR_ID['777777'] = 'atendido'
        try:
            kpi.reclasificar(self.cfg)
            con = kpi._conn()
            self.assertEqual(con.execute('SELECT estado_norm FROM citas').fetchone()[0],
                             'atendido')
            con.close()
        finally:
            kpi.ESTADO_POR_ID.pop('777777', None)


# ── El KPI nuevo: destino de la primera consulta ─────────────────────────────

class TestDestinoPrimeraConsulta(BaseKpi):
    """El reparto en 3 destinos que pidió el usuario. Los casos borde son el punto:
    volver a un control NO es fuga; no volver nunca SÍ lo es."""

    def test_paciente_que_inicio_tratamiento(self):
        self.guardar([
            _cita(1, _dia(-200), 'Primera Consulta', rut='1'),
            _cita(2, _dia(-190), 'Inicio', rut='1'),
        ])
        r = kpi.destino_primeras_consultas()
        self.assertEqual(r['destinos']['inicio'], 1)
        self.assertEqual(r['destinos']['perdido'], 0)

    def test_paciente_que_volvio_a_control_NO_es_perdido(self):
        """El caso exacto que planteó el usuario: el doctor indicó controlar en vez de
        tratar. El paciente sigue en el sistema — contarlo como fuga es un error."""
        self.guardar([
            _cita(1, _dia(-200), 'Primera Consulta', rut='2'),
            _cita(2, _dia(-150), 'Control de Evolución', rut='2'),
        ])
        r = kpi.destino_primeras_consultas()
        self.assertEqual(r['destinos']['siguio'], 1)
        self.assertEqual(r['destinos']['perdido'], 0)
        self.assertEqual(r['destinos']['inicio'], 0)

    def test_paciente_que_nunca_mas_vino_ES_perdido(self):
        self.guardar([_cita(1, _dia(-200), 'Primera Consulta', rut='3')])
        r = kpi.destino_primeras_consultas()
        self.assertEqual(r['destinos']['perdido'], 1)
        self.assertEqual(r['perdidos'][0]['rut'], '3')

    def test_consulta_reciente_queda_en_ventana_no_perdida(self):
        """Llamar 'perdido' a alguien que consultó hace dos semanas infla la fuga y le
        hace perder credibilidad al panel."""
        self.guardar([_cita(1, _dia(-14), 'Primera Consulta', rut='4')])
        r = kpi.destino_primeras_consultas()
        self.assertEqual(r['destinos']['en_ventana'], 1)
        self.assertEqual(r['destinos']['perdido'], 0)

    def test_los_indeterminados_no_entran_al_denominador(self):
        """Si 'en_ventana' contara en la base, la fuga bajaría sola cada vez que hay
        consultas recientes."""
        self.guardar([
            _cita(1, _dia(-200), 'Primera Consulta', rut='5'),
            _cita(2, _dia(-14), 'Primera Consulta', rut='6'),
        ])
        r = kpi.destino_primeras_consultas()
        self.assertEqual(r['total'], 2)
        self.assertEqual(r['base_clasificada'], 1)
        self.assertEqual(r['pct']['perdido'], 100.0)

    def test_inicio_el_mismo_dia_cuenta(self):
        """109 de 535 conversiones históricas ocurrieron el mismo día."""
        self.guardar([
            _cita(1, _dia(-200), 'Primera Consulta', rut='7'),
            _cita(2, _dia(-200), 'Montaje Total', rut='7'),
        ])
        r = kpi.destino_primeras_consultas()
        self.assertEqual(r['destinos']['inicio'], 1)
        self.assertEqual(r['dias_hasta_inicio']['mediana'], 0)

    def test_una_cita_futura_evita_que_sea_perdido(self):
        """Tiene hora agendada: no se le ha perdido a nadie."""
        self.guardar([
            _cita(1, _dia(-200), 'Primera Consulta', rut='8'),
            _cita(2, _dia(+10), 'Control Fijo', rut='8', estado='No confirmado',
                  id_status='2120'),
        ])
        r = kpi.destino_primeras_consultas()
        self.assertEqual(r['destinos']['perdido'], 0)
        self.assertEqual(r['destinos']['siguio'], 1)

    def test_estudio_cancelado_no_cuenta_como_inicio(self):
        self.guardar([
            _cita(1, _dia(-200), 'Primera Consulta', rut='9'),
            _cita(2, _dia(-190), 'Inicio', rut='9', estado='Hora Cancelada',
                  id_status='2122'),
        ])
        r = kpi.destino_primeras_consultas()
        self.assertEqual(r['destinos']['inicio'], 0)
        self.assertEqual(r['destinos']['perdido'], 1)

    def test_primera_consulta_cancelada_no_entra_al_denominador(self):
        """Una consulta que nunca ocurrió no es una oportunidad perdida de conversión."""
        self.guardar([_cita(1, _dia(-200), 'Primera Consulta', rut='10',
                            estado='Hora Cancelada', id_status='2122')])
        self.assertEqual(kpi.destino_primeras_consultas()['total'], 0)

    def test_doble_agenda_el_mismo_dia_no_cuenta_dos_veces(self):
        self.guardar([
            _cita(1, _dia(-200), 'Primera Consulta', rut='11'),
            _cita(2, _dia(-200), 'Primera Consulta', rut='11'),
        ])
        self.assertEqual(kpi.destino_primeras_consultas()['total'], 1)

    def test_conversion_90d_respeta_la_ventana(self):
        """La tasa comparable con la línea base histórica (39,2%) usa ventana estricta;
        el destino 'inicio' no, porque "arrancó" no tiene fecha de vencimiento."""
        self.guardar([
            _cita(1, _dia(-400), 'Primera Consulta', rut='12'),
            _cita(2, _dia(-200), 'Inicio', rut='12'),   # 200 días después: fuera de los 90
        ])
        r = kpi.destino_primeras_consultas()
        self.assertEqual(r['destinos']['inicio'], 1)
        self.assertEqual(r['conversion_90d'], 0.0)


# ── Fugas, ocupación, cartera ────────────────────────────────────────────────

class TestFugas(BaseKpi):

    def test_tasa_de_inasistencia(self):
        """Denominador = los que se esperaban (ocurrieron + no llegaron). Las
        canceladas se avisaron antes, no son inasistencia."""
        self.guardar(
            [_cita(i, _dia(-10)) for i in range(1, 10)] +
            [_cita(10, _dia(-10), estado='Paciente no llega', id_status='25991')] +
            [_cita(11, _dia(-10), estado='Hora Cancelada', id_status='2122')]
        )
        f = kpi.fugas()
        self.assertEqual(f['no_llega'], 1)
        self.assertEqual(f['base_inasistencia'], 10)
        self.assertEqual(f['tasa_inasistencia'], 10.0)

    def test_las_citas_futuras_no_son_no_show(self):
        self.guardar([_cita(1, _dia(+5), estado='No confirmado', id_status='2120')])
        self.assertEqual(kpi.fugas()['agendadas'], 0)

    def test_horas_perdidas(self):
        self.guardar([_cita(1, _dia(-10), estado='Paciente no llega',
                            id_status='25991', duracion=120)])
        self.assertEqual(kpi.fugas()['horas_perdidas_no_llega'], 2.0)

    def test_no_ocurrio_es_robusta_al_cambio_de_etiquetado(self):
        """La métrica que sobrevive al cambio de criterio de 2023. Medido sobre los 5
        años reales: la inasistencia cayó de 2,9% a 0,2% y las cancelaciones se
        desplomaron, pero la SUMA se quedó en ~21% — lo que cambió fue que ahora casi
        todo se marca 'Re-agendado'. Dos períodos con el mismo total de fugas y
        distinto reparto tienen que dar la misma tasa_no_ocurrio."""
        antes = [_cita(1, _dia(-10), estado='Paciente no llega', id_status='25991'),
                 _cita(2, _dia(-10), estado='Hora Cancelada', id_status='2122'),
                 _cita(3, _dia(-10)), _cita(4, _dia(-10))]
        despues = [_cita(5, _dia(-10), estado='Re-agendado', id_status='2132'),
                   _cita(6, _dia(-10), estado='Re-agendado', id_status='2132'),
                   _cita(7, _dia(-10)), _cita(8, _dia(-10))]
        self.guardar(antes)
        t_antes = kpi.fugas()['tasa_no_ocurrio']
        con = kpi._conn(); con.execute('DELETE FROM citas'); con.commit(); con.close()
        self.guardar(despues)
        self.assertEqual(t_antes, kpi.fugas()['tasa_no_ocurrio'])
        # ...y la inasistencia sola NO es robusta: por eso no es el indicador principal.
        self.assertEqual(kpi.fugas()['tasa_inasistencia'], 0.0)

    def test_sin_citas_la_tasa_es_none_no_cero(self):
        """None y 0 son cosas distintas: '0% de inasistencia' es un dato, 'no hubo
        citas' no lo es."""
        self.assertIsNone(kpi.fugas()['tasa_inasistencia'])


class TestSerieMensual(BaseKpi):

    def test_trae_todos_los_campos_que_pinta_el_panel(self):
        """Regresión: `serie_mensual` calculaba tasa_no_ocurrio con una columna que no
        estaba en el SELECT y reventaba con KeyError en el endpoint. El panel lee estas
        claves por nombre, así que si falta una, se cae la pestaña entera."""
        self.guardar([
            _cita(1, _dia(-40)),
            _cita(2, _dia(-40), estado='Re-agendado', id_status='2132'),
            _cita(3, _dia(-40), motivo='Primera Consulta'),
        ])
        s = kpi.serie_mensual()
        self.assertTrue(s)
        for campo in ('mes', 'agendadas', 'atendidos', 'no_llega', 'canceladas',
                      'reagendadas', 'primeras_consultas', 'inicios', 'altas',
                      'minutos', 'dias', 'tasa_inasistencia', 'no_ocurrieron',
                      'tasa_no_ocurrio', 'horas', 'neto_cartera'):
            self.assertIn(campo, s[0], campo)

    def test_agrupa_por_mes(self):
        self.guardar([_cita(1, '2026-01-15'), _cita(2, '2026-02-10'), _cita(3, '2026-02-11')])
        s = {m['mes']: m['agendadas'] for m in kpi.serie_mensual()}
        self.assertEqual(s['2026-01'], 1)
        self.assertEqual(s['2026-02'], 2)


class TestOcupacion(BaseKpi):

    def test_horas_por_dia_trabajado(self):
        self.guardar([
            _cita(1, _dia(-10), duracion=60), _cita(2, _dia(-10), duracion=60),
            _cita(3, _dia(-9), duracion=120),
        ])
        d = kpi.ocupacion()['por_doctor'][0]
        self.assertEqual(d['dias_trabajados'], 2)
        self.assertEqual(d['horas'], 4.0)
        self.assertEqual(d['horas_por_dia'], 2.0)

    def test_las_canceladas_no_ocupan_sillon(self):
        self.guardar([
            _cita(1, _dia(-10), duracion=60),
            _cita(2, _dia(-10), duracion=60, estado='Hora Cancelada', id_status='2122'),
        ])
        self.assertEqual(kpi.ocupacion()['por_doctor'][0]['horas'], 1.0)


class TestCartera(BaseKpi):

    def test_flujo_neto(self):
        """Si las altas superan a los inicios, la cartera se vacía aunque el volumen
        de atenciones se vea estable."""
        self.guardar([
            _cita(1, _dia(-40), 'Montaje Total', rut='1'),
            _cita(2, _dia(-40), 'Retiro Total', rut='2'),
            _cita(3, _dia(-40), 'Retiro Total', rut='3'),
        ])
        c = kpi.cartera()
        self.assertEqual(c['inicios'], 1)
        self.assertEqual(c['altas'], 2)
        self.assertEqual(c['neto'], -1)

    def test_activos_cuenta_pacientes_unicos(self):
        self.guardar([
            _cita(1, _dia(-10), rut='1'), _cita(2, _dia(-5), rut='1'),
            _cita(3, _dia(-10), rut='2'),
            _cita(4, _dia(-200), rut='3'),      # fuera de la ventana de 90 días
        ])
        self.assertEqual(kpi.cartera()['activos'], 2)


class TestPacientesNuevos(BaseKpi):

    def test_nuevo_es_el_que_nunca_habia_venido(self):
        # RUTs numéricos a propósito: limpiar_rut() descarta todo lo que no sea dígito
        # o K, así que un rut de fantasía tipo 'viejo' se guardaría vacío y la cita
        # quedaría fuera de cualquier métrica por paciente.
        self.guardar([
            _cita(1, _dia(-400), rut='11111111-1'),
            _cita(2, _dia(-10), rut='11111111-1'),   # ya existía: no es nuevo
            _cita(3, _dia(-10), rut='22222222-2'),
        ])
        self.assertEqual(kpi.pacientes_nuevos(HOY - timedelta(days=30), HOY), 1)

    def test_las_citas_sin_rut_no_cuentan(self):
        """DentiDesk tiene citas sin documento; sin RUT no hay paciente que contar."""
        self.guardar([_cita(1, _dia(-10), rut='')])
        self.assertEqual(kpi.pacientes_nuevos(HOY - timedelta(days=30), HOY), 0)


class TestOrigen(BaseKpi):

    def test_agendado_via_web(self):
        """BookedBy trae el literal 'Agendado via web' — así el origen de la reserva
        sale para toda la historia, sin depender de agendamientos.jsonl (que solo
        existe desde julio-2026)."""
        self.guardar([
            _cita(1, _dia(-10), booked='Agendado via web'),
            _cita(2, _dia(-10), booked='Marcela Torres'),
        ])
        o = kpi.origen_reservas()
        self.assertEqual(o['web'], 1)
        self.assertEqual(o['pct_web'], 50.0)


class TestIngresos(BaseKpi):
    """Las boletas DTE que empuja la extensión F2 (DentiDesk no las expone por API)."""

    def _dte(self, folio, fecha, monto, rut='111111111', tipo='Boleta'):
        return {'SII_FOLIO': folio, 'FECHA_EMISION': fecha, 'MONTO': monto,
                'RUT': rut, 'TIPO_DOCUMENTO': tipo, 'DESCRIPCION': 'CONTROL MENSUAL'}

    def test_upsert_por_folio_es_idempotente(self):
        """La extensión reenvía el mismo mes cada día: no puede duplicar ingresos."""
        d = [self._dte('1001', _dia(-5), 45000)]
        kpi.registrar_ingresos(d)
        kpi.registrar_ingresos(d)
        self.assertEqual(kpi.plata()['ingresos'], 45000)
        self.assertEqual(kpi.plata()['boletas'], 1)

    def test_nota_de_credito_resta(self):
        kpi.registrar_ingresos([
            self._dte('1001', _dia(-5), 45000),
            self._dte('1002', _dia(-5), 45000, tipo='Nota de Crédito'),
        ])
        self.assertEqual(kpi.plata()['ingresos'], 0)

    def test_monto_con_formato_chileno(self):
        kpi.registrar_ingresos([self._dte('1001', _dia(-5), '$ 45.000')])
        self.assertEqual(kpi.plata()['ingresos'], 45000)

    def test_atribuye_el_doctor_por_la_atencion_del_dia(self):
        self.guardar([_cita(1, _dia(-5), rut='111111111', doctor='Rodrigo Oyonarte')])
        kpi.registrar_ingresos([self._dte('1001', _dia(-5), 45000)])
        self.assertEqual(kpi.plata()['por_doctor'][0]['doctor'], 'rodrigo')

    def test_sin_atencion_ese_dia_no_se_adivina_el_doctor(self):
        """Atribuir con una ventana de días ensuciaría el ingreso por doctor sin que
        nadie lo note. Mejor vacío y contado."""
        r = kpi.registrar_ingresos([self._dte('1001', _dia(-5), 45000)])
        self.assertEqual(r['sin_doctor'], 1)
        self.assertEqual(kpi.plata()['por_doctor'][0]['doctor'], '')

    def test_ingreso_por_hora_de_sillon(self):
        self.guardar([_cita(1, _dia(-5), rut='111111111', duracion=60)])
        kpi.registrar_ingresos([self._dte('1001', _dia(-5), 50000)])
        self.assertEqual(kpi.plata()['ingreso_por_hora'], 50000)

    def test_descarta_filas_sin_folio_o_fecha(self):
        r = kpi.registrar_ingresos([{'MONTO': 1000}, self._dte('', _dia(-1), 1)])
        self.assertEqual(r['guardados'], 0)

    def test_sin_ingresos_el_panel_sabe_que_no_hay_datos(self):
        """meses_con_ingresos=0 es lo que impide dibujar un margen falso (todos los
        gastos contra cero ingresos)."""
        self.assertEqual(kpi.plata()['meses_con_ingresos'], 0)


class TestResumenComparacion(BaseKpi):
    """Las dos formas en que la comparación interanual puede mentir."""

    def test_no_compara_rangos_que_se_solapan_con_su_propio_ano_anterior(self):
        """Comparar 2021-2026 contra 2020-2025 es comparar el período consigo mismo
        corrido un año. Mejor no mostrar flecha que mostrar una sin sentido."""
        r = kpi.resumen(date(2021, 1, 1), date(2026, 8, 1))
        self.assertFalse(r['comparable'])
        self.assertEqual(r['ano_anterior'], {})
        r2 = kpi.resumen(date(2026, 1, 1), date(2026, 3, 31))
        self.assertTrue(r2['comparable'])

    def test_no_da_porcentaje_sobre_valores_negativos(self):
        """El flujo neto de cartera pasando de -317 a -477 EMPEORÓ, pero la fórmula del
        porcentaje daba +50,5% y el panel lo pintaba verde. Con valores negativos se
        informa la diferencia absoluta y no un porcentaje."""
        # Año anterior: 1 inicio, 3 altas -> neto -2.  Año actual: 0 inicios, 4 altas -> -4.
        self.guardar([
            _cita(1, '2025-03-10', 'Montaje Total', rut='1'),
            _cita(2, '2025-03-11', 'Retiro Total', rut='2'),
            _cita(3, '2025-03-12', 'Retiro Total', rut='3'),
            _cita(4, '2025-03-13', 'Retiro Total', rut='4'),
            _cita(5, '2026-03-10', 'Retiro Total', rut='5'),
            _cita(6, '2026-03-11', 'Retiro Total', rut='6'),
            _cita(7, '2026-03-12', 'Retiro Total', rut='7'),
            _cita(8, '2026-03-13', 'Retiro Total', rut='8'),
        ])
        r = kpi.resumen(date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(r['actual']['neto_cartera'], -4)
        self.assertEqual(r['ano_anterior']['neto_cartera'], -2)
        self.assertIsNone(r['variacion_pct']['neto_cartera'])   # sin % engañoso
        self.assertEqual(r['delta']['neto_cartera'], -2)        # y la caída se ve


class TestCalidadDatos(BaseKpi):

    def test_cuenta_las_citas_sin_motivo(self):
        """DentiDesk permite agendar sin motivo: ~19% de las citas reales. Es lo que
        hace que la conversión sea un piso y no un valor exacto, y el panel tiene que
        declararlo."""
        self.guardar([_cita(1, _dia(-10), motivo=''), _cita(2, _dia(-10))])
        q = kpi.calidad_datos()
        self.assertEqual(q['sin_motivo'], 1)
        self.assertEqual(q['sin_motivo_pct'], 50.0)


class TestEsquema(unittest.TestCase):

    def test_init_db_es_idempotente(self):
        kpi.init_db()
        kpi.init_db()

    def test_migrar_corre_antes_de_los_indices(self):
        """Regresión del bug `ix_compras_sus` documentado en CLAUDE.md: un CREATE INDEX
        sobre una columna que aún no existe aborta el executescript entero y deja la
        base a medio migrar. NO se manifiesta en una base nueva — por eso la prueba
        simula una base preexistente a la que le falta una columna."""
        con = kpi._conn()
        con.execute('ALTER TABLE citas ADD COLUMN col_de_prueba TEXT')
        con.commit()
        con.close()
        kpi.init_db()   # no debe reventar
        con = kpi._conn()
        cols = {r['name'] for r in con.execute('PRAGMA table_info(citas)')}
        con.close()
        self.assertIn('col_de_prueba', cols)
        self.assertIn('estado_norm', cols)


def suite():
    loader = unittest.TestLoader()
    s = unittest.TestSuite()
    for cls in (TestEstados, TestDoctores, TestCategorias, TestIngesta, TestSerieMensual,
                TestReclasificar, TestDestinoPrimeraConsulta, TestFugas,
                TestOcupacion, TestCartera, TestPacientesNuevos, TestOrigen, TestIngresos, TestResumenComparacion,
                TestCalidadDatos, TestEsquema):
        s.addTests(loader.loadTestsFromTestCase(cls))
    return s


if __name__ == '__main__':
    kpi.init_db()
    r = unittest.TextTestRunner(verbosity=2).run(suite())
    sys.exit(0 if r.wasSuccessful() else 1)
