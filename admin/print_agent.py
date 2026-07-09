"""
print_agent.py — Agente de impresión de etiquetas (Ortodoncia Richard)

Corre en el PC SIEMPRE-ENCENDIDO de la clínica (el mismo del bridge de WhatsApp),
con la etiquetadora térmica conectada por USB. Hace polling al backend (Render) por
etiquetas pendientes, genera cada etiqueta (QR + nombre del producto + código) y la
manda a imprimir. Mismo patrón de "cola + polling" que la tablet de consentimientos:
no necesita abrir puertos ni IP fija — el PC pregunta hacia afuera.

  Web app → encola etiqueta → backend (cola_impresion) → ESTE agente la imprime sola.

────────────────────────────────────────────────────────────────────────────────
DEPENDENCIAS (solo en el PC de la clínica, NO en Render):
    pip install requests segno pillow pywin32
    (pywin32 solo en Windows; en otros SO el agente puede correr en modo --guardar)

CONFIGURACIÓN (variables de entorno o edita los valores por defecto abajo):
    COMPRAS_API   = https://ortodonciarichard.onrender.com   (backend)
    PRINT_TOKEN   = <mismo valor que la env PRINT_TOKEN de Render>
    PRINT_IMPRESORA = nombre exacto de la impresora (vacío = impresora por defecto)
    PRINT_INTERVALO = segundos entre chequeos (por defecto 8)

USO:
    python print_agent.py            → corre en loop, imprimiendo lo que llegue
    python print_agent.py --test     → genera una etiqueta de ejemplo a archivo (no imprime)
    python print_agent.py --guardar  → en vez de imprimir, guarda cada etiqueta como PNG

Para dejarlo corriendo al encender el PC: crear un .bat (ver INSTALAR abajo) igual
que "Iniciar WhatsApp Bridge.bat".
────────────────────────────────────────────────────────────────────────────────
"""

import os
import io
import sys
import time

import requests

API = os.environ.get('COMPRAS_API', 'https://ortodonciarichard.onrender.com').rstrip('/')
PRINT_TOKEN = os.environ.get('PRINT_TOKEN', '')
IMPRESORA = os.environ.get('PRINT_IMPRESORA', '')          # '' = impresora por defecto
INTERVALO = int(os.environ.get('PRINT_INTERVALO', '8'))
GUARDAR_DIR = os.environ.get('PRINT_GUARDAR_DIR', 'etiquetas')

# Tamaño de la etiqueta en píxeles (para ~50x30 mm a ~203 dpi de una térmica típica).
ANCHO, ALTO = 400, 240


def _headers():
    return {'X-Print-Token': PRINT_TOKEN} if PRINT_TOKEN else {}


def generar_etiqueta_png(codigo, nombre, unidad=''):
    """Compone la etiqueta (QR + texto) y devuelve los bytes PNG."""
    import segno
    from PIL import Image, ImageDraw, ImageFont

    # QR del código
    qr_buf = io.BytesIO()
    segno.make(codigo, error='m').save(qr_buf, kind='png', scale=5, border=1)
    qr_buf.seek(0)
    qr = Image.open(qr_buf).convert('L')
    qr = qr.resize((ALTO - 40, ALTO - 40), Image.NEAREST)

    lienzo = Image.new('L', (ANCHO, ALTO), 255)
    lienzo.paste(qr, (10, 20))

    draw = ImageDraw.Draw(lienzo)
    try:
        f_big = ImageFont.truetype('arialbd.ttf', 26)
        f_small = ImageFont.truetype('arial.ttf', 18)
    except Exception:
        f_big = ImageFont.load_default()
        f_small = ImageFont.load_default()

    x = ALTO - 10  # texto a la derecha del QR
    # nombre (envuelto a 2 líneas si es largo)
    nombre = (nombre or '').strip()
    lineas = []
    palabra, linea = nombre.split(), ''
    for w in palabra:
        if len(linea + ' ' + w) > 16 and linea:
            lineas.append(linea); linea = w
        else:
            linea = (linea + ' ' + w).strip()
    if linea:
        lineas.append(linea)
    y = 24
    for ln in lineas[:2]:
        draw.text((x, y), ln, font=f_big, fill=0); y += 30
    if unidad:
        draw.text((x, y + 4), f'({unidad})', font=f_small, fill=0); y += 26
    draw.text((x, ALTO - 34), codigo, font=f_small, fill=0)

    out = io.BytesIO()
    lienzo.save(out, format='PNG')
    return out.getvalue()


def imprimir_png(png_bytes):
    """Imprime la etiqueta en la impresora Windows (respeta el driver/tamaño de la
    etiquetadora). Requiere pywin32 + Pillow."""
    import win32print
    import win32ui
    from PIL import Image, ImageWin

    img = Image.open(io.BytesIO(png_bytes))
    nombre_impresora = IMPRESORA or win32print.GetDefaultPrinter()
    hdc = win32ui.CreateDC()
    hdc.CreatePrinterDC(nombre_impresora)
    hdc.StartDoc('Etiqueta Ortodoncia')
    hdc.StartPage()
    # área imprimible del driver
    ancho_dev = hdc.GetDeviceCaps(8)   # HORZRES
    alto_dev = hdc.GetDeviceCaps(10)   # VERTRES
    escala = min(ancho_dev / img.width, alto_dev / img.height)
    w, h = int(img.width * escala), int(img.height * escala)
    dib = ImageWin.Dib(img)
    dib.draw(hdc.GetHandleOutput(), (0, 0, w, h))
    hdc.EndPage()
    hdc.EndDoc()
    hdc.DeleteDC()


def procesar_una(trabajo, modo):
    codigo = trabajo['codigo']
    nombre = trabajo.get('producto_nombre', '')
    unidad = trabajo.get('unidad', '')
    veces = int(trabajo.get('cantidad', 1) or 1)
    png = generar_etiqueta_png(codigo, nombre, unidad)
    if modo == 'guardar':
        os.makedirs(GUARDAR_DIR, exist_ok=True)
        ruta = os.path.join(GUARDAR_DIR, f'etiqueta_{codigo}.png')
        with open(ruta, 'wb') as f:
            f.write(png)
        print(f'  → guardada {ruta}')
    else:
        for _ in range(veces):
            imprimir_png(png)
        print(f'  → impresa x{veces}: {nombre} [{codigo}]')


def loop(modo='imprimir'):
    print(f'[print_agent] backend={API}  impresora={IMPRESORA or "(por defecto)"}  modo={modo}')
    if not PRINT_TOKEN:
        print('[print_agent] ADVERTENCIA: PRINT_TOKEN vacío. Configúralo igual que en Render.')
    while True:
        try:
            r = requests.get(f'{API}/api/compras/impresion/cola', headers=_headers(), timeout=20)
            r.raise_for_status()
            trabajos = r.json().get('trabajos', [])
            for t in trabajos:
                try:
                    procesar_una(t, modo)
                    requests.post(f'{API}/api/compras/impresion/marcar', headers=_headers(),
                                  json={'id': t['id'], 'estado': 'impreso'}, timeout=20)
                except Exception as e:
                    print(f'  ✗ error con trabajo {t.get("id")}: {e}')
                    requests.post(f'{API}/api/compras/impresion/marcar', headers=_headers(),
                                  json={'id': t['id'], 'estado': 'error'}, timeout=20)
        except Exception as e:
            print(f'[print_agent] error consultando cola: {e}')
        time.sleep(INTERVALO)


if __name__ == '__main__':
    if '--test' in sys.argv:
        png = generar_etiqueta_png('OR-1-ABC123', 'Ligaduras elásticas transparentes', 'caja')
        os.makedirs(GUARDAR_DIR, exist_ok=True)
        ruta = os.path.join(GUARDAR_DIR, 'etiqueta_ejemplo.png')
        with open(ruta, 'wb') as f:
            f.write(png)
        print(f'Etiqueta de ejemplo generada: {ruta}')
    elif '--guardar' in sys.argv:
        loop(modo='guardar')
    else:
        loop(modo='imprimir')
