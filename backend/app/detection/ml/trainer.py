"""
app/detection/ml/trainer.py
----------------------------
Random Forest ML training pipeline — Module B Stage H.

BLOCKER: No label file found in data/raw/.

Training requires: data/raw/mule_labels.csv
  Columns: account_id (str), label (int: 0=regular, 1=mule)
  Expected: ~1,500 mule accounts, ~23,500 regular accounts.

Do NOT derive labels from the same heuristics used as model features
(that would produce a circular / leaky model).

When labels are available, this module:
  1. Loads labels from CSV
  2. Extracts feature vectors for all labelled accounts via DuckDB
  3. Splits into train/val/test at account level (no account leakage)
  4. Fits preprocessing (StandardScaler, imputer) on TRAINING data only
  5. Trains sklearn RandomForestClassifier
  6. Evaluates on test set: precision, recall, F1, PR-AUC, confusion matrix
  7. Saves model + preprocessing config + feature definitions + training metadata

Until labels are available, train() returns a TrainingBlocker result.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MODEL_DIR        = Path("data/models")
MODEL_FILE       = MODEL_DIR / "mule_rf_model.pkl"
METADATA_FILE    = MODEL_DIR / "mule_rf_metadata.json"
LABEL_FILE_PATHS = [
    Path("data/raw/mule_labels.csv"),
    Path("data/raw/labels.csv"),
    Path("data/raw/ground_truth.csv"),
]
MODEL_VERSION = "1.0"


def _find_label_file() -> Optional[Path]:
    for p in LABEL_FILE_PATHS:
        if p.exists():
            return p
    return None


def train(
    conn,  # duckdb.DuckDBPyConnection
    *,
    label_file: Optional[Path] = None,
    test_size: float = 0.20,
    val_size: float = 0.10,
    random_state: int = 42,
    n_estimators: int = 200,
    max_depth: Optional[int] = 10,
) -> dict:
    """
    Train a Random Forest mule detector.

    Returns a dict with keys:
      success (bool), blocker (str|None), metadata (dict|None)
    """
    lf = label_file or _find_label_file()
    if lf is None:
        msg = (
            "BLOCKER: No label file found. "
            "Provide labels at data/raw/mule_labels.csv "
            "with columns: account_id (str), label (int: 0=regular, 1=mule). "
            "Expected: ~1,500 mule accounts and ~23,500 regular accounts from "
            "the competition dataset. Labels must be independently verified — "
            "do NOT derive them from the same heuristics used as model features."
        )
        logger.warning(msg)
        return {"success": False, "blocker": msg, "metadata": None}

    try:
        import numpy as np
        import pandas as pd
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import (
            precision_score, recall_score, f1_score,
            average_precision_score, confusion_matrix,
        )
        from sklearn.model_selection import train_test_split
        from sklearn.preprocessing import StandardScaler
        from sklearn.impute import SimpleImputer
        from sklearn.pipeline import Pipeline
    except ImportError as e:
        return {
            "success": False,
            "blocker": f"scikit-learn not installed: {e}. Run: pip install scikit-learn",
            "metadata": None,
        }

    t0 = time.perf_counter()
    logger.info("Loading labels from %s", lf)

    labels_df = pd.read_csv(lf)
    labels_df.columns = [c.strip().lower() for c in labels_df.columns]
    if "account_id" not in labels_df.columns or "label" not in labels_df.columns:
        return {
            "success": False,
            "blocker": f"Label file must have columns: account_id, label. Got: {list(labels_df.columns)}",
            "metadata": None,
        }

    labels_df["account_id"] = labels_df["account_id"].str.strip().str.upper()
    labels_df["label"] = labels_df["label"].astype(int)
    logger.info("Labels loaded: %d rows (mule=%d regular=%d)",
                len(labels_df),
                labels_df["label"].sum(),
                (labels_df["label"] == 0).sum())

    # Extract bulk features
    from app.detection.features import extract_features_bulk_sql
    account_ids = labels_df["account_id"].tolist()
    features_list = extract_features_bulk_sql(conn, account_ids)
    features_df = pd.DataFrame(features_list)
    features_df["account_id"] = features_df["account_id"].str.upper()

    merged = labels_df.merge(features_df, on="account_id", how="inner")
    if len(merged) < len(labels_df) * 0.5:
        return {
            "success": False,
            "blocker": (
                f"Only {len(merged)}/{len(labels_df)} labelled accounts found "
                "in the analytics DB. Ensure the correct DB is loaded."
            ),
            "metadata": None,
        }

    FEATURE_COLS = [
        "incoming_count", "outgoing_count", "total_count",
        "unique_senders", "unique_receivers",
        "inc_amt", "out_amt",
        "pass_through_ratio", "in_out_count_ratio",
        "activity_hours", "tx_velocity_per_hour",
        "unusual_device_count", "crypto_p2p_count", "earning_count",
    ]
    X = merged[FEATURE_COLS].values.astype(float)
    y = merged["label"].values

    # Stratified split — no account leakage (each account appears once)
    X_temp, X_test, y_temp, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state, stratify=y
    )
    val_frac = val_size / (1 - test_size)
    X_train, X_val, y_train, y_val = train_test_split(
        X_temp, y_temp, test_size=val_frac, random_state=random_state, stratify=y_temp
    )
    logger.info("Split: train=%d val=%d test=%d", len(X_train), len(X_val), len(X_test))

    # Pipeline: impute → scale → RF
    model = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler",  StandardScaler()),
        ("clf",     RandomForestClassifier(
            n_estimators=n_estimators,
            max_depth=max_depth,
            random_state=random_state,
            class_weight="balanced",
            n_jobs=-1,
        )),
    ])
    model.fit(X_train, y_train)

    # Evaluation on test set
    y_pred    = model.predict(X_test)
    y_proba   = model.predict_proba(X_test)[:, 1]
    precision = float(precision_score(y_test, y_pred, zero_division=0))
    recall    = float(recall_score(y_test, y_pred, zero_division=0))
    f1        = float(f1_score(y_test, y_pred, zero_division=0))
    pr_auc    = float(average_precision_score(y_test, y_proba))
    cm        = confusion_matrix(y_test, y_pred).tolist()

    # Feature importances
    rf = model.named_steps["clf"]
    importances = dict(zip(FEATURE_COLS, [float(x) for x in rf.feature_importances_]))

    # Save
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODEL_FILE, "wb") as fh:
        pickle.dump(model, fh)

    training_date = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    metadata = {
        "model_version":    MODEL_VERSION,
        "training_date":    training_date,
        "n_train":          len(X_train),
        "n_val":            len(X_val),
        "n_test":           len(X_test),
        "n_mule_train":     int(y_train.sum()),
        "n_regular_train":  int((y_train == 0).sum()),
        "feature_columns":  FEATURE_COLS,
        "precision":        precision,
        "recall":           recall,
        "f1":               f1,
        "pr_auc":           pr_auc,
        "confusion_matrix": cm,
        "feature_importances": importances,
        "duration_seconds": round(time.perf_counter() - t0, 2),
        "label_file":       str(lf),
        "n_estimators":     n_estimators,
        "max_depth":        max_depth,
    }
    with open(METADATA_FILE, "w") as fh:
        json.dump(metadata, fh, indent=2)

    logger.info(
        "Training complete: precision=%.3f recall=%.3f F1=%.3f PR-AUC=%.3f",
        precision, recall, f1, pr_auc,
    )
    return {"success": True, "blocker": None, "metadata": metadata}
