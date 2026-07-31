"""
reporte_semanal.py - Reporte semanal de KPIs de negocio (Ortodoncia Richard)

Arma los DATOS y el HTML de un reporte semanal para el Dr. Alberto: 4 areas
(Comercial, Clinico, Reputacion, Operacion) mas Reactivacion. Este modulo NO
envia nada -- solo junta lo que ya calculan los otros modulos (stats, nps,
seguimiento_pc, reactivacion, compras, seguros, dentidesk/control_dental) y
arma el cuerpo HTML. El cableado (ruta + envio via notify) lo hace otro modulo.

Cada fuente esta envuelta en su propio try/except: agregar() NUNCA lanza,
aunque DentiDesk, compras (SQLite) o cualquier otra fuente fallen -- un
reporte con un bloque en 'error' es mejor que un reporte que nunca sale.

Mismo criterio de estilo que seguimiento_pc.py: imports arriba, comentarios
en espanol, defensivo con try/except donde hay red o I/O externo.
"""

from datetime import date, timedelta

import fechas          # hoy_chile(): Render corre en UTC. Ver fechas.py.
import stats
import nps
import seguimiento_pc
import reactivacion
import compras
import seguros
import dentidesk
import control_dental
import notify           # _email_layout(): el sobre comun de los correos.

_DIAS_RETENCION = None  # no aplica -- este modulo no persiste nada propio.


# ── Ventana de fechas ────────────────────────────────────────────────────────

def ventana_semana_anterior(hoy=None):
    """(desde, hasta) como date: el LUNES a DOMINGO de la semana ANTERIOR a
    'hoy' (default fechas.hoy_chile()). Ej: si hoy es lunes 2026-08-03,
    devuelve (2026-07-27, 2026-08-02)."""
    hoy = hoy or fechas.hoy_chile()
    lunes_esta_semana = hoy - timedelta(days=hoy.weekday())
    desde = lunes_esta_semana - timedelta(days=7)
    hasta = desde + timedelta(days=6)
    return desde, hasta


def _scheduling_cfg():
    """dentidesk._get_agenda_day() necesita el config de scheduling
    (credenciales DentiDesk) -- import perezoso para evitar ciclos (mismo
    patron que control_dental._scheduling_cfg / seguimiento_pc._scheduling_cfg)."""
    import scheduling
    return scheduling.load_config()


# ── Barrido clinico: recorre los dias habiles de la ventana ─────────────────

def _barrido_clinico(desde, hasta, scfg=None):
    """Recorre los dias HABILES en [desde,hasta] con dentidesk._get_agenda_day
    (toda la clinica, todos los doctores) y cuenta atendidos, no_shows,
    cancelaciones, primeras_consultas, inicios y altas. Cada dia va en su
    propio try/except: si uno falla, cuenta 0 ese dia y sigue con el resto."""
    scfg = scfg or _scheduling_cfg()

    atendidos = no_shows = cancelaciones = 0
    primeras_consultas = inicios = altas = 0
    dias_habiles = 0

    d = desde
    while d <= hasta:
        if d.weekday() < 5:
            dias_habiles += 1
            try:
                citas = dentidesk._get_agenda_day(scfg, d) or []
                for c in citas:
                    estado = (c.get('Status') or '').lower()
                    reason = (c.get('Reason') or '').strip()

                    es_atendida = 'atendid' in estado
                    es_no_show = 'no llega' in estado
                    es_cancel = 'cancel' in estado

                    if es_atendida:
                        atendidos += 1
                    if es_no_show:
                        no_shows += 1
                    if es_cancel:
                        cancelaciones += 1
                    if seguimiento_pc.es_primera_consulta(reason) and not (es_cancel or es_no_show):
                        primeras_consultas += 1

                    categoria = control_dental.clasificar_motivo(reason, None)
                    ocurrio = not any(s in estado for s in control_dental._ESTADOS_NO_OCURRIO)
                    if ocurrio and categoria in ('inicio_fijos', 'inicio_alineadores'):
                        inicios += 1
                    if ocurrio and categoria == 'fin_definitivo':
                        altas += 1
            except Exception:
                # Un dia que falla (DentiDesk caido, token vencido, etc.) cuenta
                # 0 y no rompe el resto del barrido.
                pass
        d += timedelta(days=1)

    return {
        'atendidos': atendidos,
        'no_shows': no_shows,
        'cancelaciones': cancelaciones,
        'primeras_consultas': primeras_consultas,
        'inicios': inicios,
        'altas': altas,
        'dias_habiles': dias_habiles,
    }


# ── Seguros enviados en el periodo ───────────────────────────────────────────

def _contar_seguros_enviados(desde, hasta):
    """Cuenta formularios de seguro con estado 'enviado' cuya fecha de envio
    (o de creacion si no hay 'enviado') cae dentro de [desde,hasta]. No hay
    una funcion de historial-por-fecha lista en seguros.py, asi que se cuenta
    a mano desde listar_registros()."""
    desde_iso, hasta_iso = desde.isoformat(), hasta.isoformat()
    n = 0
    for r in seguros.listar_registros(estado='enviado'):
        fecha = (r.get('enviado') or r.get('creado') or '')[:10]
        if desde_iso <= fecha <= hasta_iso:
            n += 1
    return n


# ── Agregacion principal ─────────────────────────────────────────────────────

def agregar(desde, hasta, scfg=None):
    """Dict estructurado con las 4 areas (+ reactivacion). CADA fuente va en
    su propio try/except: si una falla, ese bloque queda con
    {'error': True, 'detalle': ...} pero agregar() NUNCA lanza."""
    resultado = {'desde': desde.isoformat(), 'hasta': hasta.isoformat()}

    # Comercial
    try:
        resumen_stats = stats.resumen(desde=desde, hasta=hasta)
        funnel = stats.resumen_funnel(desde=desde, hasta=hasta)
        try:
            fuga = seguimiento_pc.resumen().get('pendientes', 0)
        except Exception:
            fuga = 0
        resultado['comercial'] = {
            'reservas_online': resumen_stats.get('total', 0),
            'nuevos': resumen_stats.get('nuevos', 0),
            'conocidos': resumen_stats.get('conocidos', 0),
            'por_doctor': resumen_stats.get('por_doctor', []),
            'conversion': funnel,
            'fuga_primeras_consultas': fuga,
        }
    except Exception as e:
        resultado['comercial'] = {'error': True, 'detalle': str(e)}

    # Clinico
    try:
        resultado['clinico'] = _barrido_clinico(desde, hasta, scfg)
    except Exception as e:
        resultado['clinico'] = {'error': True, 'detalle': str(e)}

    # Reputacion (NPS es acumulado/mensual, no toma rango de fechas)
    try:
        r = nps.resumen()
        resultado['reputacion'] = {
            'nps': r.get('nps'),
            'promotores': r.get('promotores', 0),
            'pasivos': r.get('pasivos', 0),
            'detractores': r.get('detractores', 0),
            'tasa_respuesta': r.get('tasa_respuesta', 0),
            'resenas_mes': r.get('resenas_mes', {}),
            'rating': r.get('rating_reciente'),
        }
    except Exception as e:
        resultado['reputacion'] = {'error': True, 'detalle': str(e)}

    # Operacion
    try:
        seguros_enviados = _contar_seguros_enviados(desde, hasta)
    except Exception:
        # Defensivo a proposito (spec): mejor 0 que romper el reporte entero.
        seguros_enviados = 0
    try:
        gastos = compras.resumen_gastos(desde=desde.isoformat(), hasta=hasta.isoformat())
    except Exception as e:
        gastos = {'error': True, 'detalle': str(e)}
    resultado['operacion'] = {
        'seguros_enviados_periodo': seguros_enviados,
        'gastos': gastos,
    }

    # Reactivacion (acumulado, no toma rango de fechas)
    try:
        resultado['reactivacion'] = reactivacion.resumen()
    except Exception as e:
        resultado['reactivacion'] = {'error': True, 'detalle': str(e)}

    return resultado


# ── Formato de numeros ───────────────────────────────────────────────────────

def _miles(n):
    """1234567 -> '1.234.567' (separador de miles a mano, sin locale)."""
    try:
        n = int(round(float(n)))
    except (TypeError, ValueError):
        return str(n) if n is not None else '—'
    signo = '-' if n < 0 else ''
    return signo + f'{abs(n):,}'.replace(',', '.')


def _fecha_legible(iso):
    try:
        d = date.fromisoformat(iso[:10])
        return d.strftime('%d-%m-%Y')
    except (TypeError, ValueError):
        return iso or '—'


# ── HTML ──────────────────────────────────────────────────────────────────

_NAVY = '#1A2E4A'
_GOLD = '#C9A84C'
_BG = '#f0f5fb'
_BORDE = '#e2e8f0'


def _titulo_seccion(texto):
    return (f'<h2 style="margin:28px 0 12px;color:{_NAVY};font-size:16px;'
            f'font-weight:700;border-bottom:2px solid {_GOLD};padding-bottom:6px;">{texto}</h2>')


def _fila_tabla(label, valor):
    return (f'<tr><td style="padding:8px 12px;color:{_NAVY};font-size:13px;'
            f'font-weight:700;border-top:1px solid {_BORDE};width:60%;">{label}</td>'
            f'<td style="padding:8px 12px;color:#1A2535;font-size:14px;'
            f'border-top:1px solid {_BORDE};text-align:right;">{valor}</td></tr>')


def _tabla(filas_html):
    return (f'<table width="100%" cellpadding="0" cellspacing="0" '
            f'style="border:1px solid {_BORDE};border-radius:8px;overflow:hidden;'
            f'border-collapse:collapse;margin:0 0 8px;">{filas_html}</table>')


def _bloque_error(nombre):
    return (f'<p style="margin:0 0 8px;color:#a94442;font-size:13px;">'
            f'No se pudo calcular {nombre} esta semana (ver logs).</p>')


def _seccion_comercial(c):
    if c.get('error'):
        return _bloque_error('el area Comercial')
    conv = c.get('conversion') or {}
    filas = (
        _fila_tabla('Reservas online', _miles(c.get('reservas_online', 0)))
        + _fila_tabla('Pacientes nuevos', _miles(c.get('nuevos', 0)))
        + _fila_tabla('Pacientes conocidos', _miles(c.get('conocidos', 0)))
        + _fila_tabla('Conversion del agendador online',
                       f"{conv.get('conversion_pct', 0)}% "
                       f"({_miles(conv.get('reservaron', 0))} de {_miles(conv.get('total_sesiones', 0))} sesiones)")
        + _fila_tabla('Primeras consultas por reencantar', _miles(c.get('fuga_primeras_consultas', 0)))
    )
    por_doctor = c.get('por_doctor') or []
    if por_doctor:
        filas += _fila_tabla('Por doctor', ' · '.join(
            f"{d.get('label', '—')}: {_miles(d.get('total', 0))}" for d in por_doctor[:6]))
    return _tabla(filas)


def _seccion_clinico(c):
    if c.get('error'):
        return _bloque_error('el area Clinica')
    filas = (
        _fila_tabla('Dias habiles del periodo', _miles(c.get('dias_habiles', 0)))
        + _fila_tabla('Pacientes atendidos', _miles(c.get('atendidos', 0)))
        + _fila_tabla('Inasistencias (no llega)', _miles(c.get('no_shows', 0)))
        + _fila_tabla('Cancelaciones', _miles(c.get('cancelaciones', 0)))
        + _fila_tabla('Primeras consultas', _miles(c.get('primeras_consultas', 0)))
        + _fila_tabla('Inicios de tratamiento', _miles(c.get('inicios', 0)))
        + _fila_tabla('Altas / fin de tratamiento', _miles(c.get('altas', 0)))
    )
    return _tabla(filas)


def _seccion_reputacion(r):
    if r.get('error'):
        return _bloque_error('el area de Reputacion')
    nps_val = r.get('nps')
    nps_txt = str(nps_val) if nps_val is not None else 'sin datos suficientes'
    tasa = r.get('tasa_respuesta') or 0
    try:
        tasa_pct = f'{round(float(tasa) * 100)}%'
    except (TypeError, ValueError):
        tasa_pct = '—'
    rating = r.get('rating')
    rating_txt = f'{rating}' if rating else 'sin datos'
    resenas_mes = r.get('resenas_mes') or {}
    resenas_txt = '—'
    if resenas_mes:
        ultimo_mes = sorted(resenas_mes.keys())[-1]
        resenas_txt = f"{resenas_mes[ultimo_mes].get('resenas', 0)} en {ultimo_mes}"
    filas = (
        _fila_tabla('NPS', nps_txt)
        + _fila_tabla('Promotores / Pasivos / Detractores',
                       f"{_miles(r.get('promotores', 0))} / {_miles(r.get('pasivos', 0))} / "
                       f"{_miles(r.get('detractores', 0))}")
        + _fila_tabla('Tasa de respuesta de la encuesta', tasa_pct)
        + _fila_tabla('Rating reciente en Google', rating_txt)
        + _fila_tabla('Resenas (ultimo mes con dato)', resenas_txt)
    )
    return _tabla(filas)


def _seccion_operacion(o):
    filas = _fila_tabla('Formularios de seguro enviados', _miles(o.get('seguros_enviados_periodo', 0)))
    gastos = o.get('gastos') or {}
    if gastos.get('error'):
        filas += _fila_tabla('Gasto del periodo', 'sin datos (ver logs)')
    else:
        filas += _fila_tabla('Gasto total del periodo', f"${_miles(gastos.get('total', 0))}")
        filas += _fila_tabla('N° de compras registradas', _miles(gastos.get('n_compras', 0)))
    return _tabla(filas)


def _seccion_reactivacion(r):
    if r.get('error'):
        return _bloque_error('el area de Reactivacion')
    filas = (
        _fila_tabla('Candidatos a reactivar', _miles(r.get('total', 0)))
        + _fila_tabla('Pendientes de contactar', _miles(r.get('pendientes', 0)))
        + _fila_tabla('Volvieron a agendar', _miles(r.get('volvio', 0)))
        + _fila_tabla('Ciclo completado', _miles(r.get('completado', 0)))
    )
    return _tabla(filas)


def render_html(kpis):
    """Arma el cuerpo HTML del reporte y lo envuelve en notify._email_layout
    (mismo sobre navy/dorado que el resto de los correos del proyecto)."""
    desde_leg = _fecha_legible(kpis.get('desde', ''))
    hasta_leg = _fecha_legible(kpis.get('hasta', ''))

    cuerpo = (
        f'<p style="margin:0 0 16px;color:#4A5568;font-size:14px;">'
        f'Resumen de la semana del <strong>{desde_leg}</strong> al <strong>{hasta_leg}</strong>.</p>'
        + _titulo_seccion('📈 Comercial')
        + _seccion_comercial(kpis.get('comercial') or {})
        + _titulo_seccion('🦷 Clinico')
        + _seccion_clinico(kpis.get('clinico') or {})
        + _titulo_seccion('⭐ Reputacion')
        + _seccion_reputacion(kpis.get('reputacion') or {})
        + _titulo_seccion('💼 Operacion')
        + _seccion_operacion(kpis.get('operacion') or {})
        + _titulo_seccion('🔁 Reactivacion')
        + _seccion_reactivacion(kpis.get('reactivacion') or {})
    )
    return notify._email_layout('Reporte semanal', cuerpo, title_tag='Reporte semanal')


def asunto(kpis):
    """'Reporte semanal — 27-07 al 02-08' (fechas dd-mm)."""
    def dm(iso):
        try:
            return date.fromisoformat((iso or '')[:10]).strftime('%d-%m')
        except (TypeError, ValueError):
            return iso or ''
    return f"Reporte semanal — {dm(kpis.get('desde', ''))} al {dm(kpis.get('hasta', ''))}"
