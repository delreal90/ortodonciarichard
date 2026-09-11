# Base de datos de Ortodoncia Richard — inventario

> ✅ **CONSTRUIDA el 2026-09-10.** Este archivo nació como documento de traspaso para la
> sesión que iba a ordenar los datos; esa sesión ya ocurrió. **El qué se construyó y por
> qué vive en `CLAUDE.md` → *Base de datos clínica — CONSTRUIDA***. Acá queda el
> **inventario de dónde vive cada dato**, que es lo que sigue siendo útil y no estaba
> escrito en ninguna otra parte.
>
> ⚠️ Lo que este archivo decía sobre el seudónimo `pid`, el salt y el alcance **quedó
> obsoleto**: ver "Lo que cambió al construirla", más abajo.
>
> Escrito el 2026-09-10. Los volúmenes son de producción, medidos ese día.
> ⚠️ **El repo es PÚBLICO.** Acá no va ningún RUT, nombre ni cifra de un paciente.

---

## 1. El problema, en una frase

El proyecto tiene **más de veinte almacenes de datos separados**, cada uno nacido con el
sistema que lo necesitaba, y **ninguna forma de cruzarlos**. Hoy no se puede contestar
"¿el paciente con la arcada estrecha inicia tratamiento más seguido?" aunque los dos datos
existan, porque viven en archivos que nadie enlazó nunca.

No es descuido: cada uno se hizo bien para su propósito. Lo que falta es la capa de arriba.

---

## 2. Inventario — dónde vive cada dato hoy

Todo lo de abajo vive en el **disco persistente de Render**, junto a `PATIENT_INDEX_PATH`,
y está **gitignored**. Nada de esto se versiona.

### Las dos bases SQLite (relaciones reales)

| Base | Módulo | Qué guarda | Volumen (2026-09-10) |
|---|---|---|---|
| `clinica.db` (era `kpi.db`) | `kpi.py` + `clinico.py` | La **agenda completa**, una fila por cita: estado, motivo, doctor, quién agendó, cuándo se creó | **61.342 citas**, 2021-01-04 → hoy |
| `compras.db` | `compras.py` | Compras, gastos, stock, proveedores, usuarios propios | ~950 compras, 866 productos, 157 proveedores |

Las dos son la **excepción documentada a la regla 2**: hay relaciones reales y `GROUP BY`,
no documentos sueltos.

### El índice de pacientes — el que conecta todo

| | |
|---|---|
| Archivo | `patient_index.json` (`pacientes.py`) |
| Volumen | **4.383 pacientes** |
| Clave | RUT limpio (dígitos + K, vía `scheduling.limpiar_rut`) |
| Campos | nombres, apellidos, email, teléfono, **género**, dirección, comuna, previsión, convenio, **fecha de nacimiento**, `id_paciente` de DentiDesk |
| Cobertura | fecha de nacimiento **61,9 %** · género ~97 % |

⚠️ **Es la única llave común entre sistemas**, y el RUT es esa llave. Cualquier diseño
nuevo tiene que respetar `limpiar_rut` como forma canónica.

⚠️ Sus fuentes son **importaciones manuales por lote** (dos export de Excel del panel de
DentiDesk) más el barrido de la agenda 2×/día y la ficha del Google Form. Por eso hay
huecos: el género solo entra por un export y la fecha de nacimiento por otro, y ninguno se
corre solo.

### Los registros JSON, por sistema

| Archivo | Módulo | Qué guarda | Volumen |
|---|---|---|---|
| `informe_pc_registro.json` | `informe_pc.py` | **Mediciones, hallazgos, tamizaje y órdenes** de cada informe | 18 |
| `psq_registro.json` | `psq.py` | Cuestionarios de sueño pediátricos respondidos | pocos |
| `consentimientos_registro.json` | `consentimientos.py` | Consentimientos firmados + hash del PDF | 44 |
| `seguros_registro.json` | `seguros.py` | Formularios de reembolso generados y enviados | — |
| `nps_registro.json` | `nps.py` | Encuestas de satisfacción y sus respuestas | — |
| `control_dental_registro.json` | `control_dental.py` | Quién está inscrito y cuándo le toca el recordatorio | — |
| `recaptacion_registro.json` | `recaptacion.py` | Recordatorios de control enviados y programados | — |
| `fotos_finales_registro.json` | `fotos_finales.py` | Candidatos a collage post-tratamiento | — |
| `seguimiento_pc_registro.json` | `seguimiento_pc.py` | Seguimiento de primeras consultas | — |
| `reactivacion_registro.json` | `reactivacion.py` | Campañas de reactivación | — |
| Otros | `confirmaciones`, `recordatorios_wa`, `link_agenda`, `link_aseguradora`, `reagenda_pendientes`, `paciente_estado`, `backup`, `fichas_estado` | Registros operativos y anti-duplicados | — |

### Fuera del backend

| | |
|---|---|
| `ortodonciarichard-analytics/data/atendidos_2021-jul2026.parquet` | 46.692 atenciones, export histórico. ⚠️ **Superado por `kpi.db`**, que además trae el estado de cada cita (el parquet solo tiene atendidos). Sirve como verificación cruzada. |
| DentiDesk | La ficha clínica real. **No expone API de pacientes ni de documentos** — solo 6 endpoints de agenda. |

---

## 3. Lo que ya está decidido (no re-litigar)

Estas decisiones se tomaron con el usuario y están documentadas en `CLAUDE.md`:

1. **Los informes se guardan para siempre.** `podar()` se eliminó el 2026-09-10.
2. **El JSON sigue siendo la fuente de verdad; SQLite sería una PROYECCIÓN** — derivada,
   desechable y reconstruible. Eso respeta la regla 2 y permite corregir años de
   percentiles si cambia la tabla normativa, sin reescribir nada a mano.
3. **Seudónimo estable por paciente** (`HMAC-SHA256(rut_limpio, salt)`). El RUT vive en
   **una sola tabla**; todo lo demás lleva el seudónimo. Consecuencia que vale el diseño
   entero: *exportar para un estudio es "todo menos esa tabla"*, ya anonimizado.
4. **El texto libre no se proyecta** (motivo de consulta, hallazgos propios): es donde más
   fácil se cuela un dato identificante y no es analizable igual.
5. **Formato largo** en la tabla de mediciones, con el operador en la clave — para poder
   reportar reproducibilidad entre examinadores (ICC) sin `ALTER TABLE`.

El diseño completo está en `CLAUDE.md` → *"Base de datos clínica — DISEÑADA, no construida"*.

---

## 3-bis. ⭐ ALCANCE DECIDIDO (2026-09-10): una sola base analítica

El usuario lo pidió explícito: *"quiero el más amplio… idealmente tener una base de datos
para todo el proyecto, cosa que sea lo más integrado posible"*.

**Esto reemplaza la recomendación anterior de `clinica.db` separada de `kpi.db`.** Con la
integración como objetivo, dos bases obligan a cruzar en Python lo que SQL haría solo.

### La distinción que ordena todo

Hay **dos capas** que se confunden fácil, y solo una se unifica:

| | Qué es | Qué se hace |
|---|---|---|
| **Registros operativos** | El estado vivo de cada sistema: qué recordatorio se mandó, qué consentimiento está pendiente, qué cita espera respuesta | **SE QUEDAN DONDE ESTÁN.** Su aislamiento es una protección, no un descuido |
| **Capa de análisis** | Donde se hacen preguntas cruzadas | **UNA SOLA BASE**, integrada de verdad |

⚠️ **Por qué los operativos NO se fusionan.** Hoy, si el registro de seguros se corrompe,
los recordatorios de WhatsApp siguen funcionando. Fusionarlos convierte cada escritura en
un riesgo para todo lo demás — y son decenas al día. Además `jsonstore` ya resuelve bien
ese caso: escritura atómica, lock, y **un archivo corrupto se aparta en vez de pisarse**.

### La base: `kpi.db` extendida, no una nueva

Ya tiene la agenda completa (**61.342 citas, 2021 → hoy**), el patrón de esquema con
migraciones, el backfill reanudable y **60 pruebas**. Empezar de cero sería tirar eso.

Se le agregan al lado las tablas clínicas:

```
YA EXISTEN          citas · disponibilidad · ingresos · snapshots
SE AGREGAN          pacientes · informes · mediciones · oclusion
                    hallazgos · ordenes · tamizajes · meta
```

⚠️ **`pacientes` es la ÚNICA tabla con RUT.** Todo lo demás lleva `pid`
(`HMAC-SHA256(rut_limpio, salt)`). Eso da las dos propiedades a la vez:

- **Integración total** — un `JOIN` cruza un ancho de arcada con el destino de la primera
  consulta, sin salir de SQL.
- **Export anonimizado por construcción** — exportar para un estudio es *"todo menos
  `pacientes`"*, sin trabajo extra ni riesgo de olvidar una columna.

⚠️ **`citas` hoy indexa por RUT limpio.** Hay que agregarle `pid` (derivable del RUT que ya
tiene) para que los `JOIN` no pasen por el RUT. Es una migración de una columna más un
backfill, del tipo que `kpi.reclasificar()` ya sabe hacer.

### `compras.db` queda AFUERA

Tiene `usuarios` con `password_hash` y `salt`, y `sesiones` activas. Es otro dominio, con
su propio login y sus propios roles: meterlo en la base clínica significa que **la base que
se exporta para investigación carga credenciales**.

Lo que sí conviene: **proyectar sus agregados** (gasto mensual por categoría y ámbito) a la
tabla `ingresos` o a una hermana, para poder cruzar costo contra producción sin mover el
sistema de compras.

### Lo que esta base NUNCA va a tener

⚠️ **La ficha clínica real vive en DentiDesk**, que solo expone 6 endpoints de agenda —
sin API de pacientes ni de documentos (verificado, ver `CLAUDE.md`). Así que acá habrá la
agenda, los informes de evaluación, los tamizajes, los consentimientos y las encuestas.
**No** el odontograma, **no** las evoluciones, **no** las radiografías.

Decirlo importa: una base que se llama "de todo el proyecto" invita a suponer que tiene
todo, y una consulta que asume un dato que no está da un resultado sesgado sin avisar.

### Orden sugerido

1. **Medir** cuánto pesa el registro de informes y cuánto crece. Decide si la proyección es
   urgente o puede esperar.
2. **`pid` en `citas`** — la migración que habilita todos los cruces.
3. **Tablas clínicas + proyección** desde `informe_pc_registro.json`, idempotente y
   reconstruible con un `proyectar_todo()`.
4. **El contador de muestra** — cuántos pacientes acumulados por edad y sexo cumplen los
   criterios de Bishara. Es lo que dice cuándo alcanza para el estudio, y el único
   incentivo real para llenar bien el formulario todos los días.
5. **Proyectar el resto**: tamizajes (ya hay `tamizaje_link.historial()` que los junta),
   consentimientos, NPS, y los agregados de compras.
6. **El export**, que a esta altura ya sale anonimizado solo.

---

## 4. El costo de escala que ya se ve

`informe_pc_registro.json` lo parsea `jsonstore` **entero en cada lectura y en cada
guardado**. A ~2 informes/día son ~500/año y se guardan para siempre. En esta sesión se
bajó de 4 barridos a 1 por documento (hay una prueba que lo fija en 1), lo que compra un
margen de 4× — **pero no resuelve el fondo**.

Es el argumento más fuerte para la proyección a SQLite, y el que conviene medir primero:
cuántos KB pesa hoy y cuánto crece por informe.

---

## 5. Lo que falta y NO es código

⚠️ Esto puede bloquear el uso de los datos aunque la base esté perfecta:

1. **Protocolo de MEDICIÓN escrito.** ⚠️ Corregido el 2026-09-10: **no se escanea** en la
   primera consulta — se mide **directo en boca con pie de metro** (anchos transversales,
   resalte y sobremordida). El problema del punto de referencia es el mismo: Bishara mide
   sobre la **cúspide mesiovestibular** y la lámina del FAIREST usa la mesiolingual, ~15 mm
   más adentro. Sin un protocolo escrito que fije el punto, el n acumulado no vale para nada
   publicable — y con pie de metro se suma la reproducibilidad entre operadores, que con un
   STL no existía.
2. **Consentimiento para investigación.** La atención clínica cubre el uso asistencial;
   **el uso para estudios es otra finalidad**. Con la Ley 21.719 en plena vigencia el
   **1-dic-2026** y los datos de salud como categoría sensible, hace falta una línea en el
   consentimiento y declararlo en `privacidad.html`.
3. **Reproducibilidad entre examinadores** (ICC) si el objetivo es publicar.
4. ⚠️ **Seudonimizado no es anónimo.** Para publicar hay que agregar, o re-etiquetar sin
   guardar el mapeo.

---

## 6. Lo que se acumula desde ya

- La casilla **«Sin tratamiento de ortodoncia previo»** está en el formulario del informe
  desde el 2026-09-10 (`sin_tratamiento_previo`). Es criterio de inclusión de Bishara y no
  se registraba en ninguna parte; se agregó ahora para no tener que preguntarlo hacia atrás.
- Con eso, cada informe nuevo ya guarda lo necesario para entrar a la muestra.

---

## 7. La oportunidad que justifica todo esto

Con el escáner y miles de pacientes se podría construir una **normativa chilena de anchos
de arcada** con los criterios de Bishara y un n de tres dígitos. **Hoy no existe.** Se
buscó reemplazo moderno y no lo hay: Riolo/Moyers son cefalométricos, no anchos de arcada,
y el único chileno (Contulmo, n=48, 6-8 años) coincide con Bishara pero es chico.

Detalle en `CLAUDE.md` → sección *Evaluación transversal*.

---

## 8. Lo que cambió al construirla (2026-09-10)

Las preguntas abiertas que tenía este documento se contestaron, y **tres de ellas
cambiaron el diseño**. El detalle está en `CLAUDE.md`; acá queda lo que contradice lo que
se lee más arriba, para que nadie siga una decisión que ya no rige.

1. ⚠️ **No hay seudónimo `pid` ni salt.** El RUT es la llave en todas las tablas.
   Decisión textual del usuario: *"no me molesta que rut esté en varios lados… el
   anonimizado lo hago después, no me interesa que la base de datos sea anónima"*. Esto
   **anula** el punto 3 de la sección 3, el punto ⚠️ de la sección 3-bis sobre `pid` en
   `citas`, y las preguntas 3 y 4 de la lista original.
   > Tenía además una contradicción de origen: *"`pacientes` es la ÚNICA tabla con RUT"*
   > chocaba con que `citas` ya lo guardaba en 61.205 filas, indexado, y con que
   > `destino_primeras_consultas()` lo devuelve a propósito para contactar a los perdidos.
2. **El nombre del archivo cambió: `kpi.db` → `clinica.db`.** Un archivo llamado `kpi.db`
   se lee como *"métricas, derivado, desechable"*, y ahora guarda el registro clínico y
   5 años de agenda. Lo resuelve `admin/basedatos.py`, respetando `KPI_DB_PATH` si está
   seteada. ⚠️ `backup.py` lista los archivos **por nombre escrito a mano**: sin agregar
   el nombre nuevo, la base dejaba de respaldarse en silencio.
3. **El alcance se amplió.** No solo el registro clínico: los **ocho** sistemas que le
   hablan al paciente entran por una tabla `eventos` con un adaptador chico cada uno, que
   es lo que permite cruzar cualquier dato con cualquier otro. **Compras queda afuera**
   (decisión del usuario, "no por ahora") y el **parquet de analytics se declara
   superado**: no se toca, queda como verificación cruzada independiente.
4. **El medidor es el doctor de la cita** (*"el doctor que lo mide es con quien tiene la
   cita el paciente"*), así que no hizo falta campo nuevo en el formulario.
5. **El criterio de la muestra no va cableado**: *"guarda todo, pero para después se pueda
   elegir criterios de normalidad con filtros"*. El contador filtra por tratamiento
   previo, clase molar, clase canina, sexo y edad.

### Lo que se midió y corrigió la prioridad

El "costo de escala" de la sección 4 **no era urgente**: `informe_pc_registro.json` pesa
38 KB con 16 informes, y **el 43 % de ese peso son códigos QR en base64** que
`informe_pc.qr_data_uri()` sabe regenerar desde la `url` que ya se guarda. Así que la base
se hizo **para poder cruzar**, no porque algo estuviera lento — que es una razón mejor.

### Lo que sigue pendiente

- **Dejar de guardar el QR** en el registro de informes y regenerarlo al armar el
  documento. Commit aparte, bajo riesgo, y le saca casi la mitad del peso al archivo.
- ⚠️ **Verificar en producción que `disponibilidad` se esté poblando.** En local está
  vacía, y es la **única tabla irrecuperable**: `getAvailableHours` solo responde por días
  futuros, así que lo que no se capture hoy no se reconstruye nunca.
- **Los otros sistemas pueden ganar tablas tipadas** cuando alguno amerite análisis a
  fondo; hoy entran por `eventos`, que es deliberadamente laxa.
- **Compras**: entra después sin rehacer nada, por `eventos` o por una tabla de agregados.
- Y lo que **no es código** (sección 5): el protocolo de medición escrito y la línea de
  consentimiento para investigación, con la Ley 21.719 en plena vigencia el 1-dic-2026.
