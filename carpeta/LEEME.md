# Carpeta del paciente — diseño exterior

Carpeta que se entrega al paciente al final de la primera consulta, junto con el
informe de evaluación y el presupuesto. Se imprime en **un solo pliego** que se
dobla por la mitad: el panel derecho queda de **tapa** y el izquierdo de
**contratapa**.

Hay **tres versiones para elegir**. Las tres tienen exactamente las mismas medidas,
el mismo contenido y el mismo lema; cambia el aspecto.

| | | |
|---|---|---|
| **A · Navy** | Azul de la marca a todo sangrado, logo centrado, mucho aire. | La más sobria. Es la que más "carpeta de clínica cara" se ve, y la que más tinta gasta. |
| **B · Clara** | Fondo claro con un pie navy que cruza el doblez. Logo en su **celeste original**. | La única donde se ve el color propio de la marca. La más barata de imprimir y **no se le marcan las huellas dactilares**. |
| **C · Barras** | Navy con un pie de barras celestes a sangre y el logo alineado a la izquierda. | La que más carácter propio tiene: las barras salen del monograma del logo. |

---

## Qué mandarle a la imprenta

Reemplaza `<X>` por la versión elegida: `A-navy`, `B-clara` o `C-barras`.

| Archivo | Para qué |
|---|---|
| **`carpeta-<X>-marcas.pdf`** | **El que se manda.** 499 × 349 mm: el arte con sangrado más las marcas de corte y de doblez en el margen. |
| `carpeta-<X>.pdf` | El mismo arte, exacto a 479 × 329 mm y sin marcas. Úsalo si la imprenta lo pide "al sangrado, sin marcas". |
| `carpeta-<X>-guias.pdf` | **No es para imprimir.** Versión de revisión con la línea de corte, el doblez y la zona segura dibujados. |
| `vista-<X>.png` · `vista-<X>-tapa.png` · `vista-<X>-contratapa.png` | Para mirar en pantalla o mandar por WhatsApp. |

## Medidas (iguales en las tres)

```
Pliego con sangrado   479,0 × 329,0 mm
Corte final           473,0 × 323,0 mm      (sangrado de 3 mm por lado)
Doblez                al centro, x = 239,5 mm
Paneles resultantes   236,5 × 323 mm cada uno
Márgenes del contenido  24 mm desde el corte · 22 mm desde el doblez
```

El fondo es **a todo sangrado**: llega hasta el borde del pliego por los cuatro
lados. No hay ningún texto ni logo cerca del corte ni del doblez.

## Colores

| | HEX | Uso |
|---|---|---|
| Navy | `#1A2E4A` | fondo (A y C) · pie (B) |
| Celeste de marca | `#4B8FCC` | logo (B) · barras (C) |
| Dorado | `#C9A84C` | filetes |
| Dorado oscuro | `#8F7124` | títulos sobre fondo claro (solo B) |
| Fondo claro | `#F0F5FB` | fondo (solo B) |

⚠️ **Los PDF van en RGB.** Si la imprenta trabaja en CMYK, que ellos hagan la
conversión con su perfil, y **pídeles una prueba de color del azul**: un navy
oscuro y saturado como este se apaga o se va a violeta según el papel y el
perfil. Si quieren un valor de partida, `#1A2E4A` ≈ **C93 M78 Y42 K38**.

En la versión **C** las barras van en **colores planos ya mezclados** con el
navy, no con transparencia: una capa translúcida sobre un fondo sólido obliga a
la imprenta a aplanar transparencias, y ahí es donde aparecen los saltos de tono.

## Recomendaciones de impresión

- **Papel:** couché mate de 300 g o más.
- **Laminado mate:** imprescindible en **A** y **C** (fondo oscuro: sin laminado
  las huellas dactilares se ven en cuanto la toman). En **B** es opcional.
- **Hendido, no solo doblez:** con 300 g hay que **hender antes de doblar**, o el
  color se quiebra en la línea del lomo y queda una raya blanca. Crítico en A y C.
- **Si le quieren poner lomo** (para que entre más papel) o bolsillos troquelados:
  se puede sin rehacer el diseño; en las tres el fondo cruza el doblez, así que
  solo hay que correr los dos paneles. Avísame la medida del lomo.

---

## Antes de mandar a imprimir — 3 cosas que confirmar

1. **El dominio.** La tapa y el QR usan `ortodonciarichard.cl`. Confirmar que el
   DNS ya está apuntando (en `CLAUDE.md` figuraba como pendiente en nic.cl).
   Un QR impreso que no lleva a ninguna parte no se arregla después.
2. **El QR** apunta a `https://www.ortodonciarichard.cl/#agendar`. Escanéalo con
   el teléfono desde `vista-<X>-contratapa.png` antes de aprobar.
3. **Las asociaciones van como texto, no como logo.** AAO y WFO tienen logos a
   color con degradado que no se pueden pasar a un solo color sobre fondo azul sin
   deformarlos, y ambas tienen reglas de uso de marca. Por eso la contratapa dice
   *"Miembros de American Association of Orthodontists · World Federation of
   Orthodontists · Sociedad de Ortodoncia y Ortopedia Dentomaxilofacial de Chile"*.
   Si los quieres igual como logos, la salida limpia es una franja blanca abajo con
   los tres a color.

---

## Cómo cambiar algo

Cada versión es un HTML de texto plano que se edita con cualquier editor:
`carpeta-A-navy.html`, `carpeta-B-clara.html`, `carpeta-C-barras.html`.
Los tamaños están en milímetros, así que lo que se ve es lo que se imprime.

```bash
cd carpeta && python generar.py
```

Eso regenera todo. Para una sola versión: `python generar.py B`.
Los PDF los produce Chrome en modo headless, con el texto **vectorial** y las
tipografías incrustadas (no son imágenes).

Para verlo en pantalla mientras editas, abre el HTML con doble clic; agregarle
`?guias` a la dirección muestra las líneas de corte, doblez y zona segura.

⚠️ **`assets/base.css` es la geometría compartida** — pliego, sangrado, doblez y
márgenes. Si se toca, cambia en las tres a la vez, que es justamente lo que se
busca: las tres versiones tienen que ser intercambiables ante la imprenta.

### El lema

Va en **caja mixta y en Playfair Display**, la serif del sitio, no en mayúsculas
espaciadas como el resto de las líneas chicas. Un lema es una frase que la clínica
dice de sí misma; en mayúsculas con tracking ancho se lee como un rótulo de
categoría. De paso, es lo único de la tapa que usa la serif de la marca.

### Qué hay en `assets/`

- `logo-blanco.png` / `logo-celeste.png` — el logo recoloreado desde
  `images/logo-png.png`. No se editan a mano: los rehace `generar.py`.
- `qr-agenda.png` — QR de la agenda online. Para cambiar la dirección, edita
  `URL_QR` en `generar.py` y corre `python generar.py --assets`.
- `fuentes.css` — Playfair Display e Inter (licencia OFL, las mismas del sitio),
  reducidas al juego latino e incrustadas en el archivo. Van incrustadas porque
  Chrome bloquea la carga de fuentes cuando el HTML se abre con doble clic.

### El motivo de barras

En **A** son cinco barras que se desvanecen abajo a la derecha; en **C** son el pie
a sangre. En los dos casos **están dibujadas, no son el logo recortado**: llevan la
misma proporción de barra y espacio que el monograma. Se probó poniendo el logo real
de marca de agua y se veía como bloques sueltos, sin que se leyera la "R".
