## `agenti_helix.core`

Core code-intelligence primitives used by all higher layers.

### What belongs here

- Repository scanning + language detection
- AST parsing for symbol/import extraction
- Repo map generation (compact index for retrieval/prompting)
- Deterministic patch/diff primitives (line-based in this repo)

### What does NOT belong here

- No checkpointing / retries
- No judge calls
- No DAG orchestration
- No HTTP APIs

Those belong in `agenti_helix.verification`, `agenti_helix.orchestration`, and `agenti_helix.api`.

