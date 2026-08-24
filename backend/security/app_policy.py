import os


def _configured_allowlist() -> set[str] | None:
    """None means "no allowlist configured" — every app is allowed, matching
    this project's existing fail-open posture for optional features (Neo4j,
    Langfuse: log a warning and continue rather than block). ALLOWED_APPS is
    read fresh on every call rather than cached at import time so it stays
    consistent with how API_KEY is read in security/auth.py."""
    raw = os.environ.get("ALLOWED_APPS")
    if not raw or not raw.strip():
        return None
    return {name.strip().lower() for name in raw.split(",") if name.strip()}


def is_app_allowed(app_name: str) -> bool:
    allowlist = _configured_allowlist()
    if allowlist is None:
        return True
    return app_name.strip().lower() in allowlist
