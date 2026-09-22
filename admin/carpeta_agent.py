r"""
carpeta_agent.py - Ayudante local: abre la carpeta del paciente en el Explorador.

Corre en CADA PC donde se use el F2 (box, recepcion, asistente dental). El F2 le
pregunta por el paciente de la cita abierta, este busca en `\\DIGITAL1\Registros
Pacientes` y devuelve las candidatas; al elegir una, lanza el Explorador.

    F2 (content.js) -> background.js -> http://127.0.0.1:8777 -> explorer.exe

POR QUE UN PROGRAMA LOCAL Y NO EL BACKEND
------------------------------------------
Dos razones, y ninguna es de comodidad:

1. Una extension de Chrome NO puede abrir el Explorador de Windows. Algo nativo
   tiene que correr en el PC, si o si.
2. El Explorador tiene que abrirse en el PC donde alguien apreto F2. Una cola en
   Render (el patron de `print_agent.py`) abriria la ventana en el PC
   equivocado. Por eso este habla directo con la extension, no con el backend.

Consecuencia: este sistema NO le escribe nada a Render. Lo unico que persiste es
`carpeta_elegidas.json` (rut -> carpeta que una persona eligio), y vive LOCAL a
cada PC a proposito: son nombres de pacientes y el repo es publico.

--------------------------------------------------------------------------------
DEPENDENCIAS: ninguna fuera de la biblioteca estandar de Python.

CONFIGURACION (variables de entorno):
    CARPETA_RAIZ   = \\DIGITAL1\Registros Pacientes  (donde estan las carpetas)
    CARPETA_PUERTO = 8777
    CARPETA_TOKEN  = la llave que comparte con la extension. Si no se define, se
                     genera sola en `carpeta_token.txt` la primera vez.

USO:
    python carpeta_agent.py                        -> queda escuchando
    python carpeta_agent.py --probar "Miranda Araya" "Matias"
    python carpeta_agent.py --token                -> muestra la llave a pegar
                                                      en config.js de la extension

Para que arranque solo con Windows: `carpeta_agent_instalar.bat`.
--------------------------------------------------------------------------------
"""

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent))

import carpetas   # noqa: E402

AQUI = Path(__file__).parent

RAIZ = os.environ.get('CARPETA_RAIZ', r'\\DIGITAL1\Registros Pacientes')
PUERTO = int(os.environ.get('CARPETA_PUERTO', '8777'))

ARCHIVO_TOKEN = AQUI / 'carpeta_token.txt'
ARCHIVO_ELEGIDAS = AQUI / 'carpeta_elegidas.json'
ARCHIVO_LOG = AQUI / 'carpeta_agent.log'

# Los nombres de carpeta cambian poco; la red cuesta ~200 ms por carpeta `letra *`.
_CACHE_TTL = 60
_cache = {}


# ── Log ──────────────────────────────────────────────────────────────────────
# Con `pythonw.exe` no hay consola, asi que un print no se ve en ninguna parte.
# Sin esto, "no funciona" no tiene como diagnosticarse.

def log(msg, consola=True):
    """Diagnostico: al archivo de log, y a la consola salvo que se pida callar.

    ⚠️ `consola=False` no es un lujo. `--token` existe para que el instalador
    capture la llave, asi que en ese modo este programa tiene que ser MUDO en los
    dos canales. Dos fallos reales, los dos solo en un PC virgen (en desarrollo
    el archivo de la llave ya existia, asi que no se generaba ninguna y no habia
    nada que avisar):

      1. Con el aviso en STDOUT, el instalador metia "Llave nueva generada en
         ..." ENTERO dentro de config.js. Con saltos de linea y barras invertidas
         adentro, el archivo dejaba de ser JavaScript valido y la extension se
         quedaba sin NINGUNA configuracion -- ni siquiera el token del backend.
      2. Mandarlo a STDERR no alcanzo: PowerShell, con `$ErrorActionPreference =
         'Stop'`, convierte la salida de error de un .exe en excepcion, y el
         instalador moria en la primera instalacion.

    El aviso igual queda registrado en `carpeta_agent.log`, que es donde sirve.
    """
    linea = '%s  %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    if consola:
        print(linea, file=sys.stderr)
    try:
        if ARCHIVO_LOG.exists() and ARCHIVO_LOG.stat().st_size > 1_000_000:
            ARCHIVO_LOG.unlink()
        with open(ARCHIVO_LOG, 'a', encoding='utf-8') as fh:
            fh.write(linea + '\n')
    except OSError:
        pass


# ── Llave ────────────────────────────────────────────────────────────────────

def token():
    """La llave compartida con la extension. Se genera sola la primera vez.

    Se genera en vez de pedirla porque una llave que hay que inventar termina
    siendo '1234' o vacia en el PC donde alguien tenia prisa.
    """
    de_env = os.environ.get('CARPETA_TOKEN', '').strip()
    if de_env:
        return de_env
    try:
        guardada = ARCHIVO_TOKEN.read_text(encoding='utf-8').strip()
        if guardada:
            return guardada
    except OSError:
        pass
    nueva = secrets.token_hex(16)
    ARCHIVO_TOKEN.write_text(nueva, encoding='utf-8')
    # ⚠️ Solo al log: quien llama puede estar capturando la llave. Ver log().
    log('Llave nueva generada en %s' % ARCHIVO_TOKEN, consola=False)
    return nueva


# ── Memoria de elecciones ────────────────────────────────────────────────────

def _clave_rut(rut):
    return ''.join(c for c in (rut or '') if c.isalnum()).lower()


def _elegidas():
    try:
        with open(ARCHIVO_ELEGIDAS, encoding='utf-8') as fh:
            datos = json.load(fh)
        return datos if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        return {}


def recordar(rut, ruta):
    clave = _clave_rut(rut)
    if not clave:
        return
    datos = _elegidas()
    datos[clave] = ruta
    try:
        with open(ARCHIVO_ELEGIDAS, 'w', encoding='utf-8') as fh:
            json.dump(datos, fh, ensure_ascii=False, indent=1)
    except OSError as e:
        log('No se pudo recordar la eleccion: %s' % e)


# ── Lectura del servidor ─────────────────────────────────────────────────────

def _subcarpetas(ruta):
    """Los nombres de las subcarpetas de `ruta`. UN SOLO NIVEL.

    ⚠️ NUNCA recursivo. La raiz tiene `.tmp.driveupload` con 272.417 archivos
    (resto de la app vieja de Google Drive, la que reemplazo `respaldo-digital1`):
    un barrido recursivo deja el ayudante colgado sin que nadie entienda por que.
    """
    ahora = time.time()
    hit = _cache.get(ruta)
    if hit and ahora - hit[0] < _CACHE_TTL:
        return hit[1]
    nombres = []
    try:
        with os.scandir(ruta) as it:
            for e in it:
                try:
                    if e.is_dir():
                        nombres.append(e.name)
                except OSError:
                    continue
    except OSError as e:
        log('No se pudo leer %s: %s' % (ruta, e))
        return []
    _cache[ruta] = (ahora, nombres)
    return nombres


def alcanzable():
    try:
        return os.path.isdir(RAIZ)
    except OSError:
        return False


def buscar(nombre, apellido, rut=''):
    """Candidatas para un paciente, con su ruta absoluta."""
    items = []      # [(nombre_carpeta, ruta)]
    for letra in carpetas.carpetas_a_mirar(apellido):
        base = os.path.join(RAIZ, letra)
        for n in _subcarpetas(base):
            items.append((n, os.path.join(base, n)))

    # La raiz tiene algun paciente suelto (una carpeta con nombre de persona)
    # entre carpetas de trabajo ('z- Varios', 'Carpeta 1'). Son 55 entradas: sale
    # gratis mirarlas, y el puntaje descarta lo que no es un nombre de persona.
    for n in _subcarpetas(RAIZ):
        if n.lower().startswith('letra ') or n.startswith('.'):
            continue
        items.append((n, os.path.join(RAIZ, n)))

    r = carpetas.rankear(nombre, apellido, [n for n, _ in items])
    candidatas = [{'carpeta': c['carpeta'],
                   'ruta': items[c['indice']][1],
                   'puntaje': c['puntaje']} for c in r['candidatas']]
    confiable = r['confiable']

    # Lo que una persona ya eligio para este RUT manda sobre el puntaje.
    recordada = _elegidas().get(_clave_rut(rut))
    if recordada and os.path.isdir(recordada):
        candidatas = ([c for c in candidatas if c['ruta'] == recordada] or
                      [{'carpeta': os.path.basename(recordada),
                        'ruta': recordada, 'puntaje': None}]) + \
                     [c for c in candidatas if c['ruta'] != recordada]
        confiable = True

    letras = carpetas.carpetas_a_mirar(apellido)
    return {
        'ok': True,
        'candidatas': candidatas,
        'confiable': confiable,
        'letra': letras[0] if letras else '',
        'letra_ruta': os.path.join(RAIZ, letras[0]) if letras else '',
    }


# ── Abrir ────────────────────────────────────────────────────────────────────

def _dentro_de_raiz(ruta):
    """True si `ruta` queda dentro de RAIZ. Guarda contra un traversal.

    Molde de `informe_pc._dentro_de_imagenes()`. El F2 manda de vuelta una ruta
    que este mismo proceso emitio, pero eso no se puede dar por sentado: lo que
    llega por HTTP es una peticion, no una promesa.
    """
    if not ruta:
        return False
    try:
        base = os.path.normcase(os.path.realpath(RAIZ))
        destino = os.path.normcase(os.path.realpath(ruta))
    except (OSError, ValueError):
        return False
    return destino == base or destino.startswith(base + os.sep)


_CLASES_EXPLORADOR = ('CabinetWClass', 'ExploreWClass')


def _ventanas_explorador():
    """Los handles de las ventanas del Explorador que hay abiertas ahora."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    encontradas = []
    buf = ctypes.create_unicode_buffer(256)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value in _CLASES_EXPLORADOR:
                encontradas.append(hwnd)
        return True

    user32.EnumWindows(_cb, 0)
    return encontradas


def _al_frente(hwnd):
    """Trae una ventana al frente saltandose el bloqueo de foco de Windows.

    ⚠️ POR QUE HACE FALTA: este ayudante arranca con Windows y nunca recibe un
    clic, asi que para Windows es un proceso de fondo y NO tiene permiso para
    robarle el foco a lo que la persona esta usando. En vez de traer la ventana
    al frente, parpadea el boton de la barra de tareas -- la carpeta se abre,
    pero queda abajo. Y el permiso depende de `ForegroundLockTimeout`, que viene
    con valores distintos en cada PC: por eso en unos salia al frente y en otros
    no, con el mismo codigo.

    El truco es `AttachThreadInput`: enganchandose a la cola de entrada del hilo
    que SI tiene el foco, Windows trata la peticion como si viniera de el. Es la
    via documentada para esto, y no cambia ninguna configuracion del PC (poner
    `ForegroundLockTimeout` en 0 lo arreglaria igual, pero le sacaria la
    proteccion a TODOS los programas del PC, no solo a este).
    """
    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    SW_RESTORE = 9

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    frente = user32.GetForegroundWindow()
    hilo_frente = user32.GetWindowThreadProcessId(frente, None)
    hilo_propio = kernel32.GetCurrentThreadId()

    enganchado = False
    if hilo_frente and hilo_frente != hilo_propio:
        enganchado = bool(user32.AttachThreadInput(hilo_propio, hilo_frente, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if enganchado:
            user32.AttachThreadInput(hilo_propio, hilo_frente, False)


def _traer_carpeta_al_frente(ruta, previas):
    """Espera a que aparezca la ventana del Explorador y la sube. Best-effort.

    ⚠️ Nada de esto puede impedir que la carpeta se abra: si algo falla, la
    ventana ya esta abierta y lo unico que se pierde es que salte al frente.
    """
    try:
        nombre = os.path.basename(os.path.normpath(ruta))
        for _ in range(30):                      # hasta ~3 s
            time.sleep(0.1)
            actuales = _ventanas_explorador()
            nuevas = [h for h in actuales if h not in previas]
            if nuevas:
                _al_frente(nuevas[-1])
                return
        # El Explorador reusa una ventana ya abierta en esa carpeta en vez de
        # crear otra: entonces no hay ninguna "nueva" que subir, hay que
        # reconocerla por el nombre de la carpeta en el titulo.
        import ctypes
        buf = ctypes.create_unicode_buffer(512)
        for hwnd in _ventanas_explorador():
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
            if buf.value.strip().lower() == nombre.strip().lower():
                _al_frente(hwnd)
                return
    except Exception as e:                       # noqa: BLE001
        log('No se pudo traer la ventana al frente: %s' % e)


def abrir(ruta):
    """Lanza el Explorador en esa carpeta. Devuelve (ok, error)."""
    if not _dentro_de_raiz(ruta):
        return False, 'Esa carpeta esta fuera de %s' % RAIZ
    if not os.path.isdir(ruta):
        return False, 'La carpeta ya no existe'

    try:
        previas = _ventanas_explorador()
    except Exception:                            # noqa: BLE001
        previas = []

    try:
        # ⚠️ Lista de argumentos, NUNCA una linea de comandos armada por texto:
        # los nombres traen espacios, puntos y comillas.
        # (explorer.exe devuelve 1 aunque funcione; por eso no se espera ni se
        # mira el codigo de salida.)
        subprocess.Popen(['explorer', os.path.normpath(ruta)])
    except OSError as e:
        return False, str(e)

    # En un hilo aparte para contestarle al F2 al tiro: la persona no tiene por
    # que esperar a que aparezca la ventana.
    threading.Thread(target=_traer_carpeta_al_frente,
                     args=(ruta, previas), daemon=True).start()
    return True, ''


# ── Servidor ─────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = 'CarpetaAgent/1.0'

    def log_message(self, fmt, *args):      # el log lo lleva log(), no stderr
        pass

    def _responder(self, codigo, datos):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode('utf-8')
        self.send_response(codigo)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(cuerpo)))
        # ⚠️ A PROPOSITO no se manda `Access-Control-Allow-Origin`. Asi ninguna
        # pagina web puede leer lo que responde este ayudante. La extension si
        # puede: con `host_permissions` sobre 127.0.0.1 se salta CORS.
        self.end_headers()
        self.wfile.write(cuerpo)

    def _autorizado(self):
        if self.headers.get('X-Carpeta-Token', '') == token():
            return True
        self._responder(403, {'ok': False, 'error': 'Llave incorrecta'})
        return False

    def do_GET(self):
        if not self._autorizado():
            return
        partes = urlparse(self.path)
        q = parse_qs(partes.query)
        uno = lambda k: (q.get(k) or [''])[0]

        if partes.path == '/estado':
            self._responder(200, {'ok': True, 'raiz': RAIZ,
                                  'alcanzable': alcanzable()})
        elif partes.path == '/buscar':
            if not alcanzable():
                self._responder(200, {'ok': False, 'error': 'no_alcanzable',
                                      'raiz': RAIZ})
                return
            self._responder(200, buscar(uno('nombre'), uno('apellido'), uno('rut')))
        else:
            self._responder(404, {'ok': False, 'error': 'Ruta desconocida'})

    def do_POST(self):
        if not self._autorizado():
            return
        if urlparse(self.path).path != '/abrir':
            self._responder(404, {'ok': False, 'error': 'Ruta desconocida'})
            return
        try:
            n = int(self.headers.get('Content-Length') or 0)
            cuerpo = json.loads(self.rfile.read(n) or b'{}')
        except (ValueError, OSError):
            self._responder(400, {'ok': False, 'error': 'Cuerpo invalido'})
            return

        ruta = (cuerpo.get('ruta') or '').strip()
        ok, error = abrir(ruta)
        if ok:
            log('Abierta: %s' % ruta)
            recordar(cuerpo.get('rut', ''), ruta)
            self._responder(200, {'ok': True})
        else:
            log('No se pudo abrir "%s": %s' % (ruta, error))
            self._responder(400, {'ok': False, 'error': error})


def servir():
    log('Ayudante de carpetas en http://127.0.0.1:%d  (raiz: %s)' % (PUERTO, RAIZ))
    if not alcanzable():
        log('AVISO: no se alcanza la raiz. El F2 lo va a decir en pantalla.')
    # ⚠️ 127.0.0.1, nunca 0.0.0.0: esto no se asoma a la red de la clinica.
    ThreadingHTTPServer(('127.0.0.1', PUERTO), Handler).serve_forever()


def main():
    args = sys.argv[1:]
    if args and args[0] == '--token':
        print(token())
        return
    if args and args[0] == '--probar':
        if len(args) < 3:
            print('Uso: python carpeta_agent.py --probar "Apellidos" "Nombres"')
            return
        t0 = time.time()
        r = buscar(args[2], args[1])
        print('raiz: %s  (alcanzable: %s)' % (RAIZ, alcanzable()))
        print('mirando en: %s' % r.get('letra'))
        print('%d candidata(s) en %.0f ms  |  confiable: %s'
              % (len(r['candidatas']), 1000 * (time.time() - t0), r['confiable']))
        for c in r['candidatas']:
            print('   %-45s  %s' % (c['carpeta'], c['puntaje']))
        return
    servir()


if __name__ == '__main__':
    main()
