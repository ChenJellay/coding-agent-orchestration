from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class VerificationConfig:
    """Configuration values for the verification loop."""

    judge_base_url: str = "http://localhost:8000"
    judge_timeout_seconds: float = 90.0
    max_retries: int = 2

    # §4.3 — Context pruning: cap raw error history before summarization kicks in.
    max_error_history_chars: int = 4_000

    # §4.4 — Supreme Court: invoke frontier-model arbitration on the last retry before BLOCKED.
    supreme_court_enabled: bool = True


DEFAULT_CONFIG: Final[VerificationConfig] = VerificationConfig()

