## Repo cleanup report (frontend/backend refactor)

This repo was refactored from `phase1/`–`phase4/` into:

- `backend/agenti_helix/*` (Python backend)
- `frontend/*` (Vite React UI)

### Notes

- Old `phase*` folders are deprecated and can be deleted once you no longer need historical references.
- `.agenti_helix/` execution artifacts remain at repo root.

## Repo cleanup report (Phase 1–2 scope)

This repo currently implements **Phase 1** and **Phase 2** from `README.md`. Below are files I reviewed, plus anything that looks **excessive / out-of-scope / likely to surprise you**, so you can manually inspect.

### Removed from codebase (generated artifacts)

- **`.agenti_helix/checkpoints/*.json`**: removed existing checkpoint JSONs (runtime artifacts).
- **`.pytest_cache/README.md`**: removed pytest cache artifact.
- **Added `.gitignore`**: ignores `.agenti_helix/`, `.pytest_cache/`, `.cursor/`, common Python build artifacts.

### Code review, file-by-file flags

#### Phase 1 core (`agenti_helix/`)

- **`agenti_helix/repo_scanner.py`**
  - **OK / in-scope**: repository walk + language detection for JS/TS/Python (matches Phase 1 “walk repo”).
  - **Potential excess**: uses `Path.rglob("*")` without ignores; will scan vendored/large dirs if present (node_modules, .venv, etc.).

- **`agenti_helix/ast_parser.py`**
  - **OK / in-scope**: AST parsing + symbol extraction + import collection (matches Phase 1 “AST-aware chunking” beginnings).
  - **Potential excess / mismatch**:
    - Only extracts **top-level** defs; no AST “full function/class chunking” output yet (it’s symbol extraction, not chunk retrieval).
    - JS import collection includes `require()` only in a narrow pattern.

- **`agenti_helix/repo_map.py`**
  - **OK / in-scope**: generates compressed file/symbol index (Repo Map).
  - **Potential excess**: stores `imports` but doesn’t build any dependency graph edges yet (README Phase 1 mentions “basic dependency graph”).

- **`agenti_helix/diff_builder.py`**
  - **OK / in-scope**: minimal line-range patch application helper.
  - **Manual check**: patch validation is strict about line ranges; any model that returns endLine beyond EOF will hard-fail.

- **`agenti_helix/cli.py`**
  - **OK / in-scope**: utility CLI to generate repo map and apply JSON patch.

#### Phase 1 demo harness

- **`single_agent_harness.py`**
  - **Mostly in-scope**: “single-agent executor” that produces a patch JSON and applies it.
  - **Excess / surprise**:
    - Implements a **local MLX model for the coder** (`mlx_lm` + HF model id). README/Blueprint emphasize local models for *judging*; coding model choice is not specified, but this is a heavier demo dependency.
    - Prints **raw model output** to stdout unconditionally (no log level / toggle).
    - JSON extraction is “first `{...}` block” with naïve brace matching; can break on braces inside strings.

#### Phase 2 (`phase2/`)

- **`phase2/checkpointing.py`**
  - **OK / in-scope**: checkpoint JSON persistence + rollback semantics.
  - **Potential excess**: checkpoint dir is always under repo root `./.agenti_helix/checkpoints`; fine, but ensure it’s treated as runtime artifact (now ignored).

- **`phase2/judge_server.py`**
  - **OK / in-scope**: local Judge HTTP service returning PASS/FAIL JSON.
  - **Excess / surprise**:
    - Hard dependency on `mlx_lm` at import time (`import mlx_lm`), which makes “just importing the module” fail on machines without MLX.
    - Logs raw model output to stdout unconditionally.
    - JSON parsing is the same “first `{...}` block” heuristic; braces-in-strings risk.

- **`phase2/judge_client.py`**
  - **OK / in-scope**: thin HTTP client, returns FAIL on transport/JSON errors.
  - **Manual check**: `problematic_lines = [int(x) for x in problematic_lines_raw]` will raise if Judge returns non-ints (server normalizes, but any other judge impl could break client).

- **`phase2/verification_loop.py`**
  - **OK / in-scope**: LangGraph state machine for checkpoint → coder → static checks → judge → retry/rollback.
  - **Excess / surprise**:
    - Imports `langgraph` but `requirements.txt` doesn’t list it (likely missing dependency).
    - Static checks step is a placeholder that always “SKIPPED”.
    - Logging hooks exist (see `phase2/debug_log.py`)—observability is a later-phase theme; not harmful but adds surface area.

- **`phase2/debug_log.py`**
  - **Was excessive**: previously hardcoded an absolute path into your machine.
  - **Fixed**: now writes to `./.agenti_helix/logs/events.jsonl` by default, and can be disabled via `AGENTI_HELIX_DISABLE_LOGGING=1`.

- **`phase2/config.py`, `phase2/cli.py`, `phase2/__init__.py`**
  - **OK / in-scope**: demo config + CLI wiring for phase 2.

#### Tests

- **`tests/test_phase2_checkpointing.py`, `tests/test_phase2_verification_loop.py`, `conftest.py`**
  - **OK / in-scope**: basic unit tests; verification loop test smartly monkeypatches the coder and judge.

#### Demo repo (`demo-repo/`)

- **`demo-repo/src/index.js`, `demo-repo/src/components/header.js`**
  - **OK / in-scope** as a tiny target project for Phase 1/2 demos.

### Top issues to manually inspect (highest signal)

- **Missing dependency**: `phase2/verification_loop.py` imports `langgraph`, but `requirements.txt` doesn’t include it.
- **Hard-to-run deps**: `mlx_lm` is used for both coder and judge; this is Mac/MLX-specific and may not align with your intended portability.
- **Logging/telemetry surface**: raw model output `print()`s in `single_agent_harness.py` and `phase2/judge_server.py`.

