## Backend (Agenti-Helix Control Plane)

This folder contains the Python backend for Agenti-Helix:

- `agenti_helix/core`: repository map + AST-aware symbol extraction + patch primitives
- `agenti_helix/single_agent`: the “single file edit” harness (local model prompt + patch apply)
- `agenti_helix/verification`: checkpointed verification loop and judge client/server
- `agenti_helix/orchestration`: intent compiler + DAG execution engine
- `agenti_helix/api`: FastAPI server for serving `.agenti_helix/` artifacts to the UI
- `agenti_helix/observability`: event logging utilities

### Running services (examples)

- **Judge service** (local model; provides `POST /judge` and `POST /intent-compiler`):

```bash
uvicorn agenti_helix.verification.judge_server:app --host 127.0.0.1 --port 8000
```

- **Control-plane API** (serves `/api/features`, `/api/triage`, etc.):

```bash
uvicorn agenti_helix.api.main:app --reload --port 8001
```

### Notes

- The backend reads/writes execution artifacts under the repo-root `.agenti_helix/` directory.
- Set `AGENTI_HELIX_REPO_ROOT` to point the API at a different repo root if needed.
