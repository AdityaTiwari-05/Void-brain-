"""
Tests for app.validation.schema_check.validate_columns().

Covers
------
- All required columns present → ok=True
- Missing one required column → ok=False, listed in missing
- Missing multiple columns → all listed
- Unexpected extra column → ok=True, listed in unexpected, warning_message set
- Case-insensitive column matching (e.g. 'transaction_id' matches 'Transaction_ID')
- canonical_map correctness
- Empty column list → all required columns missing
"""

from __future__ import annotations

import pytest
from app.validation.schema_check import (
    REQUIRED_COLUMNS,
    ColumnCheckResult,
    validate_columns,
)


def test_all_required_columns_present():
    result = validate_columns(list(REQUIRED_COLUMNS))
    assert result.ok is True
    assert result.missing == []
    assert result.error_message == ""


def test_missing_single_column():
    cols = [c for c in REQUIRED_COLUMNS if c != "Transaction_ID"]
    result = validate_columns(cols)
    assert result.ok is False
    assert "Transaction_ID" in result.missing
    assert "Transaction_ID" in result.error_message


def test_missing_multiple_columns():
    missing_set = {"Transaction_ID", "Amount", "Timestamp"}
    cols = [c for c in REQUIRED_COLUMNS if c not in missing_set]
    result = validate_columns(cols)
    assert result.ok is False
    for col in missing_set:
        assert col in result.missing


def test_unexpected_extra_column():
    cols = list(REQUIRED_COLUMNS) + ["Extra_Column", "Another_Field"]
    result = validate_columns(cols)
    assert result.ok is True  # extra cols don't block ingestion
    assert "Extra_Column" in result.unexpected
    assert "Another_Field" in result.unexpected
    assert result.warning_message != ""


def test_case_insensitive_matching():
    # Lower-case version of all required columns should still pass
    cols = [c.lower() for c in REQUIRED_COLUMNS]
    result = validate_columns(cols)
    assert result.ok is True
    assert result.missing == []


def test_mixed_case_matching():
    cols = list(REQUIRED_COLUMNS)
    # Swap two to different cases
    cols[0] = "transaction_id"
    cols[1] = "SENDER_ACCOUNT"
    result = validate_columns(cols)
    assert result.ok is True


def test_canonical_map_populated():
    result = validate_columns(list(REQUIRED_COLUMNS))
    # Every required column should be in the values of canonical_map
    canonical_values = set(result.canonical_map.values())
    for req in REQUIRED_COLUMNS:
        assert req in canonical_values, f"{req} not in canonical_map values"


def test_empty_columns_list():
    result = validate_columns([])
    assert result.ok is False
    assert len(result.missing) == len(REQUIRED_COLUMNS)


def test_no_unexpected_when_only_required():
    result = validate_columns(list(REQUIRED_COLUMNS))
    assert result.unexpected == []
    assert result.warning_message == ""


def test_error_message_lists_file_columns():
    """error_message should tell the user what columns the file actually has."""
    cols = ["Transaction_ID", "Sender_Account"]  # grossly incomplete
    result = validate_columns(cols)
    assert result.ok is False
    # File columns should appear somewhere in the error message
    assert "Transaction_ID" in result.error_message or "Sender_Account" in result.error_message
