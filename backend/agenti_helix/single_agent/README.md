## `agenti_helix.single_agent`

The “single file edit” primitive: given an intent and a repo root, it produces and applies a minimal line patch to exactly one existing file.

### Responsibilities

- Build a repo map via `agenti_helix.core`
- Prompt a local model to emit a constrained JSON patch
- Validate the patch shape and target path
- Apply the patch and run lightweight syntax checks

### Consumers

- `agenti_helix.verification`: wraps this primitive with checkpoints + a judge loop

