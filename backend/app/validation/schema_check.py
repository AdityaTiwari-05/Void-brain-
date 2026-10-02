"""
CSV column-level schema checking for Operation Abhedya-Chakra.

Responsibilities
----------------
1. Detect missing required columns — reported as ERROR.
2. Detect unexpected extra columns — reported as WARNING (load continues).
3. Return a structured result consumed by the ingestion engine.

This runs ONCE per file, before any row-level processing.
No per-row Python; this only inspects column names.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── Constants ─────────────────────────────────────────────────────────────── #

REQUIRED_COLUMNS: tuple[str, ...] = (
    "Transaction_ID",
    "Sender_Account",
    "Receiver_Account",
    "Sender_IFSC",
    "Receiver_IFSC",
    "Amount",
    "Timestamp",
    "Payment_Mode",
    "Narration",
    "IP_Address",
    "Device_Type",
)

# Canonical case-normalised set for fast look-up
_REQUIRED_LOWER: frozenset[str] = frozenset(c.lower() for c in REQUIRED_COLUMNS)


# ── Result type ───────────────────────────────────────────────────────────── #


@dataclass
class ColumnCheckResult:
    """
    Outcome of a column-presence check.

    Attributes
    ----------
    ok : bool
        True only when every required column is present (extras are allowed).
    missing : list[str]
        Required columns absent from the file (case-insensitive match).
    unexpected : list[str]
        Columns present in the file but not in REQUIRED_COLUMNS.
    canonical_map : dict[str, str]
        Maps each *file* column name → its canonical REQUIRED_COLUMNS name
        (or the original name if it is an extra column).  Used by the
        ingestion engine to reference columns without repeating the
        case-normalisation logic.
    error_message : str
        Human-readable summary; empty string when ok is True.
    warning_message : str
        Human-readable summary of unexpected columns; empty when none.
    """

    ok: bool
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    canonical_map: dict[str, str] = field(default_factory=dict)
    error_message: str = ""
    warning_message: str = ""


# ── Public API ────────────────────────────────────────────────────────────── #


def validate_columns(file_columns: list[str]) -> ColumnCheckResult:
    """
    Check *file_columns* (the header row of an ingested CSV) against the
    required schema.

    Parameters
    ----------
    file_columns:
        Column names exactly as they appear in the file header.

    Returns
    -------
    ColumnCheckResult
    """
    # Build a lower→original mapping for file columns
    file_lower_map: dict[str, str] = {}
    for col in file_columns:
        key = col.strip().lower()
        # If duplicate header names exist, keep first occurrence
        if key not in file_lower_map:
            file_lower_map[key] = col.strip()

    # Build canonical map: file_col → required_col (or itself if extra)
    canonical_map: dict[str, str] = {}
    # Lower→canonical for required cols
    req_lower_to_canon: dict[str, str] = {c.lower(): c for c in REQUIRED_COLUMNS}

    # --- Missing ---
    missing: list[str] = []
    for req_lower, req_canon in req_lower_to_canon.items():
        if req_lower in file_lower_map:
            canonical_map[file_lower_map[req_lower]] = req_canon
        else:
            missing.append(req_canon)

    # --- Unexpected ---
    unexpected: list[str] = []
    for lower_key, original in file_lower_map.items():
        if lower_key not in _REQUIRED_LOWER:
            unexpected.append(original)
            canonical_map[original] = original  # kept as-is

    ok = len(missing) == 0

    error_message = ""
    if missing:
        error_message = (
            f"Missing required column(s): {', '.join(missing)}. "
            f"File has: {', '.join(file_columns)}."
        )

    warning_message = ""
    if unexpected:
        warning_message = (
            f"Unexpected column(s) in file (will be ignored): {', '.join(unexpected)}."
        )

    return ColumnCheckResult(
        ok=ok,
        missing=missing,
        unexpected=unexpected,
        canonical_map=canonical_map,
        error_message=error_message,
        warning_message=warning_message,
    )
