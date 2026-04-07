# Security Risks (Frontend ↔ Backend)

This document consolidates security risks found during inspection of the main `frontend/` app, and the backend services under `backend/`.

## Critical

### 1) No authentication / authorization (read + write)
**Where**
- Control-plane API: `backend/agenti_helix/api/main.py`
- Local judge service: `backend/agenti_helix/verification/judge_server.py`

**Why it’s risky**
- All exposed endpoints are callable without any auth checks.
- The main API exposes sensitive internal state and also includes a disk-persisting write endpoint.

**Impact**
- Unauthorized users can read control-plane data (agent prompts/schemas, DAG state, checkpoints, execution events).
- Unauthorized users can modify system prompts, which can alter subsequent agent behavior.
- The judge service can be abused to run model calls and potentially perform unintended file reads (see traversal risk).

**Action-oriented fixes**
1. Add an auth mechanism to both services (pick one and implement consistently).
   - Option A: API key header (easiest for internal deployments).
   - Option B: Bearer token/JWT (better long-term).
2. Require auth for every endpoint that returns or modifies:
   - `GET /api/agents`, `GET /api/agents/{agent_id}`
   - `PUT /api/agents/{agent_id}/prompt`
   - `GET /api/features/{feature_id}`, `GET /api/triage`, `GET /api/events`, `GET /api/checkpoints`
3. Implement role-based authorization (at minimum):
   - `prompt_editor` role required for prompt updates
   - `read_only` role required for all read endpoints
4. Ensure the judge service is not public:
   - Put it behind the same auth boundary as the main API, or
   - Only allow internal network access.

**Verification**
- Attempt requests to protected endpoints without a token: expect `401/403`.
- Ensure the frontend attaches the correct auth header for all API requests.

---

### 2) Prompt updates persist to disk without protection (write primitive)
**Where**
- Frontend writes: `frontend/src/lib/api.ts` (`updateAgentPrompt` uses `PUT /api/agents/{agent_id}/prompt`)
- Backend persists: `backend/agenti_helix/agents/registry.py` (`update_agent_prompt()` writes prompt files)

**Why it’s risky**
- Any caller who can reach the endpoint can permanently alter prompt templates.
- This can introduce prompt injection at the system layer.

**Impact**
- Persistent compromise of agent behavior.
- Potential secondary effects if prompts are later used in orchestration/execution.

**Action-oriented fixes**
1. Require auth + authorization (see Critical #1), and restrict the write endpoint.
2. Add server-side constraints for prompt updates:
   - Max length (e.g., 64KB, tune as appropriate)
   - Reject empty/overly large payloads (`422` or `400`)
   - Optional allowlist of filename/prompt types (ensure only known agents can be updated)
3. Add an audit log entry when a prompt changes:
   - who changed it, timestamp, agent id, and a hash of the new content (do not store the entire prompt in logs if sensitive)

**Verification**
- Ensure update calls are denied without auth.
- Validate length/format errors on malformed payloads.
- Confirm prompt files remain unchanged when requests are rejected.

---

### 3) Potential file traversal / arbitrary file read via path parameters
**Where**
- Main API uses user-controlled identifiers to load artifacts:
  - `backend/agenti_helix/api/main.py` (`get_feature`, `get_checkpoint`, etc. load from `.agenti_helix/` dirs)
- Judge service reads disk using user-controlled `repo_path` and `target_file`:
  - `backend/agenti_helix/verification/judge_server.py` (`judge_endpoint`)

**Why it’s risky**
- Identifiers (`feature_id`, `checkpoint_id`, `dag_id`) are used to compose filesystem paths.
- Judge service does not enforce that `target_file` remains within the resolved `repo_path`.

**Impact**
- Attacker could read unintended files (especially via judge service if traversal is possible).
- Risk increases further if any of these contents are returned to the client.

**Action-oriented fixes**
1. Enforce identifier validation for all artifact-loading endpoints.
   - Use a strict regex allowlist for ids (example policy: only `[A-Za-z0-9._-]` and a bounded length).
2. Enforce directory boundary checks when resolving file paths:
   - Resolve the candidate path and verify it is inside the expected directory.
3. Fix judge service traversal:
   - When handling `repo_path` + `target_file`, resolve both and ensure:
     - `target_path` is within `Path(repo_path).resolve()`
   - Reject if not (return `400`).
4. Apply consistent safety checks to both:
   - main API artifact reading
   - judge service disk reading

**Verification**
- Fuzz test identifiers with traversal payloads (`../`, long strings, null bytes if applicable).
- Ensure all attempts return `400/404` and do not read outside allowed dirs.

---

## High

### 4) CORS configuration mismatch breaks the write endpoint (and increases unsafe exposure patterns)
**Where**
- Backend CORS config: `backend/agenti_helix/api/main.py`
  - `allow_methods=["GET", "POST", "OPTIONS"]` (does not include `PUT`)
- Frontend performs a `PUT`: `frontend/src/lib/api.ts`

**Why it’s risky**
- Browser clients often fail preflight when the server doesn’t allow the needed method.
- Operators may be tempted to broaden CORS further, increasing exposure.

**Impact**
- The prompt editing flow is likely broken in browser environments.
- Incorrect CORS changes can unintentionally widen access.

**Action-oriented fixes**
1. Add `"PUT"` to `allow_methods` only if you truly need it from browsers.
2. Remove broad headers and specify needed headers instead of `allow_headers=["*"]`.
3. Keep CORS restricted to the exact deployed frontend origins.

**Verification**
- Confirm browser preflight succeeds for `PUT /api/agents/{agent_id}/prompt`.
- Ensure other endpoints are not unexpectedly allowed cross-origin.

---

## Medium

### 5) Sensitive internal data exposure without auth
**Where**
- `GET /api/agents`, `GET /api/agents/{agent_id}` return:
  - full prompt text + JSON schemas
- `GET /api/features/{feature_id}` returns:
  - DAG spec, node tasks (target file, acceptance criteria), current state, metrics
- `GET /api/events` returns:
  - execution logs, locations, run/hypothesis ids

**Why it’s risky**
- These endpoints leak internal control-plane details.

**Impact**
- A user can learn system internals and potentially craft follow-on attacks.

**Action-oriented fixes**
- Covered by Critical #1 auth requirements.
- Additionally consider response minimization (only return fields the UI truly needs).

**Verification**
- After auth is implemented, test least-privilege:
  - `read_only` users should not be able to view prompts if those are sensitive in your threat model.

---

### 6) DoS/performance risk from repeated full-file scans
**Where**
- `backend/agenti_helix/api/main.py` reads/parses `events.jsonl` on demand:
  - `_derive_features`, `/api/events`, `/api/triage`, `/api/features/{feature_id}`, `/api/compute`

**Why it’s risky**
- If `events.jsonl` grows, requests can become expensive.
- Frontend polls frequently (2.5–5s intervals).

**Impact**
- Server CPU and disk pressure; degraded UI responsiveness.

**Action-oriented fixes**
1. Add pagination for events where possible:
   - e.g. require `limit` + maybe `sinceTs` (already present, but implement efficient filtering or indexing).
2. Cache derived results for a short TTL:
   - Cache parsed events and/or derived feature/triage views for e.g. 1–5 seconds.
3. Add server-side rate limiting per IP/session (reverse proxy or middleware).

**Verification**
- Simulate large `events.jsonl` and observe request latency.
- Confirm caching reduces repeated parse overhead.

---

## Low / Operational

### 7) Debug logging of raw model output (judge service)
**Where**
- `backend/agenti_helix/verification/judge_server.py`:
  - `_parse_model_json` prints the model’s raw output

**Why it’s risky**
- Logs can become large.
- Raw output might contain sensitive repository text.

**Impact**
- Increased log volume and possible accidental data leakage.

**Action-oriented fixes**
1. Remove `print(...)` or guard behind a secure debug flag (disabled by default).
2. Ensure debug logs cannot be enabled by untrusted clients.

**Verification**
- Confirm no raw model output is printed under normal configuration.

