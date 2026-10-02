"""
test_perfil.py — Perfil de pacientes: referidos.py, geocodificar.py y perfil.py.

Cero red (Nominatim interceptado), cero correo. Todo sobre un directorio
temporal: PATIENT_INDEX_PATH y CLINICA_DB_PATH se fijan ANTES de importar,
porque las rutas se resuelven al importar.

Lo que se vigila:
  - que no se ADIVINE a una persona (dos candidatos = por confirmar);
  - que una frase no se vuelva "dentista" por aparecer dos veces;
  - que lo que se ensena desde el panel se aplique para siempre;
  - que a Nominatim no le llegue un depto ni a la cache un RUT;
  - que un punto en otra comuna no se crea;
  - que las tasas sean solo de cohortes cerradas y con n minimo;
  - que el mapa no lleve RUT ni puntos apilados en el centro de una comuna.

🔒 Todos los nombres de esta suite son INVENTADOS (repo publico). Copian la FORMA
de las respuestas reales — "Dra." delante, tildes, abreviaturas, errores de
tipeo —, que es lo unico que el codigo ejercita. Si se agregan casos, inventarlos.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta

_TMP = tempfile.mkdtemp(prefix='perfil_test_')
os.environ['PATIENT_INDEX_PATH'] = os.path.join(_TMP, 'patient_index.json')
os.environ['CLINICA_DB_PATH'] = os.path.join(_TMP, 'clinica_test.db')
os.environ.pop('KPI_DB_PATH', None)
os.environ.pop('FICHAS_PERFIL_PATH', None)
os.environ.pop('REFERIDOS_CONFIG_PATH', None)
os.environ.pop('GEOCACHE_PATH', None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import basedatos      # noqa: E402
import clinico        # noqa: E402
import fechas         # noqa: E402
import fichas         # noqa: E402
import geocodificar as geo  # noqa: E402
import kpi            # noqa: E402
import pacientes      # noqa: E402
import perfil         # noqa: E402
import referidos      # noqa: E402

HOY = fechas.hoy_chile()


def _dia(delta):
    return (HOY + timedelta(days=delta)).isoformat()


def _ctx(perfiles, indice=None, cfg=None):
    return referidos.Contexto(perfiles, indice or {}, cfg if cfg is not None else {})


# ═══════════════════════════════════════════════════════════════════════════
# referidos.py
# ═══════════════════════════════════════════════════════════════════════════

class TestReferidosCanales(unittest.TestCase):

    def setUp(self):
        # Dos fichas nombran a la misma dentista habitual -> es dentista.
        self.perf = {
            '1': {'dentista': 'María Teresa Ulloa'},
            '2': {'dentista': 'Maria Teresa Ulloa'},
            '3': {'dentista': 'mama fue paciente del richard'},
            '4': {'dentista': 'mama fue paciente del richard'},
            '5': {'dentista': 'no tengo'},
            '6': {'dentista': 'no tengo'},
        }
        self.ctx = _ctx(self.perf)

    def c(self, txt, rut=''):
        return referidos.clasificar(txt, self.ctx, rut)

    def test_palabras_clave(self):
        self.assertEqual(self.c('mi mamá')['canal'], 'familia')
        self.assertEqual(self.c('Una amiga')['canal'], 'amigo')
        self.assertEqual(self.c('Google')['canal'], 'internet')
        self.assertEqual(self.c('yo')['canal'], 'propio')
        self.assertEqual(self.c('.')['canal'], 'sin_dato')
        self.assertEqual(self.c('')['canal'], 'sin_dato')
        self.assertEqual(self.c('la pediatra')['canal'], 'medico')
        self.assertEqual(self.c('somos pacientes')['canal'], 'ya_paciente')

    def test_dentista_por_ficha_y_alias(self):
        r = self.c('Dra. Ulloa')
        self.assertEqual((r['canal'], r['clave']), ('dentista', 'maria teresa ulloa'))
        self.assertEqual(self.c('tere ulloa')['clave'], 'maria teresa ulloa')

    def test_una_frase_repetida_no_es_dentista(self):
        """'mama fue paciente' se escribio dos veces como dentista habitual: es una
        frase, no una persona."""
        self.assertNotIn('mama fue paciente del richard', self.ctx.dentistas)
        self.assertNotIn('no tengo', self.ctx.dentistas)
        self.assertEqual(self.c('mama fue paciente del richard')['canal'], 'familia')

    def test_doctor_de_la_clinica(self):
        self.assertEqual(self.c('conozco a Rodrigo Oyonarte')['canal'], 'doctor_clinica')
        self.assertEqual(self.c('Dr. Vial')['clave'], 'patricio vial')
        # "del real" son dos doctores: es la clinica, sin elegir uno.
        r = self.c('Dr. Del Real')
        self.assertEqual((r['canal'], r['clave']), ('doctor_clinica', 'del real'))

    def test_dr_con_nombre_desconocido_es_dentista(self):
        r = self.c('Dr. Juan Pérez Soto')
        self.assertEqual(r['canal'], 'dentista')

    def test_error_de_tipeo_se_une_a_un_solo_dentista(self):
        ctx = _ctx({'1': {'dentista': 'Beatriz Montalva'}, '2': {'dentista': 'Beatriz Montalva'}})
        self.assertEqual(referidos.clasificar('Beatriz Montalba', ctx)['clave'],
                         'beatriz montalva')


class TestReferidosPersonas(unittest.TestCase):

    INDICE = {
        '11': {'nombres': 'Ana María', 'apellidos': 'Soto Pérez'},
        '22': {'nombres': 'Ana', 'apellidos': 'Soto Rojas'},
        '33': {'nombres': 'Pedro', 'apellidos': 'Lagos Díaz'},
    }

    def setUp(self):
        self.ctx = _ctx({}, self.INDICE)

    def test_un_solo_paciente_se_identifica(self):
        r = referidos.clasificar('Pedro Lagos', self.ctx)
        self.assertEqual((r['canal'], r['rut'], r['estado']), ('paciente', '33', 'auto'))

    def test_dos_candidatos_no_se_adivina(self):
        r = referidos.clasificar('Ana Soto', self.ctx)
        self.assertEqual((r['canal'], r['estado'], r['candidatos']),
                         ('persona', 'por_confirmar', 2))

    def test_no_se_recomienda_a_si_mismo(self):
        r = referidos.clasificar('Pedro Lagos', self.ctx, rut_paciente='33')
        self.assertNotEqual(r['canal'], 'paciente')

    def test_un_nombre_de_pila_suelto_no_identifica(self):
        r = referidos.clasificar('Pedro', self.ctx)
        self.assertEqual(r['estado'], 'por_confirmar')


class TestReferidosAprende(unittest.TestCase):

    def setUp(self):
        referidos._CFG.save({'confirmados': {}, 'alias': {}, 'dentistas': [],
                             'no_dentistas': []})

    def test_confirmar_aplica_al_mismo_texto_escrito_distinto(self):
        referidos.confirmar('Victoria Fuenzalida', 'dentista', nombre='Victoria Fuenzalida')
        ctx = referidos.Contexto({}, {})
        r = referidos.clasificar('  victoria FUENZALIDA ', ctx)
        self.assertEqual((r['canal'], r['estado']), ('dentista', 'confirmado'))
        # Y ademas entra al directorio: "Dra. Fuenzalida" se reconoce sola.
        self.assertEqual(referidos.clasificar('Dra. Fuenzalida', ctx)['clave'], 'victoria fuenzalida')

    def test_olvidar(self):
        referidos.confirmar('cosa rara', 'otro')
        referidos.olvidar('Cosa Rara')
        self.assertNotIn('cosa rara', referidos.config()['confirmados'])

    def test_alias_manual(self):
        referidos.fijar_alias('Dra. Kesler', 'Daniel Kessler')
        ctx = referidos.Contexto({'1': {'dentista': 'Daniel Kessler'},
                                  '2': {'dentista': 'Daniel Kessler'}}, {})
        self.assertEqual(referidos.clasificar('Dra. Kesler', ctx)['clave'],
                         'daniel kessler')

    def test_canal_invalido(self):
        with self.assertRaises(ValueError):
            referidos.confirmar('x', 'inventado')

    def test_por_confirmar_agrupa_por_texto(self):
        perf = {'1': {'recomendo': 'Fulano Mengano'}, '2': {'recomendo': 'fulano mengano'}}
        clasif, _ = referidos.clasificar_todas(perf, {}, {})
        pend = referidos.por_confirmar(perf, clasif)
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]['veces'], 2)


# ═══════════════════════════════════════════════════════════════════════════
# geocodificar.py
# ═══════════════════════════════════════════════════════════════════════════

# Un punto dentro de Vitacura y otro dentro de La Florida (centros de comuna).
VITACURA = None
LA_FLORIDA = None


def setUpModule():
    global VITACURA, LA_FLORIDA
    pk = geo.por_clave()
    VITACURA = (pk['vitacura']['lat'], pk['vitacura']['lon'])
    LA_FLORIDA = (pk['la florida']['lat'], pk['la florida']['lon'])


class TestComunas(unittest.TestCase):

    def test_datos_publicos_completos(self):
        cs = geo.comunas()
        self.assertEqual(len(cs), 52)
        self.assertTrue(all(c.get('geometria') and c['poblacion'] > 0 for c in cs))
        self.assertTrue(geo.fuentes())

    def test_comuna_clave(self):
        self.assertEqual(geo.comuna_clave('Las Condes, Santiago'), 'las condes')
        self.assertEqual(geo.comuna_clave('Lo Barneche'), 'lo barnechea')
        self.assertEqual(geo.comuna_clave('vitacura santiago'), 'vitacura')
        self.assertEqual(geo.comuna_clave('Ñuñoa'), 'nunoa')
        self.assertEqual(geo.comuna_clave('Chicureo'), 'colina')
        self.assertEqual(geo.comuna_clave('San Felipe'), '')     # fuera de la RM

    def test_la_clinica_cae_en_las_condes(self):
        self.assertEqual(geo.comuna_de_punto(*geo.CLINICA), 'las condes')
        self.assertEqual(geo.comuna_de_punto(-33.0, -71.6), '')   # Valparaiso

    def test_limpiar_direccion_saca_depto_y_punto_de_miles(self):
        self.assertEqual(geo.limpiar_direccion('Av. Las Condes 12.345, depto 501'),
                         'Av. Las Condes 12345')
        self.assertEqual(geo.limpiar_direccion('Camino La Fuente 1234 casa 5'),
                         'Camino La Fuente 1234')
        self.assertEqual(geo.limpiar_direccion('Los Pinos 55 dpto. 3B'), 'Los Pinos 55')


class TestResolver(unittest.TestCase):

    def test_punto_en_otra_comuna_no_se_cree(self):
        reg = geo.resolver('Los Almendros 123', 'Vitacura',
                           consultar=lambda c, n: [LA_FLORIDA])
        self.assertTrue(reg.get('fallo'))

    def test_punto_correcto(self):
        reg = geo.resolver('Los Almendros 123', 'Vitacura',
                           consultar=lambda c, n: [LA_FLORIDA, VITACURA])
        self.assertEqual((reg['comuna'], reg['precision']), ('vitacura', 'calle'))

    def test_santiago_es_la_ciudad(self):
        reg = geo.resolver('Los Almendros 123', 'Santiago',
                           consultar=lambda c, n: [LA_FLORIDA])
        self.assertEqual(reg['comuna'], 'la florida')

    def test_a_nominatim_solo_va_calle_y_comuna(self):
        visto = []
        geo.resolver('Av. Las Condes 12.345, depto 501', 'Vitacura',
                     consultar=lambda c, n: visto.append((c, n)) or [])
        self.assertEqual(visto, [('Av. Las Condes 12345', 'Vitacura')])


class TestCorrer(unittest.TestCase):

    def setUp(self):
        geo._CACHE.save({})

    def test_guarda_sin_rut_y_no_repite(self):
        llamadas = []

        def consultar(calle, comuna):
            llamadas.append(calle)
            return [VITACURA]
        dirs = [('Los Almendros 123', 'Vitacura'), ('Los Almendros 123', 'vitacura'),
                ('sin numero', 'Vitacura')]
        r = geo.correr(maximo=10, consultar=consultar, pausa=0, direcciones=dirs)
        self.assertEqual((r['consultadas'], r['encontradas']), (1, 1))
        self.assertEqual(len(llamadas), 1)
        cache = geo.cache()
        self.assertEqual(list(cache), ['los almendros 123|vitacura'])
        geo.correr(maximo=10, consultar=consultar, pausa=0, direcciones=dirs)
        self.assertEqual(len(llamadas), 1)

    def test_error_de_red_corta_sin_la_direccion(self):
        def falla(calle, comuna):
            raise OSError('Los Almendros 123 no responde')
        r = geo.correr(maximo=10, consultar=falla, pausa=0,
                       direcciones=[('Los Almendros 123', 'Vitacura')])
        self.assertFalse(r['ok'])
        self.assertNotIn('Almendros', r['error'])

    def test_ubicacion_sin_red(self):
        cache = {'los almendros 123|vitacura': {'lat': VITACURA[0], 'lon': VITACURA[1],
                                                'comuna': 'vitacura', 'precision': 'calle'}}
        self.assertEqual(geo.ubicacion('Los Almendros 123', 'Vitacura', cache)['precision'],
                         'calle')
        # Sin geocodificar: el centro de la comuna, marcado como tal.
        u = geo.ubicacion('Otra 9', 'La Florida', cache)
        self.assertEqual((u['comuna'], u['precision']), ('la florida', 'comuna'))
        # "Santiago" sin punto no se ubica en el centro.
        u = geo.ubicacion('', 'Santiago', cache)
        self.assertIsNone(u['lat'])
        self.assertIsNone(geo.ubicacion('', 'San Felipe', cache))


# ═══════════════════════════════════════════════════════════════════════════
# perfil.py
# ═══════════════════════════════════════════════════════════════════════════

class TestClasificadoresFicha(unittest.TestCase):

    def test_intereses_multiples(self):
        g = perfil.grupos('Fútbol, piano y leer', perfil.INTERESES)
        self.assertEqual(set(g), {'Fútbol', 'Música', 'Lectura'})
        # Palabra entera: "playa" no es "play".
        self.assertNotIn('Videojuegos / tecnología', perfil.grupos('playa', perfil.INTERESES))

    def test_colegio(self):
        self.assertEqual(perfil.colegio_clave('Colegio Cumbres'), perfil.colegio_clave('cumbres'))
        self.assertEqual(perfil.colegio_clave('The Grange School'), 'grange')
        self.assertEqual(perfil.colegio_clave('no aplica'), '')

    def test_familiar_tratado(self):
        self.assertEqual(perfil._familiar_tratado('Sí, mi hermana'), 1)
        self.assertEqual(perfil._familiar_tratado('No'), 0)
        self.assertEqual(perfil._familiar_tratado('no que yo sepa'), 0)
        self.assertIsNone(perfil._familiar_tratado(''))

    def test_prevision(self):
        self.assertEqual(perfil.prevision_grupo('ISAPRE BANMEDICA'), 'Isapre')
        self.assertEqual(perfil.prevision_grupo('Fonasa B'), 'Fonasa')
        self.assertEqual(perfil.prevision_grupo(''), '')

    def test_wilson(self):
        bajo, alto = perfil.wilson(50, 100)
        self.assertTrue(39 < bajo < 41 and 59 < alto < 61)
        self.assertEqual(perfil.wilson(0, 0), (None, None))


class TestPerfilSobreLaBase(unittest.TestCase):
    """Primeras consultas reales en la agenda + fichas + ubicaciones."""

    CFG = {'doctores': {'alberto': {'professional_name': 'Alberto Del Real'}}}

    @classmethod
    def setUpClass(cls):
        kpi.init_db()
        clinico.init_db()

    def setUp(self):
        con = kpi._conn()
        for t in ('citas', 'snapshots', 'ingresos'):
            con.execute('DELETE FROM %s' % t)
        con.commit()
        con.close()
        self._orig = kpi._destinos_manuales_map
        kpi._destinos_manuales_map = lambda: {}
        geo._CACHE.save({})
        referidos._CFG.save({})
        n = [0]

        def cita(rut, fecha, motivo):
            n[0] += 1
            return {'IdAgenda': str(n[0]), 'Date': fecha, 'time': '10:00:00',
                    'duration': 30, 'ProfessionalName': 'Alberto Del Real',
                    'Reason': motivo, 'IdStatus': '2125', 'Status': 'Atendido',
                    'PatientDocument': rut, 'CreateDate': '2020-01-01 09:00:00',
                    'BookedBy': ''}
        citas, indice, perf = [], {}, {}
        # 20 recomendados por dentista: todos inician. 20 por Google: ninguno.
        for i in range(40):
            rut = '9%07d' % i
            citas.append(cita(rut, _dia(-300), 'Primera Consulta'))
            dentista = i < 20
            if dentista:
                citas.append(cita(rut, _dia(-280), 'Montaje Total'))
            indice[rut] = {'nombres': 'P%d' % i, 'apellidos': 'X', 'genero': 'F',
                           'fecha_nacimiento': '2014-01-01', 'comuna': 'Vitacura',
                           'direccion': 'Calle %d 100' % i, 'telefono': '9%08d' % i}
            perf[rut] = {'recomendo': 'Dra. Beatriz Montalva' if dentista else 'Google',
                         'fecha_form': _dia(-301), 'hobbies': 'futbol',
                         'colegio': 'Colegio Cumbres'}
        # Una consulta reciente: todavia no decide.
        citas.append(cita('80000001', _dia(-10), 'Primera Consulta'))
        indice['80000001'] = {'nombres': 'R', 'apellidos': 'X', 'comuna': 'Vitacura'}
        kpi.guardar_citas(citas, self.CFG)
        pacientes._save_index(indice) if hasattr(pacientes, '_save_index') else \
            pacientes._STORE.save(indice)
        fichas._PERFIL.save(perf)
        # Geocodificadas las 10 primeras, a nivel de calle.
        geo._CACHE.save({geo.clave_cache('Calle %d 100' % i, 'vitacura'): {
            'lat': VITACURA[0] + i * 1e-5, 'lon': VITACURA[1], 'comuna': 'vitacura',
            'precision': 'calle', 'ts': '2026-01-01T00:00:00'} for i in range(10)})
        clinico.proyectar_todo()

    def tearDown(self):
        kpi._destinos_manuales_map = self._orig

    def test_proyeccion_llena_las_tablas(self):
        con = basedatos.conectar()
        try:
            n_fp = con.execute('SELECT COUNT(*) FROM fichas_perfil').fetchone()[0]
            n_ub = con.execute("SELECT COUNT(*) FROM ubicaciones WHERE precision='calle'"
                               ).fetchone()[0]
            canal = con.execute("SELECT canal FROM fichas_perfil WHERE rut='90000000'"
                                ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual((n_fp, n_ub, canal), (40, 10, 'dentista'))

    def test_conversion_solo_cohortes_cerradas(self):
        filas = perfil.base()
        self.assertEqual(len(filas), 41)
        conv = {r['valor']: r for r in perfil.conversion(filas, 'canal')}
        self.assertEqual(conv['dentista']['pct_inicio'], 100.0)
        self.assertEqual(conv['internet']['pct_inicio'], 0.0)
        # La consulta de hace 10 dias no esta en ningun denominador.
        self.assertEqual(sum(r['n'] for r in conv.values()), 40)

    def test_pocos_casos_no_dan_tasa(self):
        filas = perfil.base()[:3]
        for r in perfil.conversion(filas, 'canal'):
            self.assertIsNone(r['pct_inicio'])

    def test_edad_a_la_fecha_de_la_consulta(self):
        f = [x for x in perfil.base() if x['rut'] == '90000000'][0]
        esperado = perfil._edad('2014-01-01', f['fecha'])
        self.assertEqual(f['edad'], esperado)
        self.assertEqual(f['banda_edad'], '<12' if esperado < 12 else '12-17')

    def test_hallazgo_solo_con_diferencia_clara(self):
        h = perfil.hallazgos(perfil.base())
        textos = ' '.join(x['texto'] for x in h)
        self.assertIn('Dentista externo', textos)

    def test_mapa_sin_rut_y_sin_centros_de_comuna(self):
        m = perfil.mapa({'universo': 'todos'})
        self.assertEqual(m['n'], 10)     # las 31 sin geocodificar NO se apilan
        self.assertNotIn('9000', json.dumps(m['celdas']))
        for celda in m['celdas']:
            self.assertEqual(len(celda), 3)
            self.assertEqual(round(celda[0], 3), celda[0])

    def test_mapa_filtrado(self):
        m = perfil.mapa({'destino': 'inicio'})
        self.assertEqual(m['n'], 10)     # las 10 geocodificadas son del grupo dentista
        m = perfil.mapa({'destino': 'perdido'})
        self.assertEqual(m['n'], 0)

    def test_recomendadores_e_intereses(self):
        filas = perfil.base()
        rec = perfil.recomendadores(filas)
        self.assertEqual(rec[0]['trajo'], 20)
        self.assertEqual(rec[0]['iniciaron'], 20)
        i = perfil.intereses(filas)
        self.assertEqual(i['hobbies'][0]['label'], 'Fútbol')
        self.assertEqual(i['colegios'][0]['n'], 40)

    def test_resumen_completo_y_snapshot(self):
        r = perfil.resumen()
        for k in ('calidad', 'llegan', 'tendencia', 'inician', 'recomendadores',
                  'intereses', 'valor', 'geografia', 'hallazgos'):
            self.assertIn(k, r)
        self.assertEqual(len(r['geografia']['comunas']), 52)
        self.assertEqual(perfil.guardar_snapshot(), 50.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
