# Base de datos de Ortodoncia Richard — documento de traspaso

> **Para qué existe este archivo:** el Dr. Alberto quiere abrir una sesión dedicada a
> ordenar los datos del proyecto. Esto es el punto de partida, para que esa sesión **no
> tenga que redescubrir dónde vive cada cosa** — el inventario de abajo tomó su rato de
> medir y no estaba escrito en ninguna parte.
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
| `kpi.db` | `kpi.py` | La **agenda completa**, una fila por cita: estado, motivo, doctor, quién agendó, cuándo se creó | **61.342 citas**, 2021-01-04 → hoy |
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

1. **Protocolo de escaneo escrito.** Bishara mide la cúspide mesiovestibular y la lámina
   del FAIREST la mesiolingual — **~15 mm de diferencia**. Sin un protocolo que diga qué
   punto se mide en el Medit, el n acumulado no vale para nada publicable.
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

## 8. Preguntas abiertas para esa sesión

1. **¿Alcance?** ¿Solo lo clínico del informe, o también cruzar con la agenda (`kpi.db`),
   seguros, NPS y control dental? El cruce con la agenda es el que habilita las preguntas
   más interesantes.
2. **¿Una base o dos?** El diseño actual propone `clinica.db` **separada** de `kpi.db`,
   sin `ATTACH` en producción — así se preserva la propiedad de que la clínica menos su
   tabla de pacientes es anónima. Se puede reconsiderar.
3. **¿Migrar los registros JSON operativos** (recordatorios, confirmaciones, links) o
   dejarlos donde están? Son de operación, no de análisis: probablemente se quedan.
4. **¿Dónde vive el salt del seudónimo?** Dentro de la base (sobrevive un redeploy, pero
   se pierde con ella) o en variable de entorno.
5. **¿Se toca el parquet de analytics** o se declara superado por `kpi.db`?

---

## 9. Cómo empezar esa sesión

Este es el prompt sugerido:

```
Quiero ordenar los datos de ortodonciarichard como una base de datos coherente,
para poder hacer estadística clínica, estudios y tener referencia futura.

Lee primero BASE-DE-DATOS.md en la raíz del repo: tiene el inventario de dónde
vive cada dato hoy, las decisiones ya tomadas y las preguntas abiertas.
Después CLAUDE.md, sobre todo las 8 reglas del inicio y la sección
"Base de datos clínica — DISEÑADA, no construida".

Antes de escribir código, hazme las preguntas de la sección 8 de ese documento
y cualquier otra que necesites. Quiero decidir el alcance contigo antes de que
construyas nada.
```

⚠️ **Que esa sesión no empiece a construir sin decidir el alcance.** La pregunta 1 cambia
casi todo el diseño.
