"""
app/api/routes/accounts.py
---------------------------
Account-level query endpoints for Module C investigator UI.

GET  /v1/accounts                    - paginated list with risk pre-filter
GET  /v1/accounts/{id}               - full account profile
GET  /v1/accounts/{id}/transactions  - paginated transactions
GET  /v1/accounts/{id}/counterparties
GET  /v1/accounts/{id}/timeline
GET  /v1/accounts/{id}/graph         - focused subgraph for this account
"""

from __future__ import annotations

import logging
from typing import Optional

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.analytics import get_analytics_db
from app.ingestion.query import (
    get_account_profile,
    get_account_transactions,
    get_account_counterparties,
    get_account_timeline,
    get_dataset_summary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/accounts", tags=["accounts"])


def _ok(data, **meta):
    return {"success": True, "data": data, **meta}


# ── Dataset summary ────────────────────────────────────────────────────────── #

@router.get("/summary")
def dataset_summary(adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db)):
    """High-level dataset statistics for the dashboard."""
    try:
        summary = get_dataset_summary(adb)
    except Exception as exc:
        logger.error("dataset_summary error: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to retrieve dataset summary.")
    return _ok(summary)


# ── Accounts list ──────────────────────────────────────────────────────────── #

@router.get("")
def list_accounts(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None, max_length=64),
    sort_by: str = Query("incoming_amount", regex="^(incoming_amount|outgoing_amount|incoming_count|outgoing_count|unique_senders|unique_receivers)$"),
    order: str = Query("desc", regex="^(asc|desc)$"),
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """
    Paginated list of accounts with pre-computed aggregates.
    Used by Suspicious Accounts table in Module C.
    """
    try:
        where_clause = ""
        params: list = []
        if search:
            where_clause = "WHERE normalized_account_id LIKE ?"
            params.append(f"%{search.upper()}%")

        order_dir = "DESC" if order == "desc" else "ASC"

        rows = adb.execute(
            f"""
            SELECT
                normalized_account_id,
                account_node_id,
                first_seen,
                last_seen,
                incoming_count,
                outgoing_count,
                CAST(incoming_amount AS DOUBLE)  AS incoming_amount,
                CAST(outgoing_amount AS DOUBLE)  AS outgoing_amount,
                unique_senders,
                unique_receivers,
                CASE WHEN incoming_amount > 0
                     THEN CAST(outgoing_amount AS DOUBLE) /
                          CAST(incoming_amount AS DOUBLE)
                     ELSE NULL END AS pass_through_ratio
            FROM accounts
            {where_clause}
            ORDER BY {sort_by} {order_dir} NULLS LAST
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()

        total_row = adb.execute(
            f"SELECT COUNT(*) FROM accounts {where_clause}", params
        ).fetchone()
        total = int(total_row[0]) if total_row else 0

        keys = [
            "account_id", "account_node_id", "first_seen", "last_seen",
            "incoming_count", "outgoing_count", "incoming_amount", "outgoing_amount",
            "unique_senders", "unique_receivers", "pass_through_ratio",
        ]
        data = []
        for row in rows:
            d = dict(zip(keys, row))
            d["first_seen"] = d["first_seen"].isoformat() if d["first_seen"] else None
            d["last_seen"]  = d["last_seen"].isoformat()  if d["last_seen"]  else None
            d["incoming_amount"] = round(d["incoming_amount"] or 0, 2)
            d["outgoing_amount"] = round(d["outgoing_amount"] or 0, 2)
            d["pass_through_ratio"] = round(d["pass_through_ratio"], 4) if d["pass_through_ratio"] else None
            data.append(d)

    except Exception as exc:
        logger.error("list_accounts error: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to list accounts.")

    return _ok(data, total=total, limit=limit, offset=offset)


# ── Account profile ────────────────────────────────────────────────────────── #

@router.get("/{account_id}")
def account_profile(
    account_id: str,
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """Full account profile including aggregates."""
    try:
        profile = get_account_profile(adb, account_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        logger.error("account_profile error: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to retrieve account profile.")
    if profile is None:
        raise HTTPException(404, f"Account {account_id} not found.")
    return _ok(profile)


# ── Transactions ───────────────────────────────────────────────────────────── #

@router.get("/{account_id}/transactions")
def account_transactions(
    account_id: str,
    limit: int = Query(100, ge=1, le=1000),
    cursor: Optional[str] = Query(None),
    direction: str = Query("both", regex="^(in|out|both)$"),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """Cursor-paginated transactions for an account."""
    try:
        result = get_account_transactions(
            adb, account_id,
            limit=limit, cursor=cursor, direction=direction,
            start_time=start_time, end_time=end_time,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        logger.error("account_transactions error: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to retrieve transactions.")
    return _ok(result)


# ── Counterparties ─────────────────────────────────────────────────────────── #

@router.get("/{account_id}/counterparties")
def account_counterparties(
    account_id: str,
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """Aggregated counterparties for an account."""
    try:
        result = get_account_counterparties(adb, account_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        logger.error("account_counterparties error: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to retrieve counterparties.")
    return _ok(result)


# ── Timeline ───────────────────────────────────────────────────────────────── #

@router.get("/{account_id}/timeline")
def account_timeline(
    account_id: str,
    limit: int = Query(200, ge=1, le=1000),
    cursor: Optional[str] = Query(None),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """Chronological transaction timeline for an account."""
    try:
        result = get_account_timeline(
            adb, account_id,
            limit=limit, cursor=cursor,
            start_time=start_time, end_time=end_time,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        logger.error("account_timeline error: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to retrieve timeline.")
    return _ok(result)


# ── Focused subgraph ───────────────────────────────────────────────────────── #

@router.get("/{account_id}/graph")
def account_graph(
    account_id: str,
    hops: int = Query(1, ge=1, le=3),
    max_nodes: int = Query(150, ge=10, le=500),
    max_edges: int = Query(500, ge=20, le=1500),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """
    Return a focused subgraph centred on account_id.
    Used by the Network Analysis panel in Module C.
    Response is bounded: max_nodes / max_edges enforced.
    """
    try:
        account_id_upper = account_id.upper()

        # Validate account exists
        exists = adb.execute(
            "SELECT 1 FROM accounts WHERE normalized_account_id = ?",
            [account_id_upper]
        ).fetchone()
        if not exists:
            # Try transactions table as fallback
            exists = adb.execute(
                "SELECT 1 FROM transactions WHERE sender_account = ? OR receiver_account = ? LIMIT 1",
                [account_id_upper, account_id_upper]
            ).fetchone()
            if not exists:
                raise HTTPException(404, f"Account {account_id} not found.")

        time_clause = ""
        time_params: list = []
        if start_time:
            time_clause += " AND t.ts >= ?"
            time_params.append(start_time)
        if end_time:
            time_clause += " AND t.ts <= ?"
            time_params.append(end_time)

        # Build hop-expanded account set
        frontier = {account_id_upper}
        all_accounts: set[str] = {account_id_upper}

        for _ in range(hops):
            if len(all_accounts) >= max_nodes:
                break
            ph = ", ".join("?" * len(frontier))
            neighbors = adb.execute(
                f"""
                SELECT DISTINCT counterparty FROM (
                    SELECT receiver_account AS counterparty
                    FROM transactions t
                    WHERE sender_account IN ({ph}) {time_clause}
                    UNION
                    SELECT sender_account AS counterparty
                    FROM transactions t
                    WHERE receiver_account IN ({ph}) {time_clause}
                )
                LIMIT ?
                """,
                list(frontier) + time_params + list(frontier) + time_params + [max_nodes - len(all_accounts)],
            ).fetchall()
            new_accounts = {r[0] for r in neighbors} - all_accounts
            all_accounts.update(new_accounts)
            frontier = new_accounts
            if not frontier:
                break

        # Fetch edges between all_accounts
        acct_list = list(all_accounts)
        ph = ", ".join("?" * len(acct_list))
        edge_rows = adb.execute(
            f"""
            SELECT
                t.transaction_id,
                t.sender_account,
                t.receiver_account,
                CAST(t.amount AS DOUBLE) AS amount,
                t.ts::VARCHAR,
                t.payment_mode,
                t.sender_ifsc,
                t.receiver_ifsc,
                t.device_type,
                t.ip_address
            FROM transactions t
            WHERE t.sender_account IN ({ph})
              AND t.receiver_account IN ({ph})
              {time_clause}
            ORDER BY t.ts ASC
            LIMIT ?
            """,
            acct_list + acct_list + time_params + [max_edges],
        ).fetchall()

        edge_keys = ["id", "source", "target", "amount", "ts",
                     "payment_mode", "sender_ifsc", "receiver_ifsc",
                     "device_type", "ip_address"]
        edges = [dict(zip(edge_keys, r)) for r in edge_rows]

        # Fetch node metadata
        ph2 = ", ".join("?" * len(acct_list))
        node_rows = adb.execute(
            f"""
            SELECT
                a.normalized_account_id,
                a.account_node_id,
                CAST(a.incoming_amount AS DOUBLE),
                CAST(a.outgoing_amount AS DOUBLE),
                a.incoming_count,
                a.outgoing_count,
                a.unique_senders,
                a.unique_receivers,
                a.first_seen,
                a.last_seen,
                CASE WHEN a.incoming_amount > 0
                     THEN CAST(a.outgoing_amount AS DOUBLE) /
                          CAST(a.incoming_amount AS DOUBLE)
                     ELSE NULL END AS pass_through_ratio
            FROM accounts a
            WHERE a.normalized_account_id IN ({ph2})
            """,
            acct_list,
        ).fetchall()

        node_keys = [
            "id", "node_id", "incoming_amount", "outgoing_amount",
            "incoming_count", "outgoing_count", "unique_senders", "unique_receivers",
            "first_seen", "last_seen", "pass_through_ratio",
        ]
        nodes = []
        node_ids_in_nodes = set()
        for row in node_rows:
            d = dict(zip(node_keys, row))
            d["is_focus"] = (d["id"] == account_id_upper)
            d["first_seen"] = d["first_seen"].isoformat() if d["first_seen"] else None
            d["last_seen"]  = d["last_seen"].isoformat()  if d["last_seen"]  else None
            d["pass_through_ratio"] = round(d["pass_through_ratio"], 4) if d["pass_through_ratio"] else None
            nodes.append(d)
            node_ids_in_nodes.add(d["id"])

        # Add accounts that appear in edges but not in accounts table
        for acct in all_accounts:
            if acct not in node_ids_in_nodes:
                nodes.append({
                    "id": acct, "node_id": None, "is_focus": (acct == account_id_upper),
                    "incoming_amount": None, "outgoing_amount": None,
                    "incoming_count": 0, "outgoing_count": 0,
                    "unique_senders": 0, "unique_receivers": 0,
                    "first_seen": None, "last_seen": None, "pass_through_ratio": None,
                })

        truncated = len(edge_rows) >= max_edges or len(all_accounts) >= max_nodes

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("account_graph error: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to build account graph.")

    return _ok({
        "nodes": nodes,
        "edges": edges,
        "focus_account": account_id_upper,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "truncated": truncated,
        "hops": hops,
    })
