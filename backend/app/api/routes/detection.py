"""
app/api/routes/detection.py
----------------------------
Module B FastAPI endpoints — /api/v1/detection/
Adapted for Void-brain- project structure.

Endpoints:
  GET  /api/v1/detection/health
  GET  /api/v1/detection/accounts/{account_id}/risk
  GET  /api/v1/detection/accounts/{account_id}/features
  GET  /api/v1/detection/accounts/{account_id}/evidence
  POST /api/v1/detection/trace
  GET  /api/v1/detection/suspicious
  GET  /api/v1/detection/model/evaluation
"""

from __future__ import annotations

import logging
from typing import Optional

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.analytics import get_analytics_db
from app.schemas.detection import (
    MuleRiskResult, AccountFeatures, FourHopTrace,
    VictimTraceRequest, ModelEvaluationResponse,
)
from app.detection.features import extract_account_features, extract_features_bulk_sql
from app.detection.risk_scorer import score_account
from app.detection.hop_tracer import trace_victim
from app.detection.ml.predictor import model_available, get_model_metadata, predict_proba
from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/detection", tags=["detection"])


def _ok(data, **meta):
    return {"success": True, "data": data, **meta}


def _err(exc):
    raise HTTPException(status_code=400, detail={"error": "INVALID_INPUT", "message": str(exc)})


# ── Health ─────────────────────────────────────────────────────────────────── #

@router.get("/health")
def detection_health(
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """Module B readiness check."""
    try:
        acct_count = adb.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        txn_count  = adb.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    except Exception:
        acct_count, txn_count = 0, 0
    return _ok({
        "status": "ok",
        "module": "B",
        "accounts": acct_count,
        "transactions": txn_count,
        "ml_model_available": model_available(),
        "detection_version": "1.0",
    })


# ── Account risk ───────────────────────────────────────────────────────────── #

@router.get("/accounts/{account_id}/risk", response_model=MuleRiskResult)
def account_risk(
    account_id: str,
    run_pass_through: bool = Query(True),
    run_cycles: bool = Query(True),
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """Compute Mule Risk Index (0–100) for account_id."""
    try:
        ml_prob, ml_ver = None, None
        if model_available():
            feats = extract_features_bulk_sql(adb, [account_id])
            if feats:
                ml_prob = predict_proba(feats[0])
                meta = get_model_metadata()
                ml_ver = meta.get("model_version") if meta else None

        result = score_account(adb, account_id,
                               ml_probability=ml_prob, ml_model_version=ml_ver,
                               run_pass_through=run_pass_through, run_cycles=run_cycles)
    except ValueError as exc:
        _err(exc)
    except Exception as exc:
        logger.error("account_risk error for %s: %s", account_id, exc, exc_info=True)
        raise HTTPException(500, "Internal error computing risk score.")

    if result.explanation.startswith("Account not found"):
        raise HTTPException(404, f"Account {account_id} not found.")
    return result


# ── Account features ───────────────────────────────────────────────────────── #

@router.get("/accounts/{account_id}/features", response_model=AccountFeatures)
def account_features(
    account_id: str,
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """Return computed feature vector for account_id."""
    try:
        features = extract_account_features(adb, account_id)
    except ValueError as exc:
        _err(exc)
    except Exception as exc:
        logger.error("account_features error: %s", exc, exc_info=True)
        raise HTTPException(500, "Internal error computing features.")
    if features is None:
        raise HTTPException(404, f"Account {account_id} not found.")
    return features


# ── Account evidence ───────────────────────────────────────────────────────── #

@router.get("/accounts/{account_id}/evidence")
def account_evidence(
    account_id: str,
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """Return full detection evidence bundle for account_id."""
    try:
        result = score_account(adb, account_id, run_pass_through=True, run_cycles=True)
    except ValueError as exc:
        _err(exc)
    except Exception as exc:
        logger.error("account_evidence error: %s", exc, exc_info=True)
        raise HTTPException(500, "Internal error generating evidence.")

    return _ok({
        "account_id":        result.account_id,
        "risk_index":        result.risk_index,
        "risk_level":        result.risk_level,
        "detection_version": result.detection_version,
        "ml_probability":    result.ml_probability,
        "components":        [c.model_dump() for c in result.components],
        "pass_through":      result.pass_through.model_dump() if result.pass_through else None,
        "fan_in":            result.fan_in.model_dump() if result.fan_in else None,
        "fan_out":           result.fan_out.model_dump() if result.fan_out else None,
        "terminal":          result.terminal.model_dump() if result.terminal else None,
        "cycles":            result.cycles.model_dump() if result.cycles else None,
        "explanation":       result.explanation,
        "limitations":       result.limitations,
    })


# ── Four-hop trace ─────────────────────────────────────────────────────────── #

@router.post("/trace", response_model=FourHopTrace)
def victim_trace(
    body: VictimTraceRequest,
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """Trace fund flow from victim account up to 4 hops downstream."""
    try:
        result = trace_victim(adb, body.victim_account,
                              max_hops=body.max_hops, max_paths=body.max_paths,
                              time_window_hours=body.time_window_hours)
    except ValueError as exc:
        _err(exc)
    except Exception as exc:
        logger.error("victim_trace error: %s", exc, exc_info=True)
        raise HTTPException(500, "Internal error during victim trace.")
    return result


# ── Suspicious accounts ────────────────────────────────────────────────────── #

@router.get("/suspicious")
def suspicious_accounts(
    min_risk_index: float = Query(50.0, ge=0.0, le=100.0),
    limit: int = Query(100, ge=1, le=1000),
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """Fast pre-filter: accounts with high pass-through ratio (proxy for risk)."""
    try:
        rows = adb.execute(
            """
            SELECT a.normalized_account_id, a.account_node_id,
                   a.incoming_count, a.outgoing_count,
                   CASE WHEN a.incoming_amount > 0
                        THEN CAST(a.outgoing_amount AS DOUBLE) /
                             CAST(a.incoming_amount AS DOUBLE)
                        ELSE NULL END AS pass_through_ratio,
                   CAST(a.incoming_amount AS DOUBLE),
                   CAST(a.outgoing_amount AS DOUBLE),
                   a.unique_senders, a.unique_receivers
            FROM accounts a
            WHERE a.incoming_count > 0 AND a.outgoing_count > 0
            ORDER BY pass_through_ratio DESC NULLS LAST
            LIMIT ?
            """,
            [limit * 3],
        ).fetchall()
    except Exception as exc:
        logger.error("suspicious_accounts error: %s", exc, exc_info=True)
        raise HTTPException(500, "Internal error querying suspicious accounts.")

    keys = ["account_id", "account_node_id", "incoming_count", "outgoing_count",
            "pass_through_ratio", "incoming_amount", "outgoing_amount",
            "unique_senders", "unique_receivers"]
    result = []
    for row in rows:
        d = dict(zip(keys, row))
        pt = d.get("pass_through_ratio")
        if pt is not None and pt * 100 >= min_risk_index:
            d["pass_through_ratio"] = round(pt, 4)
            d["incoming_amount"] = str(round(d["incoming_amount"] or 0, 2))
            d["outgoing_amount"] = str(round(d["outgoing_amount"] or 0, 2))
            result.append(d)
        if len(result) >= limit:
            break

    return _ok(result, count=len(result),
               note="Fast pre-filter using pass-through ratio. Call /accounts/{id}/risk for full score.")


# ── Model evaluation ───────────────────────────────────────────────────────── #

@router.get("/model/evaluation", response_model=ModelEvaluationResponse)
def model_evaluation():
    """Return ML model evaluation metrics and status."""
    meta = get_model_metadata()
    if meta is None:
        return ModelEvaluationResponse(
            model_available=False,
            blocker="No trained model found. Provide labels at data/raw/mule_labels.csv.",
            message="Model not available — running in heuristic-only mode.",
        )
    return ModelEvaluationResponse(
        model_available=True,
        model_version=meta.get("model_version"),
        training_date=meta.get("training_date"),
        n_train=meta.get("n_train"),
        n_test=meta.get("n_test"),
        precision=meta.get("precision"),
        recall=meta.get("recall"),
        f1=meta.get("f1"),
        pr_auc=meta.get("pr_auc"),
        feature_importances=meta.get("feature_importances"),
        message="Model loaded and available.",
    )
