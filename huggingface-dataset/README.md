---
language:
  - en
  - es
task_categories:
  - text-generation
  - text-classification
tags:
  - github
  - agentic-workflows
  - markdown
  - yaml
  - parquet
pretty_name: GitHub Agentic Workflows Markdown Dataset
---

# GitHub Agentic Workflows Markdown Dataset

## Origen

Este dataset fue generado por Miner a partir de repositorios de GitHub
identificados como usuarios de GitHub Agentic Workflows (GH-AW). Un repositorio
se considera `MATCH` cuando `.github/workflows/` contiene un par con el mismo
basename:

```text
<basename>.md
<basename>.lock.yml
```

El snapshot publicado está disponible en
[SebaS01010101/GHAW-H](https://huggingface.co/datasets/SebaS01010101/GHAW-H).

Para la extracción se vuelve a listar el directorio y se descargan solamente
los archivos que son archivos regulares y terminan exactamente en `.md`.
`body_markdown` conserva el body Markdown extraído. No se clonan repositorios,
no se usa GitHub Code Search y no se descargan archivos `.lock.yml`.

Fecha de generación: `2026-09-08`.

El artefacto incluido en este repositorio contiene el snapshot de los 374
`MATCH` disponibles en el checkpoint local al momento de generarlo: 1.541
archivos Markdown, 60.038 campos de frontmatter y 1.541 bodies. De ellos, 369
repositorios tienen al menos un archivo Markdown y 5 repositorios tienen cero
archivos extraídos. No representa
los candidatos de `results.csv` que todavía no hayan sido clasificados por la
Tarea 2. El archivo auxiliar `errors.jsonl` no registra errores en este
snapshot.

## Tablas

- `repositories.parquet`: un registro por repositorio `MATCH` canónico.
- `workflow_files.parquet`: metadata y frontmatter crudo de cada Markdown.
- `frontmatter_fields.parquet`: campos YAML heterogéneos como paths y JSON.
- `workflow_bodies.parquet`: body Markdown asociado a cada archivo.

Relaciones:

```text
repositories.repository_id = workflow_files.repository_id
workflow_files.file_id = frontmatter_fields.file_id
workflow_files.file_id = workflow_bodies.file_id
```

`workflow_files.file_id` y `frontmatter_fields.field_id` son SHA-256
deterministas. `frontmatter_fields.key_path` usa paths con puntos; los arrays
se conservan como JSON completo. La clave GH-AW `on` permanece como string.

## Carga

Los archivos pueden leerse directamente con PyArrow:

```python
import pyarrow.parquet as pq

repositories = pq.read_table("repositories.parquet")
workflow_files = pq.read_table("workflow_files.parquet")
frontmatter_fields = pq.read_table("frontmatter_fields.parquet")
workflow_bodies = pq.read_table("workflow_bodies.parquet")
```

## Procedencia y licencia

El contenido Markdown procede de repositorios de terceros en GitHub. Cada
registro conserva `source_url`, cuando GitHub lo proporciona, y el SHA local
del contenido. Las licencias y condiciones de uso de cada archivo siguen
siendo las de su repositorio de origen; deben revisarse antes de redistribuir
el contenido. Este README no sustituye esas licencias.

Miner no publica automáticamente desde tests. Para publicar manualmente:

```powershell
python -m pip install datasets huggingface_hub
huggingface-cli login
# Revisar los cuatro Parquet, este README y las licencias antes de subirlos.
huggingface-cli upload SebaS01010101/GHAW-H huggingface-dataset . --repo-type dataset
```

No incluir `.env`, tokens, caches SQLite, logs privados ni archivos
temporales.
