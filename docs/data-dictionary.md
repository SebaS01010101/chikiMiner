# Diccionario de datos

Los tipos `string` corresponden a `pyarrow.string()` y los tipos
`large_string` a `pyarrow.large_string()`. `PK` y `FK` se validan antes de
escribir los Parquet.

## `repositories`

Representa cada repositorio `MATCH` único de la entrada de la Tarea 2.

| Columna | Tipo | Nullable | Clave | Descripción |
|---|---|---:|---|---|
| `repository_id` | string | No | PK | SHA-256 de `canonical_name`. |
| `full_name` | string | No |  | Nombre `owner/repository` usado como referencia. |
| `canonical_name` | string | No |  | Nombre normalizado en minúsculas. |
| `owner` | string | No |  | Owner de GitHub. |
| `repository_name` | string | No |  | Nombre del repositorio. |
| `default_branch` | string | Sí |  | Valor de `defaultBranch` de la entrada si está disponible. |

## `workflow_files`

Representa cada archivo de texto cuyo nombre termina exactamente en `.md` y
que fue listado como archivo dentro de `.github/workflows/`.

| Columna | Tipo | Nullable | Clave | Descripción |
|---|---|---:|---|---|
| `file_id` | string | No | PK | SHA-256 de `repository_id:path`. |
| `repository_id` | string | No | FK | Referencia a `repositories.repository_id`. |
| `filename` | string | No |  | Nombre del archivo, por ejemplo `issue-triage.md`. |
| `path` | string | No |  | Path GitHub normalizado con `/`. |
| `blob_sha` | string | Sí |  | SHA devuelto por Contents API. |
| `frontmatter_raw` | large_string | Sí |  | YAML original entre los delimitadores `---`. |
| `content_sha256` | string | Sí |  | Hash local SHA-256 del contenido UTF-8 descargado. |
| `parse_status` | string | No |  | `ok`, `no_frontmatter`, `empty_frontmatter`, `invalid_yaml` o `normalization_error`. |
| `parse_error` | string | Sí |  | Error explícito cuando el parsing o normalización falla. |
| `source_url` | string | Sí |  | URL GitHub devuelta por la API, si existe. |

## `frontmatter_fields`

Representa metadata YAML heterogénea sin crear una columna por cada clave.

| Columna | Tipo | Nullable | Clave | Descripción |
|---|---|---:|---|---|
| `field_id` | string | No | PK | SHA-256 de `file_id:key_path`. |
| `file_id` | string | No | FK | Referencia a `workflow_files.file_id`. |
| `key_path` | string | No |  | Path determinista, por ejemplo `permissions.contents`. |
| `value_type` | string | No |  | `string`, `mapping`, `array`, `boolean`, `integer`, `float`, `null` y tipos YAML compatibles adicionales. |
| `value_json` | large_string | No |  | Representación JSON válida, compacta y determinista. |

Los mappings generan una fila propia y filas para sus hijos. Las listas se
conservan completas en una única fila JSON. Por ejemplo:

```text
permissions.contents -> string -> "read"
tools -> array -> ["bash","github"]
```

Las claves con puntos o barras invertidas se escapan en `key_path`. La clave
GH-AW `on` se conserva como string, no como booleano.

## `workflow_bodies`

Representa el body Markdown después del frontmatter.

| Columna | Tipo | Nullable | Clave | Descripción |
|---|---|---:|---|---|
| `file_id` | string | No | PK/FK | Referencia única a `workflow_files.file_id`. |
| `body_markdown` | large_string | No |  | Body original, incluyendo Unicode, saltos de línea y body vacío. |

## Artefacto de errores

`errors.jsonl` no es una tabla relacional final. Es un registro de auditoría
con `repository_id`, `repository`, `path`, `stage` y `error` para errores de
entrada, listado, descarga, parsing o normalización.
