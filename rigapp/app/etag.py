from __future__ import annotations
import hashlib
from typing import Any, Optional

def etag_from_fields(*fields: Any) -> str:
    """
    Build a weak ETag from stable fields (e.g., id, updated_at).
    Usage: ETag: W/"<hash>"
    """
    h = hashlib.sha256()
    for f in fields:
        h.update(str(f).encode('utf-8', 'ignore'))
        h.update(b'\x1e')  # sep
    return f'W/"{h.hexdigest()[:16]}"'

def check_if_match(request_headers: dict, current_etag: Optional[str]) -> bool:
    """
    Returns True if If-Match condition passes (or header absent).
    Returns False if the provided If-Match does not include current_etag.
    """
    if not current_etag:
        return True
    if_match = request_headers.get("if-match") or request_headers.get("If-Match")
    if not if_match:
        return True  # permissive unless we decide to enforce everywhere
    # If-Match can be a comma list; weak compare
    candidates = [p.strip() for p in if_match.split(",")]
    return any(c.strip() == current_etag for c in candidates)
