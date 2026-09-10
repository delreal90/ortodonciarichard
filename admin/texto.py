"""
texto.py - Normalizacion de texto para BUSCAR y COMPARAR.

POR QUE EXISTE
--------------
Un buscador por nombre tiene que encontrar "Núñez" cuando alguien escribe
"nunez": nadie teclea tildes en un campo de busqueda, y la mitad de los
apellidos chilenos las llevan.

Quitar tildes ya estaba escrito SIETE veces en este repo, cada una con su
propia variacion:

    control_dental.py   cumpleanos.py   dentidesk.py   fairest.py
    fichas.py (x2)      genero.py

Escribir la octava dentro de informe_pc.py era exactamente el error que
documenta el encabezado de CLAUDE.md: el mismo helper copiado hasta que las
copias divergen y un arreglo se aplica en seis de siete lugares.

⚠️ LAS SIETE COPIAS SIGUEN AHI. Migrarlas es un commit APARTE: cada una vive
en codigo probado en produccion y no puede viajar junto a una funcionalidad
nueva. Este modulo es la casa donde tienen que llegar, no un octavo inquilino.

CEREBRO SIN RED: solo transforma strings.
"""

import unicodedata


def sin_tildes(texto):
    """Minusculas y sin tildes, para comparar. 'Núñez' -> 'nunez'.

    Descompone en NFKD y descarta las marcas combinantes, que es lo que hace
    que la tilde sea un caracter aparte de la letra. No toca la enie: 'ñ'
    tambien se descompone, asi que 'Nuñez' y 'Nunez' colapsan igual -- que es
    justo lo que se quiere al buscar, aunque no lo seria al imprimir.
    """
    s = unicodedata.normalize('NFKD', (texto or '').strip().lower())
    return ''.join(c for c in s if not unicodedata.combining(c))
