"""
test_clinico.py — La proyeccion clinica sobre clinica.db.

Cero red, cero correo. Todo corre sobre un directorio temporal: se fija
PATIENT_INDEX_PATH (que es de donde cuelgan TODOS los registros del proyecto) y
CLINICA_DB_PATH ANTES de importar los modulos, porque las rutas se resuelven al
importar.

Lo que se vigila aca no son los conteos: son las OCHO TRAMPAS del mapeo. Cada
una es un caso donde proyectar "lo obvio" afirmaria algo falso sobre un paciente
— y como estos datos van a alimentar una normativa, un dato falso que se ve
razonable es peor que un dato que falta.
"""

import os
import sys
import json
import shutil
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix='clinico_test_')
os.environ['PATIENT_INDEX_PATH'] = os.path.join(_TMP, 'patient_index.json')
os.environ['CLINICA_DB_PATH'] = os.path.join(_TMP, 'clinica_test.db')
os.environ.pop('KPI_DB_PATH', None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import basedatos          # noqa: E402
import clinico            # noqa: E402
import informe_pc         # noqa: E402
import transversal        # noqa: E402
import stopbang           # noqa: E402
import kpi                # noqa: E402

REGISTRO = informe_pc.REGISTRO_PATH


def _informe(iid, **campos):
    """Un informe con lo minimo, mas lo que la prueba quiera cambiar."""
    base = {
        'id': iid,
        'rut': '111111111',
        'rut_fmt': '11.111.111-1',
        'nombre': 'Paciente Prueba',
        'fecha': '2026-03-01',
        'edad': 8,
        'sexo': 'F',
        'doctor_texto': 'Alberto Del Real',
        'conclusion': 'corresponde',
        'creado': '2026-03-01T10:00:00',
        'actualizado': '2026-03-01T10:00:00',
    }
    base.update(campos)
    return base


def _sembrar(*informes):
    """Escribe el registro de informes tal cual lo guarda informe_pc."""
    REGISTRO.parent.mkdir(parents=True, exist_ok=True)
    REGISTRO.write_text(
        json.dumps({'informes': {i['id']: i for i in informes}},
                   ensure_ascii=False, indent=2), encoding='utf-8')


def _filas(sql, params=()):
    con = basedatos.conectar()
    try:
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


class Base(unittest.TestCase):
    def setUp(self):
        clinico.init_db()
        _sembrar()
        clinico.proyectar_todo()

    def tearDown(self):
        if REGISTRO.exists():
            REGISTRO.unlink()


# ── Las ocho trampas del mapeo ──────────────────────────────────────────────

class TestTratamientoPrevio(Base):
    """Trampa 1: la ausencia del campo NO es False.

    `sin_tratamiento_previo` se agrego al formulario cuando ya habia informes
    guardados. Proyectar los viejos como 0 afirmaria que esos pacientes SI
    tuvieron tratamiento previo — el criterio de inclusion de Bishara, invertido.
    """

    def test_ausente_queda_null_y_no_false(self):
        _sembrar(_informe('a'))
        clinico.proyectar_todo()
        fila = _filas('SELECT sin_tratamiento_previo v FROM informes')[0]
        self.assertIsNone(fila['v'])

    def test_los_tres_estados_se_distinguen_al_filtrar(self):
        _sembrar(
            _informe('sin', rut='111111111', sin_tratamiento_previo=True,
                     mediciones={'intermolar_maxilar': 45}),
            _informe('con', rut='222222222', sin_tratamiento_previo=False,
                     mediciones={'intermolar_maxilar': 46}),
            _informe('nada', rut='333333333',
                     mediciones={'intermolar_maxilar': 47}),
        )
        clinico.proyectar_todo()
        for filtro, esperado in (('sin', 1), ('con', 1), ('sin_dato', 1), ('todos', 3)):
            r = clinico.muestra({'tratamiento_previo': filtro})
            self.assertEqual(r['mediciones'], esperado,
                             'filtro %r trajo %d' % (filtro, r['mediciones']))


class TestEvaluacion(Base):
    """Trampa 2: `evaluacion` ausente NO es lista vacia.

    informe_pc sustituye la ausencia por EVALUACION_POR_DEFECTO al armar el
    documento. Proyectar [] afirmaria que en esa consulta no se hizo nada.
    """

    def test_ausente_proyecta_el_default_no_vacio(self):
        _sembrar(_informe('a'))
        clinico.proyectar_todo()
        v = _filas('SELECT evaluacion e FROM informes')[0]['e']
        self.assertEqual(set(v.split(',')), set(informe_pc.EVALUACION_POR_DEFECTO))

    def test_lista_vacia_explicita_se_respeta(self):
        _sembrar(_informe('a', evaluacion=[]))
        clinico.proyectar_todo()
        self.assertEqual(_filas('SELECT evaluacion e FROM informes')[0]['e'], '')


class TestTamizajeSinRegistrar(Base):
    """Trampa 3: un item sin registrar no es un item negativo.

    Aca eso se garantiza NO reimplementando el umbral: se traslada el puntaje
    que calcula stopbang.py. Repetir el umbral en dos lados es exactamente como
    el panel y el papel firmado terminan mostrando numeros distintos.
    """

    def test_el_puntaje_es_el_que_calcula_stopbang(self):
        sb = {'ronquido': 'si', 'cansancio': 'no', 'peso': 80, 'talla': 175,
              'respondido_por_el_paciente': '2026-03-01T12:00:00'}
        _sembrar(_informe('a', tamizaje={'stopbang': sb}))
        clinico.proyectar_todo()
        datos = dict(sb, imc=stopbang.imc(80, 175))
        esperado = stopbang.evaluar(datos)['puntaje']
        fila = _filas("SELECT puntaje p FROM tamizajes WHERE instrumento='STOP-BANG'")[0]
        self.assertEqual(fila['p'], float(esperado))

    def test_un_stopbang_no_respondido_no_se_proyecta(self):
        _sembrar(_informe('a', tamizaje={'stopbang': {'ronquido': 'si'}}))
        clinico.proyectar_todo()
        self.assertEqual(
            _filas("SELECT COUNT(*) n FROM tamizajes WHERE instrumento='STOP-BANG'")[0]['n'],
            0, 'sin respuesta del paciente no hay tamizaje que informar')


class TestPercentil(Base):
    """Trampa 4: el percentil se calcula con la edad y el sexo DE ESE informe.

    Usar la edad actual para un informe de hace dos anos da un percentil que ese
    paciente nunca tuvo.
    """

    def test_usa_la_edad_del_informe_no_la_de_hoy(self):
        _sembrar(
            _informe('viejo', fecha='2022-03-01', edad=8, sexo='F',
                     mediciones={'intermolar_maxilar': 45}),
            _informe('nuevo', fecha='2026-03-01', edad=12, sexo='F',
                     mediciones={'intermolar_maxilar': 45}),
        )
        clinico.proyectar_todo()
        got = {r['informe_id']: r['percentil'] for r in
               _filas("SELECT informe_id, percentil FROM mediciones "
                      "WHERE clave='intermolar_maxilar'")}
        for iid, edad in (('viejo', 8), ('nuevo', 12)):
            esperado = transversal.percentil('intermolar', 'maxilar', 'F', edad, 45)
            self.assertEqual(got[iid], esperado['percentil'])
        self.assertNotEqual(got['viejo'], got['nuevo'],
                            'el mismo milimetro a distinta edad no es el mismo percentil')

    def test_sin_edad_no_se_inventa_percentil(self):
        _sembrar(_informe('a', edad=None, mediciones={'intermolar_maxilar': 45}))
        clinico.proyectar_todo()
        fila = _filas('SELECT percentil p, valor_mm v FROM mediciones')[0]
        self.assertIsNone(fila['p'])
        self.assertEqual(fila['v'], 45, 'el milimetro medido si se guarda')


class TestOclusion(Base):
    """Trampa: 'no registrable' (pieza ausente) NO es Clase I."""

    def test_no_registrable_no_es_clase_i(self):
        _sembrar(_informe('a', mediciones={'clase_molar_der': 'no_registrable',
                                           'clase_molar_izq': 'I'}))
        clinico.proyectar_todo()
        got = {r['lado']: r['clase'] for r in
               _filas("SELECT lado, clase FROM oclusion WHERE sitio='clase_molar'")}
        self.assertIsNone(got['der'])
        self.assertEqual(got['izq'], 'I')

    def test_guarda_los_cuartos_con_signo(self):
        _sembrar(_informe('a', mediciones={'clase_molar_der': 'II-completa',
                                           'clase_molar_izq': 'III-1/2'}))
        clinico.proyectar_todo()
        got = {r['lado']: (r['cuartos'], r['clase']) for r in
               _filas("SELECT lado, cuartos, clase FROM oclusion")}
        self.assertEqual(got['der'], (4, 'II'))
        self.assertEqual(got['izq'], (-2, 'III'))


class TestFiltroDeClase(Base):
    """El filtro de clase exige AMBOS lados.

    Un caso Clase I a la derecha y Clase II a la izquierda no es Clase I: tiene
    nombre propio en Angle (subdivision). Meterlo en la muestra de Bishara la
    contamina.
    """

    def setUp(self):
        super().setUp()
        _sembrar(
            _informe('simetrico', rut='111111111',
                     mediciones={'intermolar_maxilar': 45,
                                 'clase_molar_der': 'I', 'clase_molar_izq': 'I'}),
            _informe('subdivision', rut='222222222',
                     mediciones={'intermolar_maxilar': 46,
                                 'clase_molar_der': 'I',
                                 'clase_molar_izq': 'II-completa'}),
        )
        clinico.proyectar_todo()

    def test_ambos_lados_deja_fuera_la_subdivision(self):
        r = clinico.muestra({'clase_molar': ['I'], 'lados': 'ambos'})
        self.assertEqual(r['mediciones'], 1)

    def test_algun_lado_la_incluye(self):
        r = clinico.muestra({'clase_molar': ['I'], 'lados': 'alguno'})
        self.assertEqual(r['mediciones'], 2)

    def test_seleccion_multiple(self):
        r = clinico.muestra({'clase_molar': ['I', 'II'], 'lados': 'ambos'})
        self.assertEqual(r['mediciones'], 2, 'la subdivision calza si se aceptan I y II')


# ── El contador de muestra ──────────────────────────────────────────────────

class TestMuestra(Base):

    def test_distingue_mediciones_de_pacientes(self):
        """Un paciente con dos informes aporta dos mediciones de la MISMA boca."""
        _sembrar(
            _informe('uno', rut='111111111', fecha='2025-01-01',
                     mediciones={'intermolar_maxilar': 45}),
            _informe('dos', rut='111111111', fecha='2026-01-01',
                     mediciones={'intermolar_maxilar': 46}),
        )
        clinico.proyectar_todo()
        r = clinico.muestra({'medida': 'intermolar'})
        self.assertEqual(r['mediciones'], 2)
        self.assertEqual(r['pacientes'], 1, 'la muestra no se infla con la misma boca')

    def test_no_emite_media_bajo_el_minimo(self):
        _sembrar(*[_informe('i%d' % n, rut='%09d' % (100000000 + n), edad=8, sexo='F',
                            mediciones={'intermolar_maxilar': 44 + n})
                   for n in range(clinico.MIN_CASOS_CELDA - 1)])
        clinico.proyectar_todo()
        celda = clinico.muestra({'medida': 'intermolar'})['celdas'][0]
        self.assertFalse(celda['suficiente'])
        self.assertIsNone(celda['media'])
        self.assertIsNone(celda['de'])
        self.assertEqual(celda['pacientes'], clinico.MIN_CASOS_CELDA - 1,
                         'el conteo si se muestra: es lo que dice cuanto falta')

    def test_emite_media_al_alcanzar_el_minimo(self):
        _sembrar(*[_informe('i%d' % n, rut='%09d' % (100000000 + n), edad=8, sexo='F',
                            mediciones={'intermolar_maxilar': 45})
                   for n in range(clinico.MIN_CASOS_CELDA)])
        clinico.proyectar_todo()
        celda = clinico.muestra({'medida': 'intermolar'})['celdas'][0]
        self.assertTrue(celda['suficiente'])
        self.assertEqual(celda['media'], 45.0)
        self.assertEqual(celda['de'], 0.0)

    def test_solo_sexo_confirmado_excluye_el_sugerido(self):
        """⚠️ "Confirmado" no es "tiene algo escrito".

        Hasta la revisión del 2026-09-10 este filtro comprobaba solo que el campo
        no estuviera vacío, así que dejaba pasar un sexo que la regla del nombre
        adivinó y que nadie miró — justo contra lo que dice proteger. Y como el
        percentil sale de una tabla DISTINTA según el sexo, colar uno equivocado
        produce un número plausible y falso.
        """
        _sembrar(
            _informe('declarado', rut='111111111', sexo='F',
                     sexo_origen='declarado', mediciones={'intermolar_maxilar': 45}),
            _informe('confirmado', rut='222222222', sexo='F',
                     sexo_origen='confirmado', mediciones={'intermolar_maxilar': 46}),
            _informe('sugerido', rut='333333333', sexo='F',
                     sexo_origen='sugerido', mediciones={'intermolar_maxilar': 47}),
            _informe('vacio', rut='444444444', sexo='',
                     mediciones={'intermolar_maxilar': 48}),
        )
        clinico.proyectar_todo()
        self.assertEqual(clinico.muestra({})['mediciones'], 4)
        self.assertEqual(clinico.muestra({'solo_sexo_confirmado': True})['mediciones'], 2)

    def test_un_informe_viejo_sin_origen_no_se_da_por_confirmado(self):
        """Los informes anteriores al campo no traen `sexo_origen`. Suponer que
        alguien los revisó es exactamente el error que el campo evita."""
        _sembrar(_informe('viejo', sexo='F', mediciones={'intermolar_maxilar': 45}))
        clinico.proyectar_todo()
        self.assertEqual(
            _filas('SELECT sexo_origen o FROM informes')[0]['o'], 'desconocido')
        self.assertEqual(clinico.muestra({'solo_sexo_confirmado': True})['mediciones'], 0)

    def test_no_mezcla_mediciones_incomparables(self):
        """⚠️ El error que destapó la revisión del 2026-09-10.

        Sin la medición en la llave de la celda, un ancho intermolar (45 mm) y un
        resalte (3 mm) del mismo paciente caían en la MISMA celda, y su promedio
        daba 24 mm: un número que se ve como un ancho de arcada plausible y no
        significa nada. La tabla de Bishara también está indexada por medida.
        """
        _sembrar(*[_informe('i%d' % n, rut='%09d' % (100000000 + n), edad=8, sexo='F',
                            mediciones={'intermolar_maxilar': 45, 'resalte': 3})
                   for n in range(clinico.MIN_CASOS_CELDA)])
        clinico.proyectar_todo()
        medias = {c['clave']: c['media'] for c in clinico.muestra({})['celdas']}
        self.assertEqual(medias['intermolar_maxilar'], 45.0)
        self.assertEqual(medias['resalte'], 3.0)
        self.assertNotIn(24.0, medias.values(), 'volvió a promediar peras con manzanas')


# ── La proyeccion como tal ──────────────────────────────────────────────────

class TestProyeccion(Base):

    def test_es_idempotente(self):
        _sembrar(_informe('a', mediciones={'intermolar_maxilar': 45,
                                           'clase_molar_der': 'I'}))
        primera = clinico.proyectar_todo()
        filas1 = _filas('SELECT * FROM mediciones ORDER BY clave')
        segunda = clinico.proyectar_todo()
        filas2 = _filas('SELECT * FROM mediciones ORDER BY clave')
        self.assertEqual(filas1, filas2)
        for k in ('informes', 'mediciones', 'oclusion', 'hallazgos'):
            self.assertEqual(primera[k], segunda[k])

    def test_reconstruye_desde_cero(self):
        _sembrar(_informe('a', mediciones={'intermolar_maxilar': 45}))
        clinico.proyectar_todo()
        antes = _filas('SELECT * FROM informes')
        con = basedatos.conectar()
        try:
            for t in clinico.TABLAS_PROYECTADAS:
                con.execute('DELETE FROM %s' % t)
            con.commit()
        finally:
            con.close()
        clinico.proyectar_todo()
        self.assertEqual(_filas('SELECT * FROM informes'), antes)

    def test_no_escribe_en_el_json(self):
        """El JSON es la fuente de verdad: proyectar solo LEE."""
        _sembrar(_informe('a', mediciones={'intermolar_maxilar': 45}))
        antes = REGISTRO.read_bytes()
        clinico.proyectar_todo()
        self.assertEqual(REGISTRO.read_bytes(), antes)

    def test_un_informe_sin_id_no_entra(self):
        _sembrar(_informe('a'))
        reg = json.loads(REGISTRO.read_text(encoding='utf-8'))
        reg['informes']['roto'] = {'rut': '111111111', 'id': ''}
        REGISTRO.write_text(json.dumps(reg), encoding='utf-8')
        clinico.proyectar_todo()
        self.assertEqual(_filas('SELECT COUNT(*) n FROM informes')[0]['n'], 1)

    def test_esta_al_dia_detecta_un_informe_nuevo(self):
        _sembrar(_informe('a'))
        clinico.proyectar_todo()
        self.assertTrue(clinico.esta_al_dia())
        _sembrar(_informe('a'), _informe('b', fecha='2026-04-01'))
        self.assertFalse(clinico.esta_al_dia())
        clinico.proyectar_si_hace_falta()
        self.assertTrue(clinico.esta_al_dia())


class TestPacientes(Base):
    """La espina: una fila por RUT visto en CUALQUIER fuente."""

    def test_incluye_ruts_que_no_estan_en_el_indice(self):
        """Si solo se proyectara patient_index.json, cada cruce perderia en
        silencio a los pacientes que una fuente conoce y la otra no — que son
        justo los casos raros que uno va a mirar."""
        _sembrar(_informe('a', rut='987654321'))
        clinico.proyectar_todo()
        fila = _filas("SELECT rut, en_indice FROM pacientes WHERE rut='987654321'")
        self.assertEqual(len(fila), 1)
        self.assertEqual(fila[0]['en_indice'], 0)


class TestEventos(Base):
    """La capa que permite cruzar cualquier sistema con cualquier otro."""

    def test_los_ocho_adaptadores_reales_producen_filas(self):
        """⚠️ El hueco que destapó la revisión del 2026-09-10.

        Hasta acá los adaptadores solo se probaban con uno falso, y en el entorno
        de desarrollo NINGÚN registro existe: la proyección daba `eventos: 0` con
        `errores: {}` y eso parecía correcto. No probaba nada — un adaptador que
        leyera la clave equivocada habría dado exactamente el mismo resultado.

        Acá se siembra cada registro con la forma REAL que guarda su módulo.
        """
        reg = REGISTRO.parent
        (reg / 'consentimientos_registro.json').write_text(json.dumps({'c1': {
            'rut': '111111111', 'tipo': 'ortodoncia', 'canal': 'email',
            'estado': 'firmado', 'creado': '2026-01-05T10:00:00'}}), encoding='utf-8')
        (reg / 'nps_registro.json').write_text(json.dumps({
            'envios': {'111111111': [{'fecha': '2026-02-01', 'id_agenda': '9',
                                      'doctor': 'Alberto', 'estado': 'enviado'}]},
            'respuestas': {'111111111': {'categoria': 'promotor',
                                         'fecha': '2026-02-01'}}}), encoding='utf-8')
        (reg / 'control_dental_registro.json').write_text(json.dumps({'inscritos': {
            '222222222': {'nombre': 'X', 'estado': 'activo',
                          'envios': [{'fecha': '2026-03-01', 'canal': 'email'}]}}}),
            encoding='utf-8')
        (reg / 'recaptacion_registro.json').write_text(json.dumps({'envios': {
            '333333333': [{'fecha_envio': '2026-04-01', 'doctor': 'Vial'}]}}),
            encoding='utf-8')
        (reg / 'seguimiento_pc_registro.json').write_text(json.dumps({'candidatos': {
            '111111111': {'rut': '111111111', 'fecha_pc': '2026-01-10',
                          'estado': 'pendiente'}}}), encoding='utf-8')
        (reg / 'fotos_finales_registro.json').write_text(json.dumps({'historial': [
            {'rut': '222222222', 'avisado': '2026-02-20'}]}), encoding='utf-8')
        (reg / 'seguros_registro.json').write_text(json.dumps({'f1': {
            'rut': '333333333', 'creado': '2026-03-01T10:00:00',
            'estado': 'enviado'}}), encoding='utf-8')
        (reg / 'reactivacion_registro.json').write_text(json.dumps({'candidatos': {
            '444444444': {'rut': '444444444', 'estado': 'pendiente',
                          'proxima_fecha': '2026-04-01'}}}), encoding='utf-8')

        # Los registros sembrados los leen TODOS los módulos, así que hay que
        # retirarlos: si no, las pruebas que corren después proyectan eventos que
        # no pidieron y esta suite deja de ser determinista.
        sembrados = [p for p in reg.glob('*_registro.json') if p != REGISTRO]
        try:
            r = clinico.proyectar_todo()
            self.assertEqual(r['errores'], {}, 'ningún adaptador debe fallar')
            sistemas = {f['sistema'] for f in _filas('SELECT DISTINCT sistema FROM eventos')}
            self.assertEqual(sistemas, {s for s, _fn in clinico.ADAPTADORES},
                             'los ocho sistemas tienen que aportar filas')
            # Y el RUT tiene que quedar utilizable para cruzar, no vacío.
            sin_rut = _filas("SELECT COUNT(*) n FROM eventos WHERE rut = ''")[0]['n']
            self.assertEqual(sin_rut, 0)
        finally:
            for p in sembrados:
                p.unlink()

    def test_un_adaptador_que_revienta_no_tumba_a_los_demas(self):
        def explota():
            raise RuntimeError('registro corrupto')

        original = clinico.ADAPTADORES
        clinico.ADAPTADORES = (('roto', explota),) + original
        try:
            r = clinico.proyectar_todo()
            self.assertIn('roto', r['errores'])
            self.assertTrue(r['ok'], 'la proyeccion igual se completa')
        finally:
            clinico.ADAPTADORES = original

    def test_las_filas_llevan_rut_limpio_y_fecha_corta(self):
        def falso():
            yield ('ref-1', '11.111.111-1', '2026-03-01T10:00:00', 'prueba',
                   'enviado', {'canal': 'email'})

        original = clinico.ADAPTADORES
        clinico.ADAPTADORES = (('demo', falso),)
        try:
            clinico.proyectar_todo()
            fila = _filas("SELECT * FROM eventos WHERE sistema='demo'")[0]
            self.assertEqual(fila['rut'], '111111111')
            self.assertEqual(fila['fecha'], '2026-03-01')
            self.assertEqual(json.loads(fila['datos'])['canal'], 'email')
        finally:
            clinico.ADAPTADORES = original

    def test_dos_filas_del_mismo_paciente_no_se_pisan(self):
        """Sin un ref unico, la PK haria desaparecer una de las dos en silencio."""
        def falso():
            for f in ('2026-01-01', '2026-02-01'):
                yield (None, '111111111', f, 'aviso', 'enviado', {})

        original = clinico.ADAPTADORES
        clinico.ADAPTADORES = (('demo', falso),)
        try:
            clinico.proyectar_todo()
            self.assertEqual(
                _filas("SELECT COUNT(*) n FROM eventos WHERE sistema='demo'")[0]['n'], 2)
        finally:
            clinico.ADAPTADORES = original


class TestEsquema(unittest.TestCase):

    def test_convive_con_las_tablas_de_kpi(self):
        """Las dos capas viven en el MISMO archivo: es lo que hace que un JOIN
        cruce un ancho de arcada con la agenda sin salir de SQL."""
        kpi.init_db()
        clinico.init_db()
        con = basedatos.conectar()
        try:
            tablas = {r['name'] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
        for t in ('citas', 'disponibilidad', 'ingresos', 'snapshots'):
            self.assertIn(t, tablas, 'clinico.init_db() no puede tocar lo de kpi')
        for t in clinico.TABLAS_PROYECTADAS:
            self.assertIn(t, tablas)

    def test_la_proyeccion_no_borra_las_tablas_de_kpi(self):
        """⚠️ `disponibilidad` NO se puede reconstruir de ninguna parte:
        getAvailableHours solo responde por dias FUTUROS."""
        kpi.init_db()
        con = basedatos.conectar()
        try:
            con.execute("INSERT OR REPLACE INTO disponibilidad VALUES "
                        "('2026-03-01','alberto',120,60,'x')")
            con.commit()
        finally:
            con.close()
        _sembrar(_informe('a'))
        clinico.proyectar_todo()
        self.assertEqual(_filas('SELECT COUNT(*) n FROM disponibilidad')[0]['n'], 1)

    def test_init_db_es_idempotente_sobre_una_base_preexistente(self):
        """Los CREATE INDEX van DESPUES de _migrar(): un indice sobre una
        columna que todavia no existe aborta el script entero y deja la base a
        medio migrar. No se nota en una base nueva, solo en las viejas."""
        clinico.init_db()
        con = basedatos.conectar()
        try:
            con.execute('ALTER TABLE informes ADD COLUMN col_de_prueba TEXT')
            con.commit()
        finally:
            con.close()
        clinico.init_db()
        cols = {r['name'] for r in _filas('PRAGMA table_info(informes)')}
        self.assertIn('col_de_prueba', cols)

    def test_proyectar_sobrevive_a_una_columna_nueva(self):
        """⚠️ Los INSERT nombran sus columnas porque `_migrar()` existe para
        agregar columnas. Con INSERT posicional esto revienta con "table has N
        columns but M values were supplied" — y NO se nota en una base nueva,
        solo en la de produccion, que es la que tiene la columna agregada."""
        clinico.init_db()
        con = basedatos.conectar()
        try:
            cols = {r['name'] for r in con.execute('PRAGMA table_info(informes)')}
            if 'columna_futura' not in cols:
                con.execute('ALTER TABLE informes ADD COLUMN columna_futura TEXT')
                con.commit()
        finally:
            con.close()
        _sembrar(_informe('a', mediciones={'intermolar_maxilar': 45}))
        r = clinico.proyectar_todo()
        self.assertEqual(r['informes'], 1)


def suite():
    s = unittest.TestSuite()
    for clase in (TestTratamientoPrevio, TestEvaluacion, TestTamizajeSinRegistrar,
                  TestPercentil, TestOclusion, TestFiltroDeClase, TestMuestra,
                  TestProyeccion, TestPacientes, TestEventos, TestEsquema):
        s.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(clase))
    return s


if __name__ == '__main__':
    kpi.init_db()
    clinico.init_db()
    ok = unittest.TextTestRunner(verbosity=2).run(suite()).wasSuccessful()
    shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(0 if ok else 1)
