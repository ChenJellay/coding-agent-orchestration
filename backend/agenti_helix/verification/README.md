## `agenti_helix.verification`

Checkpointed, self-verifying execution loop around the single-agent primitive.

### Key concepts

- **EditTaskSpec**: one edit task targeting exactly one file, with acceptance criteria.
- **Checkpoint**: persisted pre/post snapshots + tool logs for a single attempt.
- **Judge**: local HTTP service returning binary `PASS/FAIL` verdicts.
- **Verification loop**: deterministic LangGraph state machine that retries with rollback.

### Persistence

- Checkpoints: `.agenti_helix/checkpoints/*.json`
- Events: `.agenti_helix/logs/events.jsonl`

