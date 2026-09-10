"""
Genera los archivos de la carpeta del paciente a partir de los HTML de diseño.

    python generar.py            -> assets + PDFs + vistas previas de las 3 versiones
    python generar.py A          -> solo la versión A (acepta A, B, C)
    python generar.py --assets   -> solo regenera assets (logos, QR, fuentes)

Por cada versión sale:
    carpeta-<X>.pdf          arte final, 479 x 329 mm (con sangrado, sin marcas)
    carpeta-<X>-marcas.pdf   el mismo arte en 499 x 349 mm con marcas de corte y doblez
    carpeta-<X>-guias.pdf    version de REVISION: corte, doblez y zona segura marcados
    vista-<X>.png / vista-<X>-tapa.png / vista-<X>-contratapa.png

El PDF lo produce Chrome en modo headless (texto vectorial, fuentes incrustadas).
Requiere: pillow, numpy, segno, pymupdf, fonttools+brotli, y Chrome instalado.
"""
import base64, os, re, subprocess, sys, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
os.chdir(AQUI)

# ---- geometria (mm) -------------------------------------------------------
W_SANGRADO, H_SANGRADO = 479.0, 329.0
W_CORTE,    H_CORTE     = 473.0, 323.0
SANGRADO = (W_SANGRADO - W_CORTE) / 2          # 3 mm
DOBLEZ_X = W_SANGRADO / 2                      # 239,5 mm
MM = 72 / 25.4                                 # mm -> puntos PDF

VERSIONES = {
    "A": ("carpeta-A-navy.html",   "A-navy"),
    "B": ("carpeta-B-clara.html",  "B-clara"),
    "C": ("carpeta-C-barras.html", "C-barras"),
}

CHROME = next((p for p in [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
] if os.path.exists(p)), None)

URL_QR = "https://www.ortodonciarichard.cl/#agendar"


# ===========================================================================
#  ASSETS
# ===========================================================================
def recolorear_logo(color, destino):
    """El logo es azul plano (#4B8FCC) sobre transparente. Para llevarlo a otro
    color plano hay que estimar la COBERTURA DE TINTA de cada pixel (proyección
    del color sobre el eje blanco->azul) y usar esa cobertura como alfa. Un
    umbral simple se comería el antialias de los bordes y el logo saldría
    dentado; aclarar u oscurecer la imagen lo dejaría gris, no blanco."""
    import numpy as np
    from PIL import Image

    im = np.asarray(Image.open(os.path.join(RAIZ, "images/logo-png.png"))
                    .convert("RGBA")).astype(np.float32)
    rgb, a = im[..., :3], im[..., 3] / 255.0
    blanco = np.array([255, 255, 255], np.float32)
    azul = np.array([75, 143, 204], np.float32)
    d = blanco - azul
    cobertura = np.clip(((blanco - rgb) @ d) / float(d @ d), 0, 1)

    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    out = np.zeros(im.shape, np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = r, g, b
    out[..., 3] = (np.clip(cobertura * a, 0, 1) * 255).round().astype(np.uint8)
    img = Image.fromarray(out, "RGBA")
    img = img.crop(img.getchannel("A").getbbox())
    img = img.resize((1500, round(img.size[1] * 1500 / img.size[0])), Image.LANCZOS)
    img.save(destino)
    print(f"  {destino}  {color}  {img.size}")


def qr():
    import segno
    segno.make(URL_QR, error="q").save(
        "assets/qr-agenda.png", scale=24, border=2, dark="#1A2E4A", light="#FFFFFF")
    print("  assets/qr-agenda.png ->", URL_QR)


def fuentes():
    """Descarga Playfair Display e Inter (licencia OFL), las reduce al juego de
    caracteres latino y las incrusta en base64 dentro de assets/fuentes.css.
    Incrustadas y no enlazadas porque Chrome bloquea la carga de fuentes por CORS
    cuando el HTML se abre con file:// (doble clic)."""
    from fontTools.subset import main as subset

    os.makedirs("assets/fonts", exist_ok=True)
    ua = {"User-Agent": "curl/8"}
    css_google = urllib.request.urlopen(urllib.request.Request(
        "https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600"
        "&family=Inter:wght@300;400;500;600&display=swap", headers=ua), timeout=30).read().decode()

    uni = ("U+0020-007E,U+00A0-00FF,U+0131,U+0152-0153,U+2013-2014,"
           "U+2018-201A,U+201C-201E,U+2022,U+2026,U+00B7,U+00BF,U+00A1")
    css, vistos = ["/* Generado por generar.py - subconjuntos OFL incrustados */\n"], set()
    for bloque in re.findall(r"@font-face\s*{(.*?)}", css_google, re.S):
        fam = re.search(r"font-family:\s*'([^']+)'", bloque).group(1)
        peso = re.search(r"font-weight:\s*(\d+)", bloque).group(1)
        url = re.search(r"url\((https://[^)]+\.ttf)\)", bloque)
        if not url or (fam, peso) in vistos:
            continue
        vistos.add((fam, peso))
        ttf = f"assets/fonts/{fam.replace(' ', '')}-{peso}.ttf"
        open(ttf, "wb").write(urllib.request.urlopen(
            urllib.request.Request(url.group(1), headers=ua), timeout=60).read())
        woff2 = ttf.replace(".ttf", ".subset.woff2")
        try:
            subset([ttf, f"--unicodes={uni}", "--flavor=woff2",
                    "--layout-features=kern,liga", f"--output-file={woff2}"])
        except SystemExit:
            pass
        b64 = base64.b64encode(open(woff2, "rb").read()).decode()
        css.append(f"@font-face{{font-family:'{fam}';font-style:normal;font-weight:{peso};"
                   f"font-display:block;src:url(data:font/woff2;base64,{b64}) format('woff2');}}")
    open("assets/fuentes.css", "w", encoding="utf-8").write("\n".join(css))
    print("  assets/fuentes.css", os.path.getsize("assets/fuentes.css") // 1024, "KB")


# ===========================================================================
#  PDF
# ===========================================================================
def imprimir(html, query, salida):
    if not CHROME:
        sys.exit("No encuentro Chrome ni Edge para generar el PDF.")
    url = "file:///" + os.path.join(AQUI, html).replace("\\", "/").replace(" ", "%20")
    subprocess.run([CHROME, "--headless=new", "--disable-gpu",
                    "--allow-file-access-from-files", "--no-pdf-header-footer",
                    "--virtual-time-budget=8000",
                    f"--print-to-pdf={os.path.join(AQUI, salida)}", url + query],
                   check=True, capture_output=True)
    print("  " + salida)


def con_marcas(origen, salida, etiqueta, margen_mm=10.0, largo=6.0, separacion=2.0):
    """Repone el arte en una hoja más grande y dibuja las marcas de corte y de
    doblez EN EL MARGEN, nunca sobre el arte. El contenido sigue siendo vectorial."""
    import fitz

    src = fitz.open(origen)
    doc = fitz.open()
    pag = doc.new_page(width=(W_SANGRADO + 2 * margen_mm) * MM,
                       height=(H_SANGRADO + 2 * margen_mm) * MM)
    pag.show_pdf_page(fitz.Rect(margen_mm * MM, margen_mm * MM,
                                (margen_mm + W_SANGRADO) * MM,
                                (margen_mm + H_SANGRADO) * MM), src, 0)

    x0 = (margen_mm + SANGRADO) * MM
    x1 = (margen_mm + SANGRADO + W_CORTE) * MM
    y0 = (margen_mm + SANGRADO) * MM
    y1 = (margen_mm + SANGRADO + H_CORTE) * MM
    s, L = separacion * MM, largo * MM
    negro = (0, 0, 0)

    def linea(a, b, ancho=0.25, guiones=None):
        f = pag.new_shape()
        f.draw_line(fitz.Point(*a), fitz.Point(*b))
        f.finish(color=negro, width=ancho, dashes=guiones)
        f.commit()

    for x in (x0, x1):                       # marcas de corte verticales
        linea((x, y0 - s), (x, y0 - s - L))
        linea((x, y1 + s), (x, y1 + s + L))
    for y in (y0, y1):                       # marcas de corte horizontales
        linea((x0 - s, y), (x0 - s - L, y))
        linea((x1 + s, y), (x1 + s + L, y))

    xd = (margen_mm + DOBLEZ_X) * MM         # marca de doblez
    linea((xd, y0 - s), (xd, y0 - s - L), 0.5, "[2 2] 0")
    linea((xd, y1 + s), (xd, y1 + s + L), 0.5, "[2 2] 0")
    pag.insert_text(fitz.Point(xd + 3, y1 + s + L - 1), "DOBLEZ", fontsize=5, color=negro)

    pag.insert_text(fitz.Point(x0, y1 + s + L + 5),
                    f"Carpeta paciente - exterior - version {etiqueta} | "
                    "pliego con sangrado 479 x 329 mm | corte final 473 x 323 mm | "
                    "doblez al centro (239,5 mm) | 2 paneles de 236,5 x 323 mm",
                    fontsize=6, color=negro)
    doc.save(os.path.join(AQUI, salida))
    print("  " + salida)


def vistas(pdf, etiqueta):
    import fitz
    d = fitz.open(pdf)
    d[0].get_pixmap(dpi=150).save(f"vista-{etiqueta}.png")
    d[0].get_pixmap(dpi=150, clip=fitz.Rect(DOBLEZ_X * MM, 0, W_SANGRADO * MM,
                                            H_SANGRADO * MM)).save(f"vista-{etiqueta}-tapa.png")
    d[0].get_pixmap(dpi=150, clip=fitz.Rect(0, 0, DOBLEZ_X * MM,
                                            H_SANGRADO * MM)).save(f"vista-{etiqueta}-contratapa.png")
    print(f"  vista-{etiqueta}.png (+ tapa y contratapa)")


# ===========================================================================
if __name__ == "__main__":
    os.makedirs("assets", exist_ok=True)
    solo_assets = "--assets" in sys.argv
    pedidas = [a.upper() for a in sys.argv[1:] if a.upper() in VERSIONES] or list(VERSIONES)

    print("Assets:")
    recolorear_logo("#FFFFFF", "assets/logo-blanco.png")    # versiones A y C
    recolorear_logo("#4B8FCC", "assets/logo-celeste.png")   # versión B
    qr()
    if not os.path.exists("assets/fuentes.css") or solo_assets:
        fuentes()
    else:
        print("  assets/fuentes.css (ya existe; --assets para rehacerlo)")

    if solo_assets:
        raise SystemExit(0)

    for v in pedidas:
        html, etq = VERSIONES[v]
        print(f"\nVersión {etq}:")
        imprimir(html, "", f"carpeta-{etq}.pdf")
        imprimir(html, "?guias", f"carpeta-{etq}-guias.pdf")
        con_marcas(f"carpeta-{etq}.pdf", f"carpeta-{etq}-marcas.pdf", etq)
        vistas(f"carpeta-{etq}.pdf", etq)

    import fitz
    r = fitz.open(f"carpeta-{VERSIONES[pedidas[0]][1]}.pdf")[0].rect
    print(f"\nOK. Arte final: {r.width / MM:.1f} x {r.height / MM:.1f} mm")
