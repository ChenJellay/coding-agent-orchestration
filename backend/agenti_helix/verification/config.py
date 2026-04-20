from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class VerificationConfig:
    """Configuration values for the verification loop."""

    judge_base_url: str = "http://localhost:8000"
    judge_timeout_seconds: float = 90.0
    max_retries: int = 2


DEFAULT_CONFIG: Final[VerificationConfig] = VerificationConfig()
