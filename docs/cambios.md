# Registro de cambios

Este registro resume cambios que afectan el comportamiento, el uso o la
documentación. No sustituye al historial de Git; sirve para comprobar que
`docs/` se actualizó junto con el código.

## 2026-09-07

### Documentación del proyecto

- Se creó el índice de documentación en `docs/README.md`.
- Se añadió el [manual de uso](manual-uso.md) para instalación, clasificación,
  dataset, resume, consola, benchmark y troubleshooting.
- Se añadió [arquitectura](arquitectura.md) con flujo, capas, GitHub,
  persistencia, dataset y límites.
- Se añadió [desarrollo y pruebas](desarrollo.md) con checklist de cambios.
- Se añadió `AGENTS.md` con la política de documentación y la regla de
  invocación de `graphify`.
- Se estableció que cada cambio debe actualizar `docs/` y este registro.

### Análisis exploratorio

- Se creó `eda/` con notebooks para descripción, calidad, exploración,
  relaciones y hallazgos del snapshot Parquet estructurado.
- Se añadió `eda/README.md` con obtención de datos, instalación del grupo
  `eda` y ejecución de ambos notebooks.
- Se añadió el grupo opcional de dependencias `eda` en `pyproject.toml` y se
  documentó el flujo en el README raíz, `docs/README.md`, `docs/manual-uso.md`
  y `docs/arquitectura.md`.

### Vista de consola

- La vista interactiva usa una sola línea horizontal con porcentaje, barra y
  estadísticas compactas.
- La barra se adapta al ancho de la terminal.
- Las salidas redirigidas conservan una línea por actualización.

### Validación

- Suite ejecutada después del cambio: `109 passed`.
- Ambos notebooks fueron ejecutados en modo headless sobre el snapshot local.

## 2026-09-08

### Cierre de la EDA de la Tarea 4

- Se añadió `seaborn` al grupo opcional `eda` y se actualizó `uv.lock`.
- Se reemplazaron los dos notebooks borrador por notebooks independientes y
  reproducibles sobre el dataset propio.
- Se documentaron joins validados, tablas analíticas en memoria, cobertura de
  `engine`, `timeout-minutes`, triggers `on.*`, longitud de body, dos preguntas
  exploratorias, hallazgos y limitaciones.
- Se actualizaron `eda/README.md`, el README del dataset y la documentación de
  uso, arquitectura y desarrollo.
- Validación de cierre: 110 tests pasaron y ambos notebooks se ejecutaron con
  kernels limpios mediante `jupyter nbconvert`, conservando outputs.
- Se incorporó el enlace público del dataset:
  <https://huggingface.co/datasets/SebaS01010101/GHAW-H>.

### Entrada de la Tarea 3

- El lector del dataset acepta `uses_ghaw=True` y `uses_ghaw=1`, para cubrir
  tanto la salida nativa de la Tarea 2 como exportaciones CSV numéricas.
