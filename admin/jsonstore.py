"""
jsonstore.py — El guardado de datos del proyecto, en un solo lugar.

POR QUE EXISTE
--------------
Casi todo el estado de este proyecto vive en archivos JSON en el disco
persistente de Render (la excepcion es Compras, que usa SQLite porque ahi si hay
relaciones de verdad). Nueve modulos habian reimplementado el MISMO par de
funciones, casi letra por letra:

    def _load_registro():
        if PATH.exists():
            try:
                return json.loads(PATH.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                pass
        return {...}

    def _save_registro(reg):
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(reg, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, PATH)

Nueve copias no son solo nueve veces el mismo codigo: son nueve lugares donde un
arreglo se aplica en ocho. Ya paso — `stats.py` era la copia sin lock y perdia
reservas cuando el panel borraba una mientras un paciente agendaba.

QUE APORTA ADEMAS DE AHORRAR LINEAS
-----------------------------------
1. **Escritura atomica** (tmp + os.replace) en todos, sin excepcion. Un corte a
   mitad de escritura nunca deja el archivo truncado.
2. **Lock propio** (RLock), asi el read-modify-write de `actualizar()` es
   indivisible aunque lo llamen dos hilos a la vez.
3. **Un archivo corrupto ya no se pierde en silencio.** Antes, si el JSON no
   parseaba se devolvia el default y el siguiente `save` lo pisaba: los datos se
   iban sin que nadie se enterara. Ahora el archivo malo se guarda como
   `.corrupto-<n>` antes de seguir, y queda un aviso en el log de Render.

COMO SE USA
-----------
Cada modulo declara su store y mantiene sus nombres de siempre, asi que no
cambia ni una llamada:

    _STORE = jsonstore.JsonStore(REGISTRO_PATH,
                                 default={'inscritos': {}, 'no_molestar': []},
                                 indent=2)

    def _load_registro():  return _STORE.load()
    def _save_registro(r): _STORE.save(r)

Para un read-modify-write seguro sin tomar el lock a mano:

    _STORE.actualizar(lambda reg: reg['vistos'].update({...}))
"""

import os
import json
import threading
from pathlib import Path

# Centinela para distinguir "no me pasaron el parametro" de "me pasaron None"
# (None es un valor legitimo para default_si_falta, ver confirmaciones.py).
_SIN_ESPECIFICAR = object()


class JsonStore:
    """Un archivo JSON en disco, con escritura atomica y lock propio.

    `default` es la estructura que se devuelve cuando el archivo no existe (o no
    se pudo leer). Se copia en cada `load()`, nunca se entrega la misma
    instancia — si no, dos lectores compartirian el mismo dict mutable.

    `claves` son claves de primer nivel que se garantizan presentes al leer
    (equivalente a los `setdefault` que hacian los modulos a mano), para que el
    codigo que consume el registro no tenga que defenderse de un archivo viejo
    escrito antes de que existiera esa clave.

    `default_si_falta=None` sirve para el caso de `confirmaciones.py`, donde la
    diferencia entre "archivo vacio" y "nunca se ha corrido" es informacion:
    la primera corrida solo siembra, no envia.
    """

    def __init__(self, path, default=None, indent=None, claves=None,
                 default_si_falta=_SIN_ESPECIFICAR):
        self.path = Path(path)
        self._default = {} if default is None else default
        self._indent = indent
        self._claves = dict(claves or {})
        # Si no se especifica, "archivo que falta" devuelve lo mismo que "vacio".
        self._falta = (self._default if default_si_falta is _SIN_ESPECIFICAR
                       else default_si_falta)
        self._lock = threading.RLock()

    # ── Lectura ──────────────────────────────────────────────────────────

    def load(self):
        """Devuelve el contenido, o el default si el archivo no existe/no se
        pudo leer. Nunca lanza."""
        with self._lock:
            if not self.path.exists():
                return self._copia(self._falta)
            try:
                datos = json.loads(self.path.read_text(encoding='utf-8'))
            except (ValueError, OSError) as e:
                self._respaldar_corrupto(e)
                return self._copia(self._default)
            return self._con_claves(datos)

    def _con_claves(self, datos):
        if isinstance(datos, dict):
            for k, v in self._claves.items():
                datos.setdefault(k, self._copia(v))
        return datos

    @staticmethod
    def _copia(valor):
        # Copia profunda barata: el default siempre es JSON-serializable.
        return json.loads(json.dumps(valor)) if valor is not None else None

    def _respaldar_corrupto(self, error):
        """Un JSON que no parsea NO se pisa. Se aparta con otro nombre para
        poder recuperarlo a mano; si se dejara pasar, el siguiente save lo
        sobrescribe con el default y los datos se pierden en silencio."""
        try:
            destino = self.path.with_suffix(self.path.suffix + '.corrupto')
            n = 1
            while destino.exists():
                n += 1
                destino = self.path.with_suffix(f'{self.path.suffix}.corrupto-{n}')
            os.replace(self.path, destino)
            print(f'[jsonstore] {self.path.name} ilegible ({error!r}). '
                  f'Se aparto como {destino.name} y se sigue con los valores por '
                  f'defecto. REVISAR: puede haber datos que recuperar.')
        except OSError as e:
            print(f'[jsonstore] {self.path.name} ilegible ({error!r}) y ademas no '
                  f'se pudo apartar: {e!r}')

    # ── Escritura ────────────────────────────────────────────────────────

    def save(self, datos):
        """Escritura atomica: se escribe un temporal y se renombra encima. Asi
        nadie lee nunca un archivo a medio escribir, y un corte de luz deja el
        archivo anterior intacto en vez de uno truncado."""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + '.tmp')
            tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=self._indent),
                           encoding='utf-8')
            os.replace(tmp, self.path)

    def actualizar(self, fn):
        """Read-modify-write indivisible. `fn` recibe los datos y los modifica en
        sitio (o devuelve la version nueva). Devuelve lo que quedo guardado.

        Usar esto en vez de load()+save() sueltos cuando el valor nuevo depende
        del viejo: entre un load y un save sin lock, otro hilo puede colarse y su
        escritura se pierde."""
        with self._lock:
            datos = self.load()
            resultado = fn(datos)
            if resultado is not None:
                datos = resultado
            self.save(datos)
            return datos

    @property
    def lock(self):
        """Para el codigo que ya toma un lock a mano alrededor de un bloque mas
        grande que un solo load/save."""
        return self._lock
