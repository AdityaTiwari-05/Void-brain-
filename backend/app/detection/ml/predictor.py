"""
app/detection/ml/predictor.py
------------------------------
ML model loading and prediction — with graceful fallback.

If the model file is missing, corrupt, or incompatible, all prediction
calls return None (not an error).  The application falls back to
deterministic heuristic mode automatically.

Never fabricate predictions — if the model is unavailable, say so.
"""

from __future__ import annotations

import json
import logging
import pickle
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MODEL_FILE    = Path("data/models/mule_rf_model.pkl")
METADATA_FILE = Path("data/models/mule_rf_metadata.json")

FEATURE_COLS = [
    "incoming_count", "outgoing_count", "total_count",
    "unique_senders", "unique_receivers",
    "inc_amt", "out_amt",
    "pass_through_ratio", "in_out_count_ratio",
    "activity_hours", "tx_velocity_per_hour",
    "unusual_device_count", "crypto_p2p_count", "earning_count",
]

_model_cache = None
_metadata_cache = None


def _load_model():
    global _model_cache, _metadata_cache
    if _model_cache is not None:
        return _model_cache, _metadata_cache
    if not MODEL_FILE.exists():
        logger.info("ML model not found at %s — running in heuristic-only mode.", MODEL_FILE)
        return None, None
    try:
        with open(MODEL_FILE, "rb") as fh:
            _model_cache = pickle.load(fh)
        if METADATA_FILE.exists():
            with open(METADATA_FILE) as fh:
                _metadata_cache = json.load(fh)
        logger.info("ML model loaded: version=%s", _metadata_cache.get("model_version") if _metadata_cache else "unknown")
        return _model_cache, _metadata_cache
    except Exception as exc:
        logger.warning("Failed to load ML model: %s — heuristic-only mode.", exc)
        _model_cache = None
        _metadata_cache = None
        return None, None


def predict_proba(features_dict: dict) -> Optional[float]:
    """
    Return mule probability [0.0, 1.0] for a feature dict, or None if unavailable.

    Parameters
    ----------
    features_dict : dict with keys matching FEATURE_COLS (may contain extras).
    """
    model, _ = _load_model()
    if model is None:
        return None
    try:
        import numpy as np
        row = [[features_dict.get(k) for k in FEATURE_COLS]]
        proba = model.predict_proba(row)[0][1]
        return float(proba)
    except Exception as exc:
        logger.warning("ML prediction failed: %s", exc)
        return None


def get_model_metadata() -> Optional[dict]:
    _, meta = _load_model()
    return meta


def model_available() -> bool:
    model, _ = _load_model()
    return model is not None


def clear_cache() -> None:
    """Force reload on next call (used after training a new model)."""
    global _model_cache, _metadata_cache
    _model_cache = None
    _metadata_cache = None
