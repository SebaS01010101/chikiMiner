# Documentación de chikiMiner

Esta carpeta es la referencia mantenida del proyecto. `README.md` contiene la
introducción rápida; los documentos de aquí explican el uso diario, la
arquitectura, el dataset y el desarrollo.

## Mapa de documentos

| Documento | Contenido |
| --- | --- |
| [Manual de uso](manual-uso.md) | Instalación, configuración, comandos, entradas, salidas, resume y solución de problemas. |
| [Arquitectura](arquitectura.md) | Capas, flujo de datos, componentes, persistencia, estados y límites. |
| [Desarrollo y pruebas](desarrollo.md) | Entorno local, tests, estructura del código y checklist para cambios. |
| [Referencia del dataset](cli.md) | Detalles del comando `dataset`, consola y archivos generados. |
| [Diccionario de datos](data-dictionary.md) | Columnas, tipos, claves y estados de las tablas Parquet. |
| [Modelo entidad-relación](er-diagram.md) | Relaciones y cardinalidades del dataset. |
| [EDA](../eda/README.md) | Instalación y ejecución de notebooks de calidad, exploración y hallazgos. |
| [Registro de cambios](cambios.md) | Cambios relevantes y documentación afectada. |

## Alcance del proyecto

chikiMiner tiene dos flujos principales:

1. Clasifica repositorios GitHub según si contienen un par GH-AW con el mismo
   nombre base, por ejemplo `daily-report.md` y `daily-report.lock.yml`.
2. Consume el CSV clasificado y construye un dataset Parquet con los Markdown
   GH-AW, su frontmatter YAML normalizado y sus cuerpos.

El flujo completo es:

```text
CSV de repositorios
        │
        ▼
miner input.csv --output classified.csv
        │
        ▼
classified.csv con uses_ghaw
        │
        ▼
miner dataset classified.csv --output-dir dataset
        │
        ▼
Parquet + errors.jsonl
```

## Regla de actualización obligatoria

`docs/` forma parte del entregable del proyecto. Cada cambio de código,
configuración, CLI, esquema, salida, dependencia o comportamiento debe dejar
la documentación consistente antes de considerarse terminado:

1. Actualizar el documento afectado junto con el cambio.
2. Actualizar el [registro de cambios](cambios.md) con fecha, descripción y
   archivos/documentos afectados.
3. Actualizar el [manual de uso](manual-uso.md) si cambia un comando, opción,
   entrada, salida, requisito o procedimiento de recuperación.
4. Actualizar [arquitectura](arquitectura.md) si cambia el flujo, una capa,
   la persistencia o la integración con GitHub.
5. Actualizar el diccionario o el ERD si cambia el dataset o sus relaciones.
6. Ejecutar la suite de tests y anotar cualquier limitación en el registro.

Los cambios exclusivamente documentales también deben aparecer en
`cambios.md` cuando modifiquen la forma de usar o entender el proyecto.
