# CLI del dataset

## Prerrequisitos

- Python 3.11 o superior.
- Dependencias instaladas con `uv sync`.
- Un token GitHub en `.env`:

```powershell
Copy-Item .env.example .env
# Editar .env y definir GITHUB_TOKEN=...
```

El token no se imprime ni se guarda en los artefactos del dataset.

## Input

El comando recibe la salida clasificada de la Tarea 2. Debe contener al menos:

```text
name,uses_ghaw
```

También utiliza `defaultBranch` cuando esa columna existe. Solo procesa filas
con `uses_ghaw=True` o `uses_ghaw=1`, deduplica por `owner/repository` canónico y vuelve a
listar `.github/workflows/` para descubrir todos los Markdown.

El CSV original sin clasificación (`results.csv`) no es un input válido para
este comando.

## Comando

```powershell
uv run miner dataset results_ghaw.csv `
  --output-dir data/ghaw-dataset `
  --resume `
  --concurrency 2
```

Con el entorno local:

```powershell
.\.venv\Scripts\python.exe -m chikiminer dataset results_ghaw.csv `
  --output-dir data/ghaw-dataset --resume
```

## Parámetros

| Parámetro | Default | Descripción |
|---|---|---|
| `input_csv` | requerido | CSV clasificado de la Tarea 2. |
| `--output-dir` | `dataset` | Directorio de salida. |
| `--resume/--no-resume` | `--resume` | Reutiliza contenido cuyo `blob_sha` no cambió. |
| `--cache-path` | `.chikiminer-cache.sqlite3` | Checkpoint SQLite y cache Markdown. |
| `--concurrency` | `1` | Concurrencia limitada de repositorios, entre 1 y 4. |

El comando legado sigue funcionando sin subcomando:

```powershell
uv run miner results.csv --output results_ghaw.csv --resume
```

## Consola en tiempo real

En una terminal interactiva Miner refresca una única línea horizontal cada
segundo: porcentaje, barra y estadísticas compactas. Las etiquetas usan
abreviaturas (`M` = MATCH, `E` = errores, `C` = cache, `RL` = rate limit).
Al redirigir la salida a un archivo se conserva una línea por actualización.

Para abrir una nueva consola Windows y reanudar la Tarea 2:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/open-miner-console.ps1 `
  -Mode task2 -InputCsv results.csv -OutputCsv results_ghaw.csv `
  -CachePath .chikiminer-cache.sqlite3 -BatchSize 100 -Concurrency 2
```

Para abrir la consola del dataset:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/open-miner-console.ps1 `
  -Mode dataset -InputCsv results_ghaw.csv `
  -OutputDir huggingface-dataset -Concurrency 2
```

Windows Terminal se usa automáticamente si está instalado; en caso contrario
se abre PowerShell. La ventana permanece abierta al terminar para revisar el
código de salida.

## Output

Se generan:

```text
data/ghaw-dataset/
├── repositories.parquet
├── workflow_files.parquet
├── frontmatter_fields.parquet
├── workflow_bodies.parquet
└── errors.jsonl
```

Los cuatro Parquet se validan antes de escribirse: PK únicas, FK existentes,
JSON válido y exactamente un body por cada workflow file. Si hubo errores, el
dataset parcial se escribe y el comando termina con código distinto de cero;
los detalles quedan en `errors.jsonl`.

## Resume

En cada ejecución se vuelve a listar el directorio para detectar todos los
archivos actuales. El contenido se recupera de SQLite solo cuando coinciden
repositorio, path y `blob_sha`. Si cambió el blob, se descarga nuevamente.

## Ejemplo completo

```powershell
uv run miner results.csv `
  --output results_ghaw.csv `
  --batch-size 100 `
  --concurrency 2 `
  --resume

uv run miner dataset results_ghaw.csv `
  --output-dir huggingface-dataset `
  --cache-path .chikiminer-cache.sqlite3 `
  --concurrency 2 `
  --resume
```
