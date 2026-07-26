"""
avisos.py — Lo que comparten los tres sistemas que le escriben al paciente.

QUE SISTEMAS
------------
- `recaptacion.py`    — WhatsApp de "le toca su control", lo dispara la asistente.
- `control_dental.py` — email cada 6 meses al paciente con aparatos, automatico.
- `nps.py`            — encuesta de satisfaccion por WhatsApp tras la atencion.

Se escribieron uno con el molde del anterior (recaptacion copio a
recordatorios_wa, control_dental copio a recaptacion, nps copio a los dos), asi
que comparten contrato y andamiaje. Lo que NO comparten —las guardas de negocio
de cada uno— se queda en su modulo: son justamente lo que los distingue.

EL CONTRATO DE `evaluar()`
--------------------------
Los tres exponen `evaluar(rut, ...)` que devuelve:
  - `None`  -> se puede enviar.
  - `{'motivo', 'detalle', 'puede_forzar'}` -> hay que bloquear.

`detalle` es texto que LEE UNA PERSONA (la asistente, en el panel del F2), no un
log: por eso dice "ya tiene hora agendada el martes 8 de agosto con el Dr. Vial"
y no un slug. `puede_forzar` decide si el F2 muestra el boton "Enviar igual".

LA REGLA QUE NO SE NEGOCIA
--------------------------
`no_molestar` se evalua SIEMPRE PRIMERO y SIEMPRE con `puede_forzar=False`. Es el
opt-out del paciente: ninguna otra consideracion lo salta, ni un override manual
desde el F2. Si algun dia se agrega un sistema de avisos nuevo, que herede esto
gratis es la razon principal de que este archivo exista.

POR QUE COMPOSICION Y NO HERENCIA
---------------------------------
Los tres modulos tienen ciclos de vida muy distintos (uno se dispara a mano, otro
por barrido de agenda, otro por atencion terminada). Una clase base que los
abarcara terminaria llena de ganchos vacios. Aca se comparte solo lo que de
verdad es igual: la normalizacion del RUT, la lista de no molestar y el armado
del contrato.
"""

import dentidesk


def rut_key(rut):
    """Clave canonica de un paciente en los registros.

    Distintos formatos del MISMO rut (con o sin puntos, con o sin guion,
    minuscula o mayuscula en la K) tienen que caer en la misma entrada — si no,
    un paciente marcado "no molestar" como 17.406.985-9 seguiria recibiendo
    mensajes cuando el sistema lo lea como 174069859.

    El fallback al string tal cual importa: si viniera vacio o algo que no es un
    RUT, es preferible una clave rara pero estable a colapsar todos los casos
    raros en la misma clave vacia."""
    return dentidesk.limpiar_rut(rut) or (rut or '').strip()


def bloqueo(motivo, detalle, puede_forzar):
    """Arma la respuesta de `evaluar()` cuando hay que bloquear."""
    return {'motivo': motivo, 'detalle': detalle, 'puede_forzar': puede_forzar}


def primera_guarda(guardas):
    """Corre las guardas EN ORDEN y devuelve el primer bloqueo, o None si
    ninguna bloquea. El orden es parte del contrato: no_molestar va primero
    siempre, y despues las que puedan forzarse.

    `guardas` es una lista de callables sin argumentos que devuelven None o un
    dict de bloqueo. Se evaluan perezosamente: una guarda cara (por ejemplo la
    que consulta DentiDesk por citas futuras) no corre si una anterior ya
    bloqueo."""
    for guarda in guardas:
        resultado = guarda()
        if resultado:
            return resultado
    return None


class ListaNoMolestar:
    """El opt-out del paciente, guardado bajo la clave 'no_molestar' del registro.

    Se construye con las funciones de carga y guardado del modulo y su lock, para
    que las escrituras se serialicen con el resto de las operaciones sobre el
    mismo archivo.
    """

    def __init__(self, load_registro, save_registro, lock, clave='no_molestar'):
        self._load = load_registro
        self._save = save_registro
        self._lock = lock
        self._clave = clave

    def agregar(self, rut):
        clave = rut_key(rut)
        with self._lock:
            reg = self._load()
            lista = reg.setdefault(self._clave, [])
            if clave not in lista:
                lista.append(clave)
            self._save(reg)
            return lista

    def quitar(self, rut):
        clave = rut_key(rut)
        with self._lock:
            reg = self._load()
            lista = reg.setdefault(self._clave, [])
            if clave in lista:
                lista.remove(clave)
            self._save(reg)
            return lista

    def listar(self):
        return list(self._load().get(self._clave) or [])

    def contiene(self, rut):
        return rut_key(rut) in (self._load().get(self._clave) or [])

    def guarda(self, detalle):
        """Devuelve una guarda lista para `primera_guarda()`. `detalle` es el
        texto que vera la asistente, distinto en cada sistema.

        puede_forzar es SIEMPRE False: el opt-out del paciente no se salta."""
        def _guarda(rut):
            if self.contiene(rut):
                return bloqueo('no_molestar', detalle, False)
            return None
        return _guarda
