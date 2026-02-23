"""
Supabase Data API helper utilities.

Supports both read and write operations via PostgREST. Reads default to the
anon key while writes prefer a service-role key when available. Loads
credentials from backend/.env automatically.
"""
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import requests
from dotenv import load_dotenv

# Load .env from backend folder (search upward from this file)
_this_dir = Path(__file__).resolve().parent
_backend_env = _this_dir.parent / ".env"
if _backend_env.exists():
    load_dotenv(_backend_env)
    print(f"[supabase_client] Loaded .env from: {_backend_env}")
else:
    load_dotenv()  # fallback to default search
    print("[supabase_client] Using default dotenv search")

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().strip('"')
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip().strip('"')
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip().strip('"')
FEATURE_SUPABASE_READ = os.getenv("FEATURE_SUPABASE_READ", "false").lower() == "true"
FEATURE_SUPABASE_WRITE = os.getenv("FEATURE_SUPABASE_WRITE", "false").lower() == "true"

logger.info(
    "[supabase_client] URL set: %s, ANON_KEY present: %s, SERVICE_KEY present: %s, "
    "Read enabled: %s, Write enabled: %s",
    bool(SUPABASE_URL),
    bool(SUPABASE_ANON_KEY),
    bool(SUPABASE_SERVICE_ROLE_KEY),
    FEATURE_SUPABASE_READ,
    FEATURE_SUPABASE_WRITE,
)


def _get_read_key() -> str:
    return SUPABASE_ANON_KEY


def _get_write_key() -> str:
    return SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY


def _get_headers(api_key: str, *, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _build_url(table: str) -> str:
    base = SUPABASE_URL.rstrip("/")
    return f"{base}/rest/v1/{table}"


def is_enabled() -> bool:
    """Check if Supabase reads are enabled and configured."""
    return FEATURE_SUPABASE_READ and bool(SUPABASE_URL) and bool(_get_read_key())


def is_write_enabled() -> bool:
    """Check if Supabase writes are permitted and configured."""
    return FEATURE_SUPABASE_WRITE and bool(SUPABASE_URL) and bool(_get_write_key())


def select(
    table: str,
    params: Optional[str] = None,
    columns: str = "*",
    timeout: int = 10
) -> List[Dict[str, Any]]:
    """
    Execute a SELECT query against a Supabase table.
    
    Args:
        table: Table name (e.g., 'inventory')
        params: PostgREST filter string (e.g., 'sku=eq.SKU000001')
        columns: Columns to select (default '*')
        timeout: Request timeout in seconds
    
    Returns:
        List of row dictionaries
    
    Raises:
        Exception on HTTP or network errors
    """
    url = _build_url(table)
    qs = f"?select={columns}"
    if params:
        qs += "&" + params
    
    full_url = url + qs
    
    try:
        resp = requests.get(full_url, headers=_get_headers(_get_read_key()), timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        status = getattr(e.response, "status_code", "N/A")
        body = getattr(e.response, "text", str(e))[:200]
        print(f"[supabase_client] HTTPError {status} on {table}: {body}")
        raise
    except requests.exceptions.RequestException as e:
        print(f"[supabase_client] RequestException on {table}: {e}")
        raise


def select_one(
    table: str,
    params: Optional[str] = None,
    columns: str = "*"
) -> Optional[Dict[str, Any]]:
    """Select a single row (returns first match or None)."""
    rows = select(table, params=params, columns=columns)
    return rows[0] if rows else None


def insert(
    table: str,
    rows: Union[Dict[str, Any], Sequence[Dict[str, Any]]],
    timeout: int = 10,
) -> Optional[List[Dict[str, Any]]]:
    """Insert row(s) into a Supabase table."""
    if not is_write_enabled():
        logger.debug("[supabase_client] Insert skipped; feature disabled")
        return None

    payload: List[Dict[str, Any]]
    if isinstance(rows, dict):
        payload = [rows]
    else:
        payload = list(rows)

    if not payload:
        return None

    url = _build_url(table)
    headers = _get_headers(
        _get_write_key(),
        extra={"Prefer": "return=representation"},
    )

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        if resp.content:
            return resp.json()
        return None
    except requests.exceptions.HTTPError as exc:
        status = getattr(exc.response, "status_code", "N/A")
        body = getattr(exc.response, "text", str(exc))[:400]
        logger.warning(
            "[supabase_client] Insert failed for %s (%s): %s", table, status, body
        )
        raise
    except requests.exceptions.RequestException as exc:
        logger.warning("[supabase_client] Insert request error for %s: %s", table, exc)
        raise


def upsert(
    table: str,
    rows: Union[Dict[str, Any], Sequence[Dict[str, Any]]],
    *,
    conflict_column: Optional[str] = None,
    timeout: int = 10,
) -> Optional[List[Dict[str, Any]]]:
    """Upsert row(s) into a Supabase table."""
    if not is_write_enabled():
        logger.debug("[supabase_client] Write skipped; feature disabled")
        return None

    payload: List[Dict[str, Any]]
    if isinstance(rows, dict):
        payload = [rows]
    else:
        payload = list(rows)

    if not payload:
        return None

    params = ""
    if conflict_column:
        params = f"?on_conflict={conflict_column}"

    url = _build_url(table) + params

    headers = _get_headers(
        _get_write_key(),
        extra={
            "Prefer": "resolution=merge-duplicates,return=representation",
        },
    )

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        if resp.content:
            return resp.json()
        return None
    except requests.exceptions.HTTPError as exc:
        status = getattr(exc.response, "status_code", "N/A")
        body = getattr(exc.response, "text", str(exc))[:400]
        logger.warning(
            "[supabase_client] Upsert failed for %s (%s): %s", table, status, body
        )
        raise
    except requests.exceptions.RequestException as exc:
        logger.warning("[supabase_client] Upsert request error for %s: %s", table, exc)
        raise


def update(
    table: str,
    updates: Dict[str, Any],
    params: str,
    timeout: int = 10,
) -> Optional[List[Dict[str, Any]]]:
    """Update rows in a Supabase table using PATCH."""
    if not is_write_enabled():
        logger.debug("[supabase_client] Update skipped; feature disabled")
        return None

    url = _build_url(table) + "?" + params

    headers = _get_headers(_get_write_key())

    try:
        resp = requests.patch(url, headers=headers, json=updates, timeout=timeout)
        resp.raise_for_status()
        if resp.content:
            return resp.json()
        return None
    except requests.exceptions.HTTPError as exc:
        status = getattr(exc.response, "status_code", "N/A")
        body = getattr(exc.response, "text", str(exc))[:400]
        logger.warning(
            "[supabase_client] Update failed for %s (%s): %s", table, status, body
        )
        raise
    except requests.exceptions.RequestException as exc:
        logger.warning("[supabase_client] Update request error for %s: %s", table, exc)
        raise
