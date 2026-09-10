# Manual de uso

## 1. Qué hace chikiMiner

chikiMiner procesa una lista de repositorios GitHub en dos etapas:

- **Clasificación:** inspecciona `.github/workflows/` y marca `uses_ghaw=True`
  solo cuando encuentra un archivo `.md` y otro `.lock.yml` con el mismo nombre
  base.
- **Dataset:** para los repositorios clasificados como `True`, vuelve a listar
  el directorio, descarga los Markdown, separa el frontmatter YAML del cuerpo y
  escribe cuatro tablas Parquet relacionadas.

La clasificación no clona repositorios, no descarga contenido de workflows y no
usa GitHub Code Search. La descarga de contenido ocurre únicamente en la etapa
del dataset.

## 2. Requisitos e instalación

Se necesita:

- Windows PowerShell, Git y Python 3.11 o superior.
- `uv` para instalar y ejecutar el entorno recomendado.
- Un token de GitHub con permisos para consultar los repositorios objetivo.

Desde la raíz del proyecto:

```powershell
uv sync
Copy-Item .env.example .env
```

Editar `.env` y definir el token:

```dotenv
GITHUB_TOKEN=pegar_el_token_aqui
```

El archivo `.env` está ignorado por Git. El token se carga con
`python-dotenv`, no se imprime y no debe copiarse en logs, issues ni artefactos.

Si `uv` no está disponible pero existe el entorno local, se puede sustituir
`uv run miner` por `\.venv\Scripts\python.exe -m chikiminer` y
`uv run pytest` por `\.venv\Scripts\python.exe -m pytest`.

## 3. Entrada para la clasificación

El CSV debe tener una columna `name` con referencias en formato exacto
`owner/repository`:

```csv
id,name,defaultBranch
1,octo-org/example,main
2,another-org/project,master
```

Se conservan todas las columnas y el orden de las filas. La herramienta:

- procesa el CSV por bloques para no cargarlo completo en memoria;
- normaliza la clave interna a minúsculas;
- deduplica repositorios para las consultas remotas;
- conserva filas duplicadas en la salida;
- deja vacía la clasificación de referencias inválidas o estados no resueltos.

También se puede usar otra columna con referencias desde la API Python, pero el
comando CLI espera `name`.

## 4. Ejecutar la clasificación

Comando recomendado:

```powershell
uv run miner results.csv --output results_ghaw.csv
```

Alias compatible:

```powershell
uv run chikiminer results.csv --output results_ghaw.csv
```

La salida contiene todas las columnas originales y añade `uses_ghaw`:

| Valor | Significado |
| --- | --- |
| `True` | Se encontró el par GH-AW. |
| `False` | El repositorio existe y no se encontró el par. |
| vacío | Repositorio no encontrado, acceso denegado, error o referencia inválida. |

El comando termina con código distinto de cero si quedan repositorios sin una
clasificación definitiva, aunque conserva el CSV parcial y el checkpoint.

### Opciones de clasificación

| Opción | Default | Uso |
| --- | --- | --- |
| `--output`, `-o` | requerido | CSV clasificado de salida. |
| `--batch-size` | `50` | Alias GraphQL por lote, entre 1 y 100. |
| `--concurrency` | `1` | Ventana de solicitudes concurrentes, entre 1 y 4. |
| `--resume/--no-resume` | `--resume` | Reutiliza estados definitivos guardados. |
| `--cache-path` | `.chikiminer-cache.sqlite3` | Checkpoint SQLite. |
| `--backend` | `graphql` | `graphql` o `rest`. |
| `--graphql-mode` | `head` | `head`, `default-branch` o `two-phase`. |
| `--adaptive` | desactivado | Ajusta lote y concurrencia según presión observada. |

Perfil explícito de mayor rendimiento medido:

```powershell
uv run miner results.csv `
  --output results_ghaw.csv `
  --batch-size 100 `
  --concurrency 2 `
  --resume
```

Perfil adaptativo:

```powershell
uv run miner results.csv `
  --output results_ghaw.csv `
  --adaptive `
  --resume
```

El modo adaptativo comienza normalmente en `100/2` y puede cambiar entre las
políticas `SAFE` (`50/1`), `DEGRADED` (`50/2`), `NORMAL` (`100/2`) y `FAST`
(`100/4`). La concurrencia sigue limitada; no se crea un pool ilimitado.

## 5. Resume y checkpoint

Cada clasificación se persiste en SQLite después de los lotes lógicos. El
checkpoint conserva, entre otros datos, el repositorio, el estado, el backend,
el par detectado, el ETag y el número de intentos.

Con `--resume`, se reutilizan `MATCH`, `NO_MATCH` y `NOT_FOUND`. Los estados
`FORBIDDEN`, `RATE_LIMITED`, `UNAVAILABLE` y `ERROR` se vuelven a intentar en
una ejecución posterior. Es seguro relanzar el mismo comando después de una
interrupción:

```powershell
uv run miner results.csv --output results_ghaw.csv --resume
```

Usar `--no-resume` fuerza la revalidación remota, pero no elimina el archivo de
checkpoint. No borrar `.chikiminer-cache.sqlite3` salvo que se quiera comenzar
una ejecución completamente nueva.

La salida se escribe primero en `results_ghaw.csv.tmp` y solo se reemplaza el
archivo final cuando la escritura completa termina correctamente.

## 6. Construir el dataset Parquet

La segunda etapa requiere el CSV clasificado, no el CSV original:

```powershell
uv run miner dataset results_ghaw.csv `
  --output-dir huggingface-dataset `
  --resume `
  --concurrency 2
```

El comando solo procesa filas con `uses_ghaw=True` o `uses_ghaw=1` (ambas
representaciones de una coincidencia). Lista nuevamente
`.github/workflows/`, selecciona archivos cuyo nombre termine exactamente en
`.md`, reutiliza contenido cacheado cuando coincide el `blob_sha` y descarga el
resto mediante la API Contents de GitHub.

Se generan:

```text
huggingface-dataset/
├── repositories.parquet
├── workflow_files.parquet
├── frontmatter_fields.parquet
├── workflow_bodies.parquet
└── errors.jsonl
```

La descripción de cada columna está en el [diccionario de datos](data-dictionary.md)
y las relaciones en el [modelo ER](er-diagram.md).

El comando falla de forma explícita si recibe un CSV sin `uses_ghaw`; esto evita
descargar contenido de todos los candidatos por accidente.

## 7. Consola de progreso

En una terminal interactiva se actualiza una única fila horizontal:

```text
T2 25.00% [###-----------] 25/100 | 5.0/s | ETA 15s | M:2 E:1 C:10 B:50 RL:4900
```

Abreviaturas principales:

- `M`: coincidencias GH-AW.
- `E`: errores o repositorios sin resolver.
- `C`: resultados reutilizados desde cache.
- `B`: tamaño de lote actual.
- `RL`: rate limit restante de GraphQL.
- `MD`, `F`, `E`, `P`: Markdown, campos, errores y pendientes del dataset.

La barra se adapta al ancho de la terminal para evitar que el estado se
convierta en una columna. Si la salida se redirige a un archivo, se conserva
una línea por actualización para facilitar auditoría:

```powershell
uv run miner results.csv --output results_ghaw.csv > miner.log
```

Para abrir una consola Windows separada:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/open-miner-console.ps1 `
  -Mode task2 `
  -InputCsv results.csv `
  -OutputCsv results_ghaw.csv `
  -CachePath .chikiminer-cache.sqlite3 `
  -BatchSize 100 `
  -Concurrency 2
```

Para el dataset:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/open-miner-console.ps1 `
  -Mode dataset `
  -InputCsv results_ghaw.csv `
  -OutputDir huggingface-dataset `
  -Concurrency 2
```

## 8. Experimentos y benchmark

Antes de cambiar backend, lote o concurrencia en un dataset grande, se puede
ejecutar el benchmark reproducible sobre una muestra:

```powershell
uv run python -m chikiminer.experiment `
  results.csv `
  --sample-size 250 `
  --output experiment.json
```

El reporte compara REST y GraphQL, contabiliza solicitudes, tiempo, coste
GraphQL, errores, fallbacks y equivalencia de resultados. Los experimentos no
modifican el CSV principal.

## 9. Solución de problemas

### Falta `GITHUB_TOKEN`

Verificar que `.env` existe en la raíz, contiene `GITHUB_TOKEN=...` y que el
proceso se ejecuta desde la raíz del proyecto. El valor nunca debe escribirse en
la documentación ni en un issue.

### El dataset rechaza el input

Usar la salida de la primera etapa (`results_ghaw.csv`) y comprobar que tiene
`name` y `uses_ghaw`. El archivo original sin clasificar no es válido para la
segunda etapa.

### Quedan estados sin resolver

Conservar el checkpoint y volver a ejecutar con `--resume`. Revisar el rate
limit de GitHub y reducir temporalmente `--batch-size` o `--concurrency`. Un
error remoto no se convierte en `False` automáticamente.

### La salida de consola se ve vertical

Usar Windows Terminal o ampliar la ventana. El renderizador actual reduce la
barra al ancho disponible; las salidas redirigidas son deliberadamente
multilínea, una actualización por línea.

### Validar la instalación

```powershell
uv run miner --help
uv run miner dataset --help
uv run pytest
```

## 10. Exploratory data analysis

La EDA usa el snapshot estructurado de `huggingface-dataset/` y no vuelve a
consultar GitHub. El snapshot actual contiene 374 repositorios, 1.541 archivos
Markdown, 60.038 nodos de frontmatter y 1.541 bodies. Instalar las
dependencias específicas y abrir JupyterLab. El snapshot publicado está en
[SebaS01010101/GHAW-H](https://huggingface.co/datasets/SebaS01010101/GHAW-H);
no se utiliza el dataset temporal de `pavtch/GHAW-H`.

```powershell
uv sync --group eda
uv run --group eda python -m ipykernel install --user --name chikiminer-eda --display-name "Python (chikiMiner EDA)"
uv run --group eda jupyter lab
```

Ejecutar `eda/01_descripcion_y_calidad.ipynb` para revisar descripción,
esquema, nulos, claves, relaciones y parsing. Después ejecutar
`eda/02_exploracion_y_hallazgos.ipynb` para explorar distribución por
repositorio, frontmatter, body y las dos preguntas exploratorias. Notebook 2
es independiente y vuelve a cargar los cuatro Parquet. Ambos notebooks
aceptan la variable `CHIKIMINER_EDA_DATA_DIR` para apuntar a otra copia. La
guía completa está en [`eda/README.md`](../eda/README.md).

Para conservar resultados, reiniciar el kernel, ejecutar todas las celdas y
guardar cada notebook. No se genera `eda/data/processed/`; las tablas
derivadas se construyen en memoria.
