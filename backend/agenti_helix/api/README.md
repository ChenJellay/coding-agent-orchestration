## `agenti_helix.api`

FastAPI control-plane API consumed by the web UI.

### Purpose

Expose `.agenti_helix/` artifacts and derived views:

- Features list (derived from DAGs + states)
- Triage inbox (blocked items)
- Event log stream
- Checkpoints list/details
- Repo map + rules endpoints (best-effort)

### Runtime configuration

- `AGENTI_HELIX_REPO_ROOT`: repo root whose `.agenti_helix/` artifacts are served

