# Agenti-Helix

**Agenti-Helix** is an AI-native SDLC control plane: intent is compiled into a DAG of micro-tasks, agents run through a checkpointed verification loop with a local judge service, and a web UI exposes the feature board, DAG detail, triage, and sign-off review. Layered architecture and phased roadmap: [`Agenti-Helix Architecture Blueprint.md`](Agenti-Helix%20Architecture%20Blueprint.md).

---

## Team members
Jerry Chen, Meghana Dhruv, Michelle Zheng, Shriya Jejukar


---

## Selected track A


Track-specific interaction flow, branch logic, and UI walkthroughs are documented in [`deliverables/09_track_additions.md`](deliverables/09_track_additions.md).

---

## Setup instructions

1. **Clone the repository** and work from the repository root unless noted.

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   cd frontend && npm install && cd ..
   ```

3. **Configure environment**

   ```bash
   cp backend/.env.example backend/.env
   cp frontend/.env.example frontend/.env.local
   ```

   Edit the copies and set at least:

   | Variable | Purpose |
   |----------|---------|
   | `AGENTI_HELIX_REPO_ROOT` | Absolute path to the repo agents edit (often `demo-repo/`) |
   | `QWEN_MODEL_PATH` | Local MLX model directory (Apple Silicon) |
   | `OPENAI_API_KEY` | Optional if `AGENTI_HELIX_BACKEND_TYPE=openai` |
   | `VITE_API_BASE_URL` | Frontend → API (default `http://127.0.0.1:8001`) |
   | `VITE_API_KEY` / `AGENTI_HELIX_API_KEY` | Bearer auth when enabled |
   | `AGENTI_HELIX_CORS_ORIGINS` | Browser origins allowed by the API |
   | `AGENTI_HELIX_ALLOWED_REPO_ROOTS` | Optional path prefixes for dashboard `repo_path` |
   | `AGENTI_HELIX_JUDGE_SERVICE_TOKEN` | Optional; judge requires `X-Agenti-Helix-Judge-Token` when set |
   | `AGENTI_HELIX_SANDBOX_ENABLED` | Logs Docker isolation intent (executor not fully wired) |

   **Control-plane vs target repo:** `AGENTI_HELIX_REPO_ROOT` holds `.agenti_helix/` (DAG specs, state, events). The dashboard **Run command** `repo_path` is the workspace being edited; keep them aligned unless you intend a split layout.

---

## How to run or access the project

**Recommended (all services):**

```bash
./scripts/start-dev.sh
# optional custom repo:
./scripts/start-dev.sh --repo /path/to/your/repo
```

This starts:

- **Judge service** → `http://127.0.0.1:8000`
- **Control-plane API** → `http://127.0.0.1:8001`
- **Frontend** → `http://localhost:5173`

**Manual (API + UI separately):**

```bash
cd backend && python -m uvicorn agenti_helix.api.main:app --host 127.0.0.1 --port 8001 --reload

cd frontend && npm run dev
```

Start the judge on `:8000` the same way if you are not using `./scripts/start-dev.sh`:

```bash
cd backend && python -m uvicorn agenti_helix.verification.judge_server:app --host 127.0.0.1 --port 8000 --reload
```

If the API is not on `127.0.0.1:8001`, set `VITE_API_BASE_URL` in `frontend/.env.local`.

**Demo flow:** Open [http://localhost:5173](http://localhost:5173), use **Run command**, set repo path (e.g. `../demo-repo`) and a macro intent such as: *Add input validation to the login form in header.js*.

**Headless evaluation** (Judge + API running, `AGENTI_HELIX_REPO_ROOT` pointing at `demo-repo/`):

```bash
python scripts/eval/headless_eval.py --tags stable
python scripts/eval/headless_eval.py --tags all
```

`stable` runs S1–S3, S5–S7; `all` also includes the LLM-heavy escalation scenario (S4).

**Tests / CI** (from repository root):

```bash
pip install -r requirements.txt
cd frontend && npm install && npm run lint && npm run build && cd ..
PYTHONPATH=backend pytest backend/tests tests -q
```

**Ports / cleanup:** Check listeners with `lsof -nP -iTCP:8000 -iTCP:8001 -iTCP:5173 -sTCP:LISTEN`; stop the dev script with Ctrl+C (it tears down child processes).

---

## Required dependencies or platforms

- **Python** 3.11+
- **Node.js** 20+
- **Inference:** Apple Silicon Mac with MLX and a local model path **or** OpenAI API (per `backend/.env.example`)
- **OS:** Development scripts assume a Unix-like shell (macOS/Linux)

---

## Folder guide

| Path | Role |
|------|------|
| [`backend/`](backend/) | FastAPI control plane, orchestration, verification loop, judge client |
| [`frontend/`](frontend/) | Vite + React/TypeScript dashboard (Features, DAG, Triage, Sign-Off, etc.) |
| [`demo-repo/`](demo-repo/) | Sample target application and **eval** manifest (`eval/scenarios.json`, fixtures) |
| [`scripts/`](scripts/) | `start-dev.sh`, `scripts/eval/headless_eval.py`, adapters |
| [`deliverables/`](deliverables/) | Architecture, roles, coordination, evaluation plan, risks, track additions |
| [`docs/`](docs/) | Supplementary technical notes |
| [`tests/`](tests/) | Top-level Python tests (e.g. orchestrator) |

---

## Summary of evaluation materials

- **Plan:** [`deliverables/06_evaluation_plan.md`](deliverables/06_evaluation_plan.md) — seven scenarios (cosmetic patch, retry + memory, Supreme Court, human escalation, security block, multi-node cascade, full TDD build), global success criteria, automated matrix (S1–S7), operator runbook, rubric dimensions.
- **Manifest:** [`demo-repo/eval/scenarios.json`](demo-repo/eval/scenarios.json) — scenario definitions, tags (`stable` / `llm`), execution order.
- **Harness:** [`scripts/eval/headless_eval.py`](scripts/eval/headless_eval.py); unit coverage in [`backend/tests/unit/test_headless_eval.py`](backend/tests/unit/test_headless_eval.py).
- **Sample report:** [`demo-repo/eval/full-report.md`](demo-repo/eval/full-report.md) (example narrative where present).

---

## Summary of outputs included

- **Per eval batch:** `demo-repo/.agenti_helix/eval/last-run.json` (machine-readable results, rubric) and `last-run.md` (human-readable summary). Paths live under the configured repo’s `.agenti_helix/` (often gitignored).
- **Runtime traces:** `events.jsonl`, DAG/checkpoint state under `.agenti_helix/` for reviewed runs.
- **Deliverable docs:** Markdown set under [`deliverables/`](deliverables/) (architecture diagram, role definitions, tools/memory, prototype notes, evaluation plan, risk/governance, contribution update, track additions).

---

## Known limitations

- **Intent compilation failures:** Invalid or empty DAG JSON from the LLM may leave no `dag_id` on the feature board and little UI feedback (see [`deliverables/09_track_additions.md`](deliverables/09_track_additions.md) and risk plan R5).
- **Sandbox:** `AGENTI_HELIX_SANDBOX_ENABLED` reflects intent; ephemeral Docker execution is not fully wired end-to-end.
- **S4 (escalation):** Optional in automation; may flake if the coder does not emit the escalation signal (`--tags stable` omits it by default).
- **Sign-off intent edits:** Updating macro intent after a run does not automatically re-execute completed nodes.
- **Security / ops:** Treat dev mode as local-trust; hardening notes appear in [`security_risks.md`](security_risks.md) and [`deployment-gaps.md`](deployment-gaps.md).
