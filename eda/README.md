# EDA de GitHub Agentic Workflows

## Dataset

La EDA usa el dataset propio generado por la Tarea 3, ubicado en
huggingface-dataset/. El snapshot actual contiene:

- 374 repositorios.
- 1.541 archivos Markdown.
- 60.038 nodos de frontmatter normalizados.
- 1.541 bodies Markdown.

El dataset propio está publicado en
[SebaS01010101/GHAW-H](https://huggingface.co/datasets/SebaS01010101/GHAW-H).
No se utiliza el dataset temporal de `pavtch/GHAW-H`.

## Estructura

- 01_descripcion_y_calidad.ipynb: origen, carga, esquema, relaciones,
  controles de calidad y decisiones de tratamiento.
- 02_exploracion_y_hallazgos.ipynb: distribución por repositorio,
  frontmatter, body, dos preguntas exploratorias, hallazgos y limitaciones.

Notebook 2 vuelve a importar librerías, carga los cuatro Parquet y reconstruye
sus tablas derivadas; es técnicamente independiente de Notebook 1.

## Datos requeridos

Desde la raíz del proyecto deben existir:

    huggingface-dataset/
    ├── repositories.parquet
    ├── workflow_files.parquet
    ├── frontmatter_fields.parquet
    └── workflow_bodies.parquet

También se puede indicar otra ubicación con la variable de entorno
CHIKIMINER_EDA_DATA_DIR. En PowerShell:

    $env:CHIKIMINER_EDA_DATA_DIR = 'C:\ruta\al\dataset'

## Instalación

Desde la raíz del proyecto:

    uv sync --group eda

El grupo eda contiene JupyterLab, ipykernel, pandas, PyArrow, Matplotlib y
Seaborn mediante las dependencias del proyecto.

## Kernel

Registrar el kernel del proyecto:

    uv run --group eda python -m ipykernel install --user --name chikiminer-eda --display-name 'Python (chikiMiner EDA)'

## Inicio

    uv run --group eda jupyter lab

En JupyterLab, seleccionar el kernel Python (chikiMiner EDA).

## Orden de ejecución

1. Abrir y ejecutar 01_descripcion_y_calidad.ipynb.
2. Abrir y ejecutar 02_exploracion_y_hallazgos.ipynb.

Para conservar resultados: reiniciar el kernel, ejecutar todas las celdas y
guardar el notebook. Notebook 2 puede ejecutarse directamente desde un kernel
limpio, sin ejecutar Notebook 1 antes.

También es posible ejecutar desde terminal:

    uv run --group eda jupyter nbconvert --to notebook --execute --ExecutePreprocessor.kernel_name=chikiminer-eda --ExecutePreprocessor.timeout=600 --inplace eda/01_descripcion_y_calidad.ipynb
    uv run --group eda jupyter nbconvert --to notebook --execute --ExecutePreprocessor.kernel_name=chikiminer-eda --ExecutePreprocessor.timeout=600 --inplace eda/02_exploracion_y_hallazgos.ipynb

## Dataset procesado

No se generó eda/data/processed/. Las comprobaciones de integridad no
justificaron modificar los Parquet originales. Las agregaciones a nivel archivo
y repositorio se reconstruyen en memoria para evitar doble conteo por
frontmatter_fields.

Los notebooks no eliminan repositorios sin archivos, bodies extremos,
contenidos repetidos ni campos opcionales ausentes.

## Reproducibilidad

La ejecución requiere los cuatro Parquet del snapshot y el entorno del grupo
eda. El análisis no consulta GitHub, no modifica Miner y no sobrescribe los
datos de entrada.
