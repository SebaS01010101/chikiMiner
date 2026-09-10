# Modelo entidad-relación

El dataset representa repositorios GH-AW y todos sus archivos Markdown
descubiertos en `.github/workflows/`. Los identificadores son hashes SHA-256
deterministas; no dependen del orden de ejecución.

```mermaid
erDiagram
    repositories ||--o{ workflow_files : contains
    workflow_files ||--o{ frontmatter_fields : has
    workflow_files ||--|| workflow_bodies : contains

    repositories {
        string repository_id PK
        string full_name
        string canonical_name
        string owner
        string repository_name
        string default_branch
    }

    workflow_files {
        string file_id PK
        string repository_id FK
        string filename
        string path
        string blob_sha
        large_string frontmatter_raw
        string content_sha256
        string parse_status
        string parse_error
        string source_url
    }

    frontmatter_fields {
        string field_id PK
        string file_id FK
        string key_path
        string value_type
        large_string value_json
    }

    workflow_bodies {
        string file_id PK, FK
        large_string body_markdown
    }
```

## Cardinalidades

- Un `repository` puede tener cero o muchos `workflow_files`.
- Un `workflow_file` puede tener cero o muchos `frontmatter_fields`.
- Cada `workflow_file` descargado tiene exactamente un `workflow_body`, incluso
  cuando el body está vacío o el frontmatter es inválido.
- Un archivo sin frontmatter no genera fields, pero conserva su body y su
  estado `no_frontmatter`.
- Los errores de listado, descarga o parsing se escriben aparte en
  `errors.jsonl` y no se convierten en falsos registros válidos.
