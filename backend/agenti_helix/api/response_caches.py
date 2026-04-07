"""Short-lived TTL caches for expensive GET handlers; invalidate on mutations."""

from __future__ import annotations

try:
    from cachetools import TTLCache  # type: ignore[import]

    CACHE_AVAILABLE = True
    FEATURES_CACHE: TTLCache = TTLCache(maxsize=1, ttl=5)
    TRIAGE_CACHE: TTLCache = TTLCache(maxsize=1, ttl=5)
except ImportError:
    CACHE_AVAILABLE = False
    FEATURES_CACHE = {}  # type: ignore[assignment]
    TRIAGE_CACHE = {}  # type: ignore[assignment]


def invalidate_features_and_triage_caches() -> None:
    """Call after DAG/task mutations so the next poll sees fresh disk state."""
    if CACHE_AVAILABLE:
        FEATURES_CACHE.clear()
        TRIAGE_CACHE.clear()
    else:
        FEATURES_CACHE.clear()
        TRIAGE_CACHE.clear()
