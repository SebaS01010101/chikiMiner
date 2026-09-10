# Desarrollo y pruebas

## Preparar el entorno

```powershell
uv sync
```

Para comprobar que el paquete se resuelve desde el código fuente:

```powershell
uv run python -c "import chikiminer; print(chikiminer.__file__)"
```

No usar un token real en tests. La configuración de tests crea tokens ficticios
y sustituye las respuestas HTTP con `httpx.MockTransport`.

## Estructura relevante

```text
src/chikiminer/
├── cli.py                 # comandos miner y miner dataset
├── config.py              # configuración y secreto
├── csv_io.py              # entrada/salida CSV
├── detector.py            # regla de detección GH-AW
├── service.py             # orquestación Task 2
├── checkpoint.py          # SQLite y cache
├── progress.py            # estado visual de consola
├── github/                # GraphQL, REST, rate limits y adaptación
└── dataset/               # extracción, parsing, normalización y Parquet

tests/
├── fixtures/              # CSV y Markdown controlados
└── test_*.py              # pruebas unitarias e integración con mocks
```

## Ejecutar y validar la EDA

La EDA usa el grupo opcional `eda`, que añade JupyterLab, `ipykernel` y
`seaborn`; pandas, PyArrow y Matplotlib son dependencias del proyecto o del
grupo. Desde la raíz:

```powershell
uv sync --group eda
uv run --group eda python -m ipykernel install --user --name chikiminer-eda --display-name "Python (chikiMiner EDA)"
uv run --group eda jupyter lab
```

Los notebooks deben ejecutarse en este orden para la entrega, aunque el
segundo es independiente y vuelve a cargar los Parquet desde un kernel limpio:

1. `eda/01_descripcion_y_calidad.ipynb`.
2. `eda/02_exploracion_y_hallazgos.ipynb`.

La ejecución headless reproducible usa `jupyter nbconvert` con el kernel
`chikiminer-eda`, `--execute` y timeout de 600 segundos. Los notebooks
guardan sus outputs y no escriben `eda/data/processed/`.

## Ejecutar las pruebas

Suite completa:

```powershell
uv run pytest
```

Un módulo concreto:

```powershell
uv run pytest tests/test_progress.py
uv run pytest tests/test_dataset_service.py
```

Comprobación manual del CLI:

```powershell
uv run miner --help
uv run miner dataset --help
```

Antes de una entrega, ejecutar como mínimo la suite completa y los tests del
módulo modificado. Si el entorno usa el intérprete local:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Criterios para cambios

- Mantener las reglas de dominio fuera de `cli.py`.
- No imprimir tokens, cabeceras sensibles ni contenido de `.env`.
- Conservar la semántica explícita de estados; un error nunca es `NO_MATCH`.
- Mantener la escritura atómica del CSV y la validación del dataset.
- Añadir o modificar tests junto con cada comportamiento nuevo.
- Preferir interfaces pequeñas (`Protocol`) para facilitar mocks.
- Mantener límites de concurrencia y de reintentos explícitos.
- Actualizar la documentación en la misma modificación.

## Checklist obligatorio de documentación

Antes de cerrar un cambio:

1. Identificar si afecta uso, arquitectura, CLI, dataset, persistencia o tests.
2. Actualizar el documento correspondiente en `docs/`.
3. Añadir una entrada a [cambios.md](cambios.md).
4. Si cambia una opción o salida, actualizar el manual y ejecutar `--help`.
5. Si cambia una tabla, columna, clave o cardinalidad, actualizar el diccionario
   y el ERD.
6. Ejecutar `pytest` y registrar el resultado.

La misma regla está resumida en el `AGENTS.md` de la raíz para que agentes y
colaboradores la apliquen antes de editar el proyecto.

## Añadir una opción CLI

1. Declarar la opción en `src/chikiminer/cli.py` con rango y ayuda.
2. Validarla en `config.py` o en la capa de dominio adecuada.
3. Añadir tests de configuración y de invocación.
4. Actualizar las tablas de opciones en [manual-uso.md](manual-uso.md) y
   `docs/cli.md` si corresponde.
5. Añadir el cambio a [cambios.md](cambios.md).

## Añadir una columna al dataset

1. Cambiar el modelo dataclass correspondiente.
2. Cambiar el schema y escritura en `dataset/parquet.py`.
3. Actualizar validaciones y tests.
4. Actualizar [data-dictionary.md](data-dictionary.md) y
   [er-diagram.md](er-diagram.md).
5. Explicar migración o compatibilidad en [cambios.md](cambios.md).

## Revisión de cambios

Antes de entregar, revisar:

```powershell
git diff --check
git status --short
uv run pytest
```

No incluir `.env`, tokens, bases SQLite, resultados grandes ni artefactos
temporales en un commit.
