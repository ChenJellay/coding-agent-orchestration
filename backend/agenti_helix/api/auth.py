"""D1 — Authentication & Authorization.

Provides a FastAPI dependency (`require_auth`) that validates a Bearer token
against `AGENTI_HELIX_API_KEY`. When the env var is unset, auth is bypassed so
local dev still works without configuration.

Role model (simple, no database required):
- `viewer` — all GET endpoints
- `editor` — mutation endpoints (POST/PUT/DELETE)

The role is determined by the token value itself:
- `$AGENTI_HELIX_API_KEY`          → `editor` (full access)
- `$AGENTI_HELIX_VIEWER_API_KEY`   → `viewer` (read-only)

Set `AGENTI_HELIX_AUTH_ENABLED=true` to enforce auth even without an API key
(all requests rejected). Without this flag, missing API key = no auth (dev mode).
"""
from __future__ import annotations

import os
from typing import Literal, Optional

from fastapi import Depends, Header, HTTPException, Query, status


Role = Literal["editor", "viewer"]

_BYPASS_MSG = (
    "Authentication disabled — set AGENTI_HELIX_API_KEY to enable. "
    "Running in unauthenticated development mode."
)


def _editor_key() -> Optional[str]:
    return os.environ.get("AGENTI_HELIX_API_KEY") or None


def _viewer_key() -> Optional[str]:
    return os.environ.get("AGENTI_HELIX_VIEWER_API_KEY") or None


def _auth_enforced() -> bool:
    """Return True when auth must be enforced regardless of key presence."""
    return os.environ.get("AGENTI_HELIX_AUTH_ENABLED", "").lower() in ("1", "true", "yes")


def _resolve_role(token: str) -> Optional[Role]:
    """Map a token string to a role, or None if the token is invalid."""
    editor_key = _editor_key()
    viewer_key = _viewer_key()
    if editor_key and token == editor_key:
        return "editor"
    if viewer_key and token == viewer_key:
        return "viewer"
    return None


def require_auth(authorization: Optional[str] = Header(default=None)) -> Role:
    """FastAPI dependency: validate Bearer token and return the caller's role.

    Usage::

        @app.post("/api/dags/run")
        def run_dag(role: Role = Depends(require_auth)):
            ...
    """
    editor_key = _editor_key()
    if not editor_key and not _auth_enforced():
        # Dev mode: no key configured, auth bypassed.
        return "editor"

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Expected: Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    role = _resolve_role(token.strip())
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired API token",
        )
    return role


def require_auth_sse_friendly(
    authorization: Optional[str] = Header(default=None),
    access_token: Optional[str] = Query(
        default=None,
        description="Same value as Bearer token; for EventSource clients that cannot set headers.",
    ),
) -> Role:
    """Like ``require_auth`` but accepts ``access_token`` query for SSE (browser EventSource)."""
    if access_token and not authorization:
        authorization = f"Bearer {access_token}"
    return require_auth(authorization=authorization)


def require_editor(role: Role = Depends(require_auth)) -> Role:
    """Dependency: editor-level access required (mutations)."""
    if role != "editor":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Editor role required for this operation",
        )
    return role
