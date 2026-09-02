# chikiMiner: descripción técnica para investigar optimizaciones

> Documento de contexto para una revisión externa con GPT Web.
> Fecha de corte: 2 de septiembre de 2026. Las métricas de red dependen de
> GitHub y pueden variar. El procesamiento completo del CSV todavía no se ha
> declarado terminado.

## 1. Objetivo

chikiMiner clasifica repositorios candidatos de GitHub para detectar si usan
GitHub Agentic Workflows (GH-AW).

Un repositorio cuenta como GH-AW únicamente si, dentro de
.github/workflows/, existe al menos un par de archivos con el mismo nombre
base:

~~~text
daily-report.md
daily-report.lock.yml
~~~

Los siguientes casos no cuentan:

- solo existe el archivo .md;
- solo existe el archivo .lock.yml;
- los nombres base son diferentes;
- el archivo termina en .lock.yaml;
- el nombre está en mayúsculas, por ejemplo report.MD;
- la entrada de GitHub es un directorio y no un archivo.

El programa solo solicita el listado de entradas de .github/workflows/,
específicamente name y type. No descarga el contenido de los workflows, no
clona repositorios y no usa GitHub Code Search.

## 2. Dataset real inspeccionado

El archivo usado en la raíz del proyecto es:

~~~text
results.csv
~~~

Datos observados:

| Métrica | Valor |
| --- | ---: |
| Tamaño del archivo | 641,709,310 bytes |
| Filas | 473,619 |
| Columnas | 35 |
| Referencias válidas | 473,619 |
| Filas inválidas | 0 |
| Repositorios únicos | 473,619 |
| Filas duplicadas | 0 |
| Grupos duplicados | 0 |

La columna de identidad es name, con formato owner/repository. Todas las
filas del dataset real fueron válidas y únicas. Aun así, el parser implementa
deduplicación porque el programa debe funcionar también con otros CSV y no
debe repetir solicitudes si el input contiene duplicados.

Las 35 columnas reales son:

~~~text
id, name, isFork, commits, branches, releases, forks, mainLanguage,
defaultBranch, license, homepage, watchers, stargazers, contributors, size,
createdAt, pushedAt, updatedAt, totalIssues, openIssues, totalPullRequests,
openPullRequests, blankLines, codeLines, commentLines, metrics, lastCommit,
lastCommitSHA, hasWiki, isArchived, isDisabled, isLocked, languages, labels,
topics
~~~

El parser de pandas lee los campos como string, conserva el texto original y
desactiva la conversión automática a NaN. Celdas vacías observadas:

| Columna | Vacías |
| --- | ---: |
| license | 93,333 |
| homepage | 296,693 |
| contributors | 352 |
| blankLines | 1,349 |
| codeLines | 1,349 |
| commentLines | 1,349 |
| metrics | 1,349 |
| languages | 1 |
| labels | 1,858 |
| topics | 235,497 |

Las demás columnas no tenían celdas vacías en la inspección. La columna
defaultBranch existe y no tenía vacíos; actualmente se conserva como dato del
CSV, pero no se usa para construir las consultas GraphQL. Esto es una posible
oportunidad de optimización, descrita más adelante.

## 3. Normalización y deduplicación

La normalización es interna. No modifica results.csv ni los valores que
aparecen en el CSV de salida.

Para la unión y el cache se usa:

~~~text
canonical_key = owner.lower() + "/" + repository.lower()
~~~

Por ejemplo, Microsoft/VSCode y microsoft/vscode producen la misma clave
canónica. RepositoryRef conserva la primera escritura original encontrada
para construir la solicitud a GitHub, mientras que canonical_name se usa como
clave estable.

El flujo de input es:

~~~text
CSV
  -> inspección en chunks de 10,000 filas
  -> detección vectorizada de referencias owner/repository
  -> deduplicación por canonical_key, en orden de primera aparición
  -> consulta remota solo para repositorios pendientes
  -> clasificación asociada a canonical_key
  -> segunda lectura del CSV para conservar las filas y agregar uses_ghaw
~~~

En un CSV con duplicados, GitHub recibe una sola inspección por repositorio.
Todas las filas originales vuelven a aparecer en la salida, conservando orden,
valores y duplicados. La salida agrega la columna uses_ghaw: True para MATCH,
False para NO_MATCH y vacío para NOT_FOUND, errores o referencias inválidas.
Un vacío no significa NO_MATCH.

## 4. Arquitectura actual

La estructura relevante es:

~~~text
src/chikiminer/
  cli.py
  config.py
  models.py
  csv_io.py
  detector.py
  checkpoint.py
  service.py
  experiment.py
  github/
    client.py
    graphql.py
    rest.py
    rate_limit.py
    errors.py
    models.py
~~~

### Responsabilidades

| Módulo | Responsabilidad |
| --- | --- |
| models.py | Modelos Pydantic de dominio y estados explícitos |
| csv_io.py | Lectura pandas por chunks, validación, normalización, deduplicación y output |
| detector.py | Detector puro de GH-AW; no conoce HTTP, pandas ni SQLite |
| github/graphql.py | Construcción y parseo de queries GraphQL |
| github/rest.py | Parseo de Contents API y respuestas REST |
| github/client.py | Cliente HTTPX, batching, retries, rate limit, split y fallback |
| github/rate_limit.py | Lectura de headers y rateLimit GraphQL |
| checkpoint.py | Persistencia SQLite y política de reanudación |
| service.py | Orquestación de CSV, cache, cliente GitHub y detector |
| cli.py | Parámetros Typer, configuración, progreso y exit codes |
| experiment.py | Experimentos reproducibles de backend, batch size y modo GraphQL |

Dependencias de ejecución: HTTPX, pandas, Pydantic, python-dotenv y Typer.
No se usa PyGithub. Los tests usan pytest y los mocks HTTP usan
httpx.MockTransport.

## 5. Modelos y estados

Los principales modelos Pydantic son:

- RepositoryRef(owner, name), con validación de owner/repository y propiedad
  canonical_name;
- WorkflowEntry(name, is_file), que normaliza file de REST y blob de GraphQL;
- RateLimitInfo, con limit, remaining, used, reset_at, resource y cost;
- InspectionResult, que asocia un repositorio con su estado, backend, par
  detectado, ETag, error y número de intentos.

Los estados son:

| Estado | Significado |
| --- | --- |
| MATCH | El listado fue obtenido correctamente y existe un par GH-AW |
| NO_MATCH | El listado fue obtenido correctamente y no existe el par |
| NOT_FOUND | El repositorio no existe o no pudo resolverse |
| FORBIDDEN | GitHub rechazó el acceso; incluye respuestas no clasificables como 451 |
| RATE_LIMITED | Se agotó o se activó un límite de GitHub |
| UNAVAILABLE | Fallo transitorio o servicio no disponible tras los retries |
| ERROR | Error definitivo no clasificado de otra manera |

Un error, timeout, 403 o rate limit nunca se convierte en NO_MATCH. Los
estados no definitivos no tienen uses_ghaw=True/False.

## 6. Detector local

detector.py implementa find_ghaw_pair() y uses_ghaw().

La lógica mantiene dos sets:

~~~text
markdown_bases = set()
lock_bases = set()
~~~

Al observar base.md se guarda base; al observar base.lock.yml se guarda base.
Si el otro set ya contiene la base, el detector termina inmediatamente con
MATCH.

El detector:

- es una función pura;
- compara de forma sensible a mayúsculas y minúsculas;
- considera únicamente los sufijos exactos .md y .lock.yml;
- recibe solo entradas que el servicio identifica como archivos;
- tiene complejidad temporal O(f), donde f es el número de entradas de la
  carpeta;
- usa memoria O(f) en el peor caso;
- tiene salida temprana al encontrar el primer par.

No hace comparaciones entre todos los archivos, por lo que no es O(f²).

## 7. Backend GraphQL seleccionado

La ruta rápida seleccionada es GraphQL por aliases. Una consulta conceptual
de un batch es:

~~~graphql
query ChikiMinerWorkflowTree {
  r0: repository(owner: "foo", name: "bar") {
    object(expression: "HEAD:.github/workflows") {
      ... on Tree {
        entries { name type }
      }
    }
  }
  r1: repository(owner: "openai", name: "example") {
    object(expression: "HEAD:.github/workflows") {
      ... on Tree {
        entries { name type }
      }
    }
  }
  rateLimit { cost limit remaining used resetAt }
}
~~~

El código real escapa los valores de owner y name como strings JSON. Cada
alias se mantiene en un mapa rN -> RepositoryRef; no depende del orden de los
errores devueltos por GitHub.

La consulta solicita únicamente:

- existencia del repositorio;
- árbol de .github/workflows;
- name de cada entrada;
- type de cada entrada;
- datos de rateLimit.

El contenido, URL de descarga y blobs completos no se solicitan.

### Validación de las variantes GraphQL

Se compararon REST Contents, GraphQL HEAD, GraphQL con defaultBranchRef en una
consulta y GraphQL en dos fases. La muestra incluyó repositorios con ramas
main, master, devel, ausencia de workflows, un repositorio GH-AW, un
repositorio de Actions normal y un repositorio inexistente.

Resultados:

1. HEAD:.github/workflows coincidió con REST en la muestra, incluyendo main,
   master, otra rama por defecto, ausencia de la carpeta y repositorio
   inexistente.
2. La variante de una consulta basada en
   defaultBranchRef.target...Commit.file(path: ".github/workflows") produjo
   muchos errores parciales y fallbacks REST. No fue seleccionada.
3. La variante de dos fases funcionó, pero requiere primero obtener el nombre
   de la rama y después consultar el árbol. Duplica aproximadamente los round
   trips y fue más lenta.

Importante: la equivalencia de HEAD con la rama por defecto se validó
experimentalmente, pero la implementación no obtiene explícitamente
defaultBranchRef en la consulta de producción. Si una optimización depende de
una garantía formal sobre la rama, debe volver a validarse contra REST.

## 8. REST Contents y fallback

El backend REST usa:

~~~text
GET /repos/{owner}/{repo}/contents/.github/workflows
~~~

Se omite ref, por lo que GitHub resuelve la rama por defecto según el
comportamiento de Contents API.

Casos importantes:

- 200: se parsea el array de entradas y se conservan solo tipos de archivo;
- 304: se reutiliza la clasificación definitiva guardada junto con el ETag;
- 404: es ambiguo: puede faltar la carpeta o el repositorio;
- ante un 404, se hace una única consulta de metadata del repositorio;
- metadata 200 + Contents 404 significa carpeta ausente y se clasifica como
  listado vacío, es decir NO_MATCH después del detector;
- metadata 404 significa NOT_FOUND;
- 403, 451, 429, 500, 502, 503 y 504 conservan estados de error apropiados.

Los ETags de REST se guardan en SQLite y se envían como If-None-Match en una
futura revalidación. No se hacen requests por cada archivo.

El fallback es por repositorio o alias fallido. Si falla r1, no se manda todo
el batch restante a REST.

## 9. Datos parciales, retries y rate limit

El cliente crea un único httpx.Client de larga duración, con pooling, headers
compartidos y timeouts:

~~~text
connect = 5 s
read    = 20 s
write   = 10 s
pool    = 5 s
~~~

El pool está limitado a 10 conexiones máximas y 5 keep-alive. Se envían
Authorization, Accept, User-Agent y X-GitHub-Api-Version cuando corresponde.
El token se carga desde .env con python-dotenv, nunca se imprime y no se
guarda en SQLite.

Se permiten como máximo 5 intentos por request. Se reintentan, con backoff
exponencial acotado y jitter:

- errores de conexión y timeout;
- errores de lectura/protocolo conocidos de HTTPX;
- 429;
- 502, 503 y 504;
- ciertos 403 que contienen evidencia de rate limit;
- errores GraphQL de rate limit.

Si existe Retry-After, se respeta. Si remaining == 0 y se conoce resetAt, el
cliente espera hasta el reset más un margen. Para límites secundarios sin
cabecera explícita utiliza una espera conservadora. No hay retries infinitos.

GraphQL puede responder HTTP 200 con data y errors al mismo tiempo. El cliente:

1. procesa inmediatamente los aliases con datos válidos;
2. obtiene el alias desde errors[].path, por ejemplo ["r1"];
3. reintenta solo los repositorios de esos aliases;
4. divide un batch cuando el problema es de tamaño, recurso, 502, 504 o
   timeout de transporte;
5. llega hasta batch unitario;
6. usa REST para un fallo GraphQL individual persistente.

La división es recursiva y busca reducir el blast radius de queries grandes.

## 10. Checkpoint SQLite y resume

El archivo por defecto es:

~~~text
.chikiminer-cache.sqlite3
~~~

La tabla es:

~~~sql
CREATE TABLE repository_cache (
    repo TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    uses_ghaw INTEGER NULL,
    checked_at TEXT NOT NULL,
    backend TEXT NULL,
    workflow_pair TEXT NULL,
    etag TEXT NULL,
    error TEXT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
~~~

SQLite usa WAL, synchronous=NORMAL y busy_timeout=5000. Los resultados
remotos se guardan después de cada batch lógico, no solo al final.

Con --resume se reutilizan:

- MATCH;
- NO_MATCH;
- NOT_FOUND.

Se vuelven a intentar en otra ejecución:

- FORBIDDEN;
- RATE_LIMITED;
- UNAVAILABLE;
- ERROR.

La clave del cache es el repositorio canónico. No contiene el token. La
clasificación definitiva no tiene TTL todavía: un resultado viejo puede
quedar obsoleto si cambia el repositorio. El campo checked_at permite añadir
TTL o revalidación posterior.

La salida se escribe en output.csv.tmp y solo se publica mediante os.replace()
cuando termina la lectura completa del CSV. Incluye todas las filas originales
y agrega uses_ghaw. Esto protege el archivo final contra una interrupción,
pero significa que durante una corrida interrumpida las clasificaciones
confirmadas permanecen en SQLite y todavía no en el CSV final.

Ante Ctrl+C, el servicio conserva los checkpoints ya confirmados, espera y
cierra los requests en vuelo de forma controlada, cierra HTTPX y SQLite, y la
CLI devuelve exit code 130. La ejecución puede continuar con --resume.

## 11. Benchmark y evidencia disponible

### Muestra fija de 257 repositorios

El archivo experiment-250.json registra el benchmark de la misma muestra para
todas las variantes. Los tiempos pueden variar entre ejecuciones.

| Estrategia | Requests HTTP | Tiempo aprox. | Cost GraphQL | Partial | Fallback REST | p50 batch | p95 batch |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| REST serial, artefacto guardado | 293 | 92.53 s | — | 0 | 0 | 0.30 s | 0.68 s |
| GraphQL HEAD, b=10 | 29 | 32.64 s | 27 | 2 | 1 | 1.21 s | 1.56 s |
| GraphQL HEAD, b=25 | 14 | 25.06 s | 12 | 2 | 1 | 2.26 s | 3.00 s |
| GraphQL HEAD, b=50 | 9 | 21.72 s | 7 | 2 | 1 | 3.83 s | 4.47 s |
| GraphQL default branch, una fase, b=50 | 86 | 59.59 s | 12 | 74 | 37 | 8.09 s | 16.14 s |
| GraphQL default branch, dos fases, b=50 | 15 | 41.74 s | 13 | 2 | 1 | 7.50 s | 9.03 s |

El REST de ese artefacto tuvo un error transitorio, por lo que su campo formal
de equivalencia quedó en false y el recomendador automático escribió rest. Se
repitió el baseline REST sin errores: aproximadamente 294 requests, 96 s y
1.20 MB. En las ejecuciones exitosas, GraphQL HEAD produjo los mismos
resultados de clasificación que REST para los 256 repositorios existentes y
el repositorio inexistente.

### Batch size y concurrencia

En una muestra posterior de 500 repositorios reales:

| Configuración | Requests | Tiempo | Cost | Splits | Errores |
| --- | ---: | ---: | ---: | ---: | ---: |
| b=50, c=2 | 10 | 20.14 s | 10 | 0 | 0 |
| b=100, c=2 | 5 | 22.39 s | 5 | 0 | 0 |
| b=125, c=2 | 8 | 29.07 s | 6 | 2 | 0 |
| b=50, c=4 | 10 | 12.87 s | 10 | 0 | 0 |
| b=100, c=4 | 5 | 12.95 s | 5 | 0 | 0 |

El límite configurado para batch_size es 100 porque b=125 requirió split y
fue más lento. Aunque c=4 fue más rápido en la muestra pequeña, una prueba a
escala completa con b=100/c=4 produjo 300 errores de transporte del tipo
incomplete chunked read. Se corrigió el manejo de esos errores para
reintentarlos como UNAVAILABLE, se reanudó la ejecución y se recuperaron.
Por eso el perfil operacional actual es c=2: ofrece batching y paralelismo
moderado con un margen de estabilidad mayor. La configuración conservadora de
la CLI sigue siendo c=1.

La métrica GraphQL cost no es equivalente al número de requests. Un batch
grande puede reducir round trips y, dependiendo de la forma de la query, tener
un costo GraphQL diferente. Toda optimización debe medir ambos.

## 12. Estado de la ejecución completa

Última lectura del checkpoint al crear este documento:

~~~text
cache rows: 156,600 / 473,619 repositorios únicos
MATCH:      153
NO_MATCH:   156,327
NOT_FOUND:  104
FORBIDDEN:  16
RATE_LIMITED: 0
UNAVAILABLE:  0
ERROR:       0
~~~

Esto representa aproximadamente 33.1% de los repositorios únicos y deja
317,019 pendientes según esa lectura. El proceso completo aún no debe
considerarse finalizado. Con batch 100, el mínimo teórico restante sería de
3,171 batches GraphQL, sin contar fallbacks, retries ni splits.

El comando operacional usado es:

~~~powershell
uv run miner results.csv --output results_ghaw.csv --cache-path .chikiminer-cache.sqlite3 --backend graphql --graphql-mode head --batch-size 100 --concurrency 2 --resume
~~~

No se debe tomar el conteo anterior como resultado final: cambiará mientras
el proceso avance. El CSV final solo aparece o se reemplaza atómicamente al
terminar.

## 13. Oportunidades de optimización que deben investigar

Estas son hipótesis, no cambios ya aceptados.

### Prioridad alta: usar defaultBranch del propio CSV

El CSV ya trae la columna defaultBranch, sin vacíos en el dataset real. El
código actual ignora ese dato y usa HEAD para evitar una fase adicional. Una
variante podría construir directamente, por alias:

~~~text
object(expression: "<defaultBranch>:.github/workflows")
~~~

Ventajas potenciales:

- elimina la ambigüedad conceptual de HEAD;
- evita la query de dos fases;
- mantiene una sola request GraphQL por batch;
- puede conservar el mismo número de requests y costo bajo.

Riesgos:

- el CSV puede estar desactualizado;
- la rama puede contener caracteres que deban escaparse;
- la referencia puede haber sido renombrada o eliminada;
- una misma clave canónica podría aparecer con metadatos distintos en otro
  dataset;
- un dato incorrecto produciría un falso NO_MATCH.

Experimento recomendado: tomar una muestra fija estratificada, comparar HEAD,
rama del CSV y REST Contents, medir discrepancias por repositorio, probar
ramas main, master, devel, nombres con caracteres especiales, repositorios
vacíos y repositorios sin workflow. Solo aceptar la variante si las
discrepancias se explican y no se convierte ningún error en NO_MATCH.

### Prioridad alta: política adaptativa de batch y concurrencia

El código permite b=1..100 y c=1..4, pero los valores son estáticos durante la
corrida. Investigar un controlador con ventana explícita que:

- comience en b=50 o b=100 y c=2;
- aumente gradualmente solo después de batches sanos;
- reduzca batch o concurrencia ante 502/504, errores de protocolo, secondary
  rate limit o p95 elevado;
- conserve un límite total de requests en vuelo;
- registre la decisión y la razón;
- no vuelva inmediatamente a c=4 después de un error.

Debe compararse con una política fija en la misma muestra y con el mismo
token. No basta con ejecutar una sola vez.

### Prioridad media: cliente asíncrono

Evaluar httpx.AsyncClient solo si reduce de verdad el tiempo manteniendo
backpressure. La prueba con c=4 demostró que más concurrencia puede exponer
fallos de transporte. Una implementación async no debe eliminar:

- el límite de requests en vuelo;
- el commit coordinado de SQLite;
- el split adaptativo;
- el manejo por alias de data y errors;
- la espera por rate limit.

### Prioridad media: evitar relecturas completas innecesarias del CSV

La corrida actual hace, conceptualmente, tres pasadas:

1. inspección y conteo;
2. extracción/deduplicación para consultar GitHub;
3. lectura final para agregar la clasificación y crear el output.

Investigar un manifiesto persistente de repositorios, un fingerprint del input
y/o la combinación segura de pasadas. Cualquier optimización debe seguir
preservando columnas, valores, orden y duplicados del input. Esto optimiza
I/O/CPU local, no reduce directamente el rate limit de GitHub.

### Prioridad media: invalidación del cache

El cache reutiliza estados definitivos sin TTL. Investigar:

- TTL configurable;
- fingerprint del dataset y versión de la regla;
- separación por backend o versión de query;
- revalidación ETag para REST;
- política explícita de --refresh.

La clave debe seguir evitando trabajo duplicado, pero no debe reutilizar
silenciosamente una clasificación de meses atrás si el objetivo es una
fotografía actual.

### Prioridad baja: micro-optimizaciones

Solo medir después de resolver red y cache:

- compresión y tamaño de query;
- GraphQL variables frente a valores embebidos;
- tamaño de chunks pandas;
- costo de validación Pydantic por entrada;
- frecuencia de save_many;
- estructura de índices o pragmas SQLite.

No conviene sacrificar durabilidad del checkpoint por una mejora local pequeña.

## 14. Restricciones de cualquier propuesta futura

Una optimización no puede:

- usar Code Search como algoritmo principal;
- clonar repositorios;
- descargar contenido de workflows;
- hacer una request por archivo;
- hacer cientos de requests simultáneos;
- transformar timeout, 403, 5xx, error GraphQL o rate limit en NO_MATCH;
- eliminar filas del CSV de salida o eliminar duplicados originales;
- modificar valores originales;
- imprimir o persistir el token;
- asumir que menos requests HTTP significa automáticamente menor costo
  GraphQL;
- eliminar el checkpoint por batch;
- omitir la validación experimental contra una referencia confiable.

## 15. Tests y comandos de validación

Última validación local conocida:

~~~text
uv sync --locked       OK
uv build               OK
uv run pytest          59 passed
uv run miner --help    OK
uv run chikiminer --help OK
~~~

Los tests cubren:

- detección de pares y early exit;
- sufijos incorrectos, mayúsculas y directorios;
- normalización y modelos Pydantic;
- CSV, columnas reales, invalidación y output atómico;
- query y parseo GraphQL;
- data parcial y errors por alias;
- null, rate limit y errores HTTP;
- REST 200, 304, 403, 404, 429 y 5xx;
- ETag, If-None-Match y Retry-After;
- split adaptativo y fallback individual;
- deduplicación remota;
- SQLite y resume;
- Ctrl+C y conservación del progreso.

Comandos principales:

~~~powershell
uv sync
uv run pytest
uv run miner --help
uv run python -m chikiminer.experiment results.csv --sample-size 250 --output experiment-nuevo.json
~~~

Para ejecutar el dataset completo se requiere un GITHUB_TOKEN válido en .env,
creado desde .env.example. El archivo .env está ignorado por Git.

## 16. Prompt sugerido para GPT Web

Se puede entregar este documento junto con el repositorio y usar una
instrucción como la siguiente:

~~~text
Actúa como investigador senior de rendimiento para una aplicación Python que
minería repositorios GitHub a gran escala. Lee el contexto de
OPTIMIZACION_PARA_GPT_WEB.md y revisa el código fuente real antes de proponer
cambios.

Objetivo: reducir tiempo total, solicitudes HTTP y consumo de rate limit sin
perder correctitud ni capacidad de resume.

Analiza especialmente:
1. si conviene utilizar la columna defaultBranch del CSV para construir una
   query GraphQL directa por rama;
2. si HEAD es seguro para este caso o debe mantenerse una verificación contra
   REST;
3. cómo optimizar batch size y concurrencia con backpressure;
4. si AsyncClient aportaría algo sin aumentar secondary rate limits;
5. cómo evitar pasadas innecesarias sobre un CSV de 473,619 filas;
6. cómo agregar TTL, fingerprint o refresh al checkpoint sin repetir trabajo;
7. si hay fallos de diseño en partial data, retries, split o fallback.

No propongas Code Search, git clone, descarga de contenidos, requests por
archivo, concurrencia ilimitada ni convertir errores en NO_MATCH.

Entrega:
- diagnóstico de cuellos de botella ordenado por impacto;
- optimizaciones priorizadas con beneficio esperado, riesgo y complejidad;
- experimentos reproducibles sobre la misma muestra;
- métricas que deben registrarse: tiempo, requests, bytes, GraphQL cost,
  p50/p95, splits, errores, fallbacks y remaining;
- criterios de aceptación de correctitud;
- una recomendación final conservadora y otra agresiva;
- cambios de código concretos solo después del análisis.

No asumas que un benchmark aislado representa producción. Distingue siempre
entre evidencia medida, inferencia y propuesta.
~~~

## 17. Limitaciones abiertas

1. El resultado final del dataset completo todavía no está disponible.
2. No hay aún un TTL automático para clasificaciones definitivas.
3. HEAD fue validado experimentalmente, pero no se consulta defaultBranchRef
   en el fast path.
4. La variante GraphQL Commit.file para directorios no resultó estable y
   permanece solo como modo experimental.
5. Las métricas de producción no se persisten en una tabla histórica; se
   observan por consola y en los artefactos de benchmark.
6. El perfil c=4 fue rápido en muestras pequeñas, pero tuvo errores de
   transporte durante una prueba a escala completa; c=2 es la elección
   operacional actual.
7. Las respuestas de GitHub pueden cambiar por repositorios privados,
   permisos, archivado, límites secundarios, cambios de rama o disponibilidad
   temporal.

El criterio correcto para cualquier mejora es: primero resultados equivalentes
a una referencia confiable; después estabilidad y rate limit; finalmente
tiempo y cantidad de requests.
