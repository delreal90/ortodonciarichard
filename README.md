# Sitio Web — Ortodoncistas Richard

Sitio web estático para la Clínica Ortodoncistas Richard, Las Condes, Santiago.

- **URL producción:** https://ortodonciarichard.cl *(una vez configurado el DNS en nic.cl)*
- **GitHub Pages:** https://delreal90.github.io/ortodonciarichard
- **Repositorio:** https://github.com/delreal90/ortodonciarichard

---

## Estructura de archivos

```
ortodonciarichard/
├── index.html          ← toda la estructura del sitio (una sola página)
├── css/
│   └── styles.css      ← todos los estilos y colores
├── js/
│   └── main.js         ← interactividad (nav, modal doctores, FAQ, acordeón)
└── images/
    ├── logo.jpg                    ← logo con fondo blanco (nav al hacer scroll)
    ├── logo-png.png                ← logo sin fondo (nav sobre hero oscuro)
    ├── clinica-1.jpg               ← foto principal clínica
    ├── video.MP4                   ← video hero
    ├── sortch-png.png              ← logo SORT Chile
    ├── aao-png.png                 ← logo AAO
    ├── WFO-png.png                 ← logo WFO
    ├── dr-octavio-del-real.jpeg
    ├── dr-rodrigo-oyonarte.jpeg
    ├── dr-alberto-del-real.jpeg
    ├── dr-patricio-vial.png
    ├── urgencias.webp              ← guía de urgencias AAO
    └── ejemplo-*.jpg               ← fotos de casos clínicos
```

---

## Cómo publicar cambios en GitHub

Cada vez que modifiques algo (fotos, textos, etc.), ejecuta esto en la terminal desde la carpeta `ortodonciarichard`:

```bash
git add .
git commit -m "Descripción del cambio"
git push
```

GitHub Pages publica los cambios automáticamente en 1-2 minutos.

---

## Cómo actualizar fotos

### Foto de un doctor
1. Renombra la foto nueva con el mismo nombre que la actual (ej: `dr-octavio-del-real.jpeg`)
2. Cópiala a la carpeta `images/` reemplazando la anterior
3. Publica los cambios con `git add . && git commit -m "..." && git push`

### Agregar foto de un miembro del staff (secretaria, asistente, etc.)
1. Copia la foto a `images/` con nombre descriptivo (ej: `secretaria-maria-gonzalez.jpg`)
2. En `index.html`, busca el placeholder correspondiente (ej: secretaria 1) y reemplaza:

```html
<!-- ANTES -->
<div class="team-photo placeholder-photo" data-initials="S1">
    <div class="initials-circle"></div>
</div>
<h3>Nombre Apellido</h3>
<p class="team-role">Secretaria</p>

<!-- DESPUÉS -->
<div class="team-photo">
    <img src="images/secretaria-maria-gonzalez.jpg" alt="María González">
</div>
<h3>María González</h3>
<p class="team-role">Secretaria</p>
```

3. Publica los cambios en GitHub.

### Agregar fotos de la clínica (recepción, boxes, laboratorio, etc.)
1. Copia la foto a `images/` (ej: `clinica-recepcion.jpg`)
2. En `index.html`, busca la sección `id="galeria"` y agrega dentro de `.gallery-grid`:

```html
<div class="gallery-item">
    <img src="images/clinica-recepcion.jpg" alt="Recepción">
    <div class="gallery-caption">Recepción</div>
</div>
```

3. Publica los cambios en GitHub.

---

## Cómo actualizar textos

### CV de un doctor (modal al hacer clic)
Abre `js/main.js` y busca `const doctorData`. Ahí están los datos de cada doctor:

```js
octavio: {
    name: 'Dr. Octavio Del Real S.',
    bio:  'Texto de la bio...',
    education: ['Título 1', 'Título 2'],
    specialties: ['Especialidad 1'],
},
```

### Agregar o editar una pregunta frecuente
Abre `index.html` y busca la sección `id="pacientes"`. Las preguntas están organizadas en tabs (Primeros pasos, Durante el tratamiento, etc.). Cada pregunta sigue este formato:

```html
<div class="acc-item">
    <button class="acc-btn">¿Pregunta? <i class="fas fa-chevron-down"></i></button>
    <div class="acc-body"><p>Respuesta aquí.</p></div>
</div>
```

---

## Formulario de contacto

El formulario envía los mensajes a `recepcion@ortodonciarichard.cl` usando **Web3Forms**.
- Access Key configurada: `f0aa501d-602a-4212-ac11-16b414a91b61`
- Panel Web3Forms: https://web3forms.com

---

## Pendientes

- [ ] **Fotos del staff** — agregar fotos y nombres reales de las 2 secretarias, 5 asistentes dentales y 3 personas de laboratorio/aseo
- [ ] **Fotos adicionales de la clínica** — recepción, boxes, sala de diagnóstico, laboratorio, esterilización, equipo de rayos
- [ ] **Configurar DNS en nic.cl** — apuntar `ortodonciarichard.cl` a GitHub Pages (ver instrucciones abajo)
- [ ] **Integración DentiDesk** — cuando app.dentidesk.cl habilite API, conectar el botón de agenda online

### DNS para nic.cl

Agregar estos registros sin borrar los existentes (especialmente los MX del correo):

| Tipo  | Nombre | Valor                  |
|-------|--------|------------------------|
| A     | @      | 185.199.108.153        |
| A     | @      | 185.199.109.153        |
| A     | @      | 185.199.110.153        |
| A     | @      | 185.199.111.153        |
| CNAME | www    | delreal90.github.io    |

Luego en GitHub: Settings → Pages → Custom domain → `ortodonciarichard.cl` → Save → activar "Enforce HTTPS".
