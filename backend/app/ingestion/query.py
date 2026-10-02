"""
app/ingestion/query.py
-----------------------
Module A query service — data-access layer for Module B.
Adapted for Void-brain- schema (Phase 1 transactions table).

Void-brain- transactions schema:
  transaction_id, run_id, source_row, sender_account, receiver_account,
  sender_ifsc, receiver_ifsc, amount DECIMAL(18,2), ts TIMESTAMP,
  payment_mode, device_type, narration, ip_address

The accounts table is created by Module B's schema addition.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import duckdb

from app.config import get_settings

logger = logging.getLogger(__name__)

_SAFE_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")


# ── Helpers ───────────────────────────────────────────────────────────────── #

def _validate_account_id(account_id: str) -> str:
    if not account_id or not _SAFE_ACCOUNT_RE.match(account_id):
        raise ValueError(
            f"Invalid account_id format: {account_id!r}. "
            "Must be 1–32 alphanumeric characters."
        )
    return account_id.upper()


def _clamp_limit(limit: Optional[int]) -> int:
    s = get_settings()
    if limit is None:
        return s.query_default_limit
    return max(1, min(int(limit), s.query_max_limit))


def _encode_cursor(ts: Any, transaction_id: str) -> str:
    ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    return base64.urlsafe_b64encode(
        json.dumps({"ts": ts_str, "tid": transaction_id}).encode()
    ).decode()


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return payload["ts"], payload["tid"]
    except Exception as exc:
        raise ValueError(f"Invalid pagination cursor: {cursor!r}") from exc


def _row_to_dict(keys: list[str], row: tuple) -> dict:
    result: dict = {}
    for k, v in zip(keys, row):
        if isinstance(v, Decimal):
            result[k] = str(v)
        elif isinstance(v, datetime):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result


def _rows_to_dicts(keys: list[str], rows: list[tuple]) -> list[dict]:
    return [_row_to_dict(keys, r) for r in rows]


# ── Column definitions (Phase 1 schema) ──────────────────────────────────── #

_TXN_COLS = """
    t.transaction_id,
    t.sender_account,
    t.receiver_account,
    a_s.account_node_id   AS sender_node_id,
    a_r.account_node_id   AS receiver_node_id,
    t.amount,
    t.ts,
    t.payment_mode,
    t.sender_ifsc,
    t.receiver_ifsc,
    t.narration           AS narration_raw,
    NULL                  AS narration_markers,
    t.ip_address          AS source_ip,
    NULL                  AS ip_anomaly_flags,
    t.device_type,
    'VALID'               AS validation_status,
    NULL                  AS source_file,
    t.source_row          AS source_row_number,
    t.run_id              AS ingestion_batch_id,
    'INR'                 AS currency
"""

_TXN_JOIN = """
    FROM transactions t
    LEFT JOIN accounts a_s ON a_s.normalized_account_id = t.sender_account
    LEFT JOIN accounts a_r ON a_r.normalized_account_id = t.receiver_account
"""

_TXN_KEYS = [
    "transaction_id", "sender_account", "receiver_account",
    "sender_node_id", "receiver_node_id",
    "amount", "ts", "payment_mode", "sender_ifsc", "receiver_ifsc",
    "narration_raw", "narration_markers",
    "source_ip", "ip_anomaly_flags", "device_type",
    "validation_status", "source_file", "source_row_number",
    "ingestion_batch_id", "currency",
]


# ── Query functions ───────────────────────────────────────────────────────── #

def get_account_profile(
    conn: duckdb.DuckDBPyConnection,
    account_id: str,
) -> Optional[dict]:
    """Return account aggregate profile, or None if not found."""
    account_id = _validate_account_id(account_id)

    # First try the accounts table (populated by Module B materialisation)
    row = conn.execute(
        """
        SELECT account_node_id, normalized_account_id,
               first_seen, last_seen,
               incoming_count, outgoing_count,
               incoming_amount, outgoing_amount,
               unique_senders, unique_receivers
        FROM accounts WHERE normalized_account_id = ?
        """,
        [account_id],
    ).fetchone()

    if row is not None:
        keys = ["account_node_id", "normalized_account_id", "first_seen", "last_seen",
                "incoming_count", "outgoing_count", "incoming_amount", "outgoing_amount",
                "unique_senders", "unique_receivers"]
        return _row_to_dict(keys, row)

    # Fallback: compute on-the-fly from transactions table
    r = conn.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE receiver_account = ?) AS incoming_count,
            COUNT(*) FILTER (WHERE sender_account   = ?) AS outgoing_count,
            COALESCE(SUM(amount) FILTER (WHERE receiver_account = ?), 0) AS incoming_amount,
            COALESCE(SUM(amount) FILTER (WHERE sender_account   = ?), 0) AS outgoing_amount,
            COUNT(DISTINCT sender_account)   FILTER (WHERE receiver_account = ?) AS unique_senders,
            COUNT(DISTINCT receiver_account) FILTER (WHERE sender_account   = ?) AS unique_receivers,
            MIN(ts), MAX(ts)
        FROM transactions
        WHERE sender_account = ? OR receiver_account = ?
        """,
        [account_id] * 8,
    ).fetchone()

    if r is None or (r[0] == 0 and r[1] == 0):
        return None

    return {
        "account_node_id": None,
        "normalized_account_id": account_id,
        "first_seen": r[6].isoformat() if r[6] else None,
        "last_seen": r[7].isoformat() if r[7] else None,
        "incoming_count": int(r[0]),
        "outgoing_count": int(r[1]),
        "incoming_amount": str(r[2]),
        "outgoing_amount": str(r[3]),
        "unique_senders": int(r[4]),
        "unique_receivers": int(r[5]),
    }


def get_account_transactions(
    conn: duckdb.DuckDBPyConnection,
    account_id: str,
    *,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    direction: str = "both",
    limit: Optional[int] = None,
    cursor: Optional[str] = None,
) -> dict:
    """Return transactions for account_id with cursor pagination."""
    account_id = _validate_account_id(account_id)
    limit = _clamp_limit(limit)

    if direction not in ("in", "out", "both"):
        raise ValueError(f"direction must be 'in', 'out', or 'both'; got {direction!r}")

    if direction == "in":
        dir_clause = "t.receiver_account = ?"
        dir_params = [account_id]
    elif direction == "out":
        dir_clause = "t.sender_account = ?"
        dir_params = [account_id]
    else:
        dir_clause = "(t.sender_account = ? OR t.receiver_account = ?)"
        dir_params = [account_id, account_id]

    cursor_clause, cursor_params = "", []
    if cursor:
        cts, ctid = _decode_cursor(cursor)
        cursor_clause = "AND (t.ts > ? OR (t.ts = ? AND t.transaction_id > ?))"
        cursor_params = [cts, cts, ctid]

    time_clauses, time_params = "", []
    if start_time:
        time_clauses += " AND t.ts >= ?"; time_params.append(start_time)
    if end_time:
        time_clauses += " AND t.ts <= ?"; time_params.append(end_time)

    rows = conn.execute(
        f"""
        SELECT {_TXN_COLS}
        {_TXN_JOIN}
        WHERE {dir_clause} {cursor_clause} {time_clauses}
        ORDER BY t.ts ASC, t.transaction_id ASC
        LIMIT ?
        """,
        dir_params + cursor_params + time_params + [limit + 1],
    ).fetchall()

    has_more = len(rows) > limit
    page = rows[:limit]
    dicts = _rows_to_dicts(_TXN_KEYS, page)

    next_cursor = None
    if has_more and page:
        last = _row_to_dict(_TXN_KEYS, page[-1])
        next_cursor = _encode_cursor(last["ts"], last["transaction_id"])

    return {"transactions": dicts, "count": len(dicts),
            "next_cursor": next_cursor, "has_more": has_more}


def get_account_counterparties(
    conn: duckdb.DuckDBPyConnection,
    account_id: str,
) -> dict:
    """Return aggregated counterparties for account_id."""
    account_id = _validate_account_id(account_id)
    max_limit = get_settings().query_max_limit

    rows = conn.execute(
        """
        SELECT counterparty, direction,
               COUNT(*)     AS transaction_count,
               SUM(amount)  AS total_amount,
               MIN(ts)      AS first_interaction,
               MAX(ts)      AS last_interaction,
               a.account_node_id AS counterparty_node_id
        FROM (
            SELECT t.receiver_account AS counterparty, 'outgoing' AS direction,
                   t.amount, t.ts
            FROM transactions t WHERE t.sender_account = ?
            UNION ALL
            SELECT t.sender_account AS counterparty, 'incoming' AS direction,
                   t.amount, t.ts
            FROM transactions t WHERE t.receiver_account = ?
        ) sub
        LEFT JOIN accounts a ON a.normalized_account_id = sub.counterparty
        GROUP BY counterparty, direction, a.account_node_id
        ORDER BY total_amount DESC LIMIT ?
        """,
        [account_id, account_id, max_limit],
    ).fetchall()

    keys = ["counterparty_account", "direction", "transaction_count",
            "total_amount", "first_interaction", "last_interaction",
            "counterparty_node_id"]
    return {"account_id": account_id,
            "counterparties": _rows_to_dicts(keys, rows),
            "count": len(rows)}


def get_account_timeline(
    conn: duckdb.DuckDBPyConnection,
    account_id: str,
    *,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: Optional[int] = None,
    cursor: Optional[str] = None,
) -> dict:
    """Return chronological transaction timeline for account_id."""
    account_id = _validate_account_id(account_id)
    limit = _clamp_limit(limit)

    cursor_clause, cursor_params = "", []
    if cursor:
        cts, ctid = _decode_cursor(cursor)
        cursor_clause = "AND (t.ts > ? OR (t.ts = ? AND t.transaction_id > ?))"
        cursor_params = [cts, cts, ctid]

    time_clauses, time_params = "", []
    if start_time:
        time_clauses += " AND t.ts >= ?"; time_params.append(start_time)
    if end_time:
        time_clauses += " AND t.ts <= ?"; time_params.append(end_time)

    rows = conn.execute(
        f"""
        SELECT t.transaction_id, t.ts,
               CASE WHEN t.sender_account = ? THEN 'sent' ELSE 'received' END AS direction,
               CASE WHEN t.sender_account = ? THEN t.receiver_account
                    ELSE t.sender_account END AS counterparty,
               t.amount, 'INR' AS currency, t.payment_mode,
               t.narration AS narration_raw,
               NULL AS narration_markers,
               t.device_type, t.ip_address AS source_ip
        FROM transactions t
        WHERE (t.sender_account = ? OR t.receiver_account = ?)
        {cursor_clause} {time_clauses}
        ORDER BY t.ts ASC, t.transaction_id ASC LIMIT ?
        """,
        [account_id, account_id, account_id, account_id]
        + cursor_params + time_params + [limit + 1],
    ).fetchall()

    has_more = len(rows) > limit
    page = rows[:limit]
    keys = ["transaction_id", "ts", "direction", "counterparty",
            "amount", "currency", "payment_mode",
            "narration_raw", "narration_markers", "device_type", "source_ip"]
    dicts = _rows_to_dicts(keys, page)

    next_cursor = None
    if has_more and page:
        last = _row_to_dict(keys, page[-1])
        next_cursor = _encode_cursor(last["ts"], last["transaction_id"])

    return {"account_id": account_id, "events": dicts, "count": len(dicts),
            "next_cursor": next_cursor, "has_more": has_more}


def get_transaction_by_id(
    conn: duckdb.DuckDBPyConnection,
    transaction_id: str,
) -> Optional[dict]:
    """Return full transaction row by ID, or None."""
    if not transaction_id or len(transaction_id) > 64:
        raise ValueError("transaction_id must be 1–64 characters")
    row = conn.execute(
        f"SELECT {_TXN_COLS} {_TXN_JOIN} WHERE t.transaction_id = ?",
        [transaction_id],
    ).fetchone()
    return _row_to_dict(_TXN_KEYS, row) if row else None


def get_transactions_for_accounts(
    conn: duckdb.DuckDBPyConnection,
    account_ids: list[str],
    *,
    limit: Optional[int] = None,
    cursor: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> dict:
    """Return transactions involving any of the given accounts."""
    if not account_ids:
        raise ValueError("account_ids must not be empty")
    s = get_settings()
    if len(account_ids) > s.query_max_limit:
        raise ValueError(f"Too many account_ids ({len(account_ids)}); max is {s.query_max_limit}")

    validated = list({_validate_account_id(a) for a in account_ids})
    limit = _clamp_limit(limit)
    ph = ", ".join("?" * len(validated))

    cursor_clause, cursor_params = "", []
    if cursor:
        cts, ctid = _decode_cursor(cursor)
        cursor_clause = "AND (t.ts > ? OR (t.ts = ? AND t.transaction_id > ?))"
        cursor_params = [cts, cts, ctid]

    time_clauses, time_params = "", []
    if start_time:
        time_clauses += " AND t.ts >= ?"; time_params.append(start_time)
    if end_time:
        time_clauses += " AND t.ts <= ?"; time_params.append(end_time)

    rows = conn.execute(
        f"""
        SELECT {_TXN_COLS} {_TXN_JOIN}
        WHERE (t.sender_account IN ({ph}) OR t.receiver_account IN ({ph}))
        {cursor_clause} {time_clauses}
        ORDER BY t.ts ASC, t.transaction_id ASC LIMIT ?
        """,
        validated + validated + cursor_params + time_params + [limit + 1],
    ).fetchall()

    has_more = len(rows) > limit
    page = rows[:limit]
    dicts = _rows_to_dicts(_TXN_KEYS, page)

    next_cursor = None
    if has_more and page:
        last = _row_to_dict(_TXN_KEYS, page[-1])
        next_cursor = _encode_cursor(last["ts"], last["transaction_id"])

    return {"transactions": dicts, "count": len(dicts),
            "next_cursor": next_cursor, "has_more": has_more,
            "queried_accounts": validated}


def get_dataset_summary(conn: duckdb.DuckDBPyConnection) -> dict:
    """Return high-level dataset statistics."""
    row = conn.execute("""
        SELECT COUNT(*), COUNT(DISTINCT sender_account), COUNT(DISTINCT receiver_account),
               MIN(ts), MAX(ts), SUM(amount),
               COUNT(DISTINCT payment_mode), COUNT(DISTINCT device_type)
        FROM transactions
    """).fetchone()
    acct_count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    keys = ["total_transactions", "unique_senders", "unique_receivers",
            "earliest_transaction", "latest_transaction",
            "total_amount", "payment_modes", "device_types"]
    summary = _row_to_dict(keys, row)
    summary["total_accounts"] = acct_count
    return summary
