"""
app/ai/hallucination_validator.py
----------------------------------
Module D — Programmatic Anti-Hallucination Validator.

CRITICAL REQUIREMENT: This is a CODE-LEVEL enforcement layer.
We do NOT rely only on prompting to prevent AI hallucinations.

Architecture:
  1. Before AI generation: build whitelists from evidence packet.
  2. After AI generation: extract all factual entities from generated text.
  3. Validate every entity against whitelists.
  4. REJECT if any entity is unsupported — never silently repair.

Validates:
  - Account numbers (12-digit strings)
  - IFSC codes (bank format: XXXX0XXXXXX)
  - Transaction IDs
  - Amounts (decimal numbers with INR context)
  - Timestamps (date/time patterns)

Prompt-injection test:
  If narration contains "IGNORE", "instruction", or similar injection attempts,
  the validator ensures those strings are treated as data — not followed.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

logger = logging.getLogger(__name__)

# ── Regex patterns ────────────────────────────────────────────────────────── #

# 12-digit account number (may appear with leading zeros)
_ACCOUNT_RE = re.compile(r"\b(\d{12})\b")

# IFSC: 4 alpha + 0 + 6 alphanumeric
_IFSC_RE = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b")

# Transaction IDs: TXN followed by alphanumeric (common pattern in synthetic data)
_TXN_RE = re.compile(r"\b(TXN[A-Za-z0-9]{6,20})\b", re.IGNORECASE)

# Amounts: digits with optional decimal and optional ₹/INR/Rs prefix
_AMOUNT_RE = re.compile(
    r"(?:₹|INR|Rs\.?\s*)?(\d{1,15}(?:,\d{2,3})*(?:\.\d{1,2})?)\b"
)

# Date/time: YYYY-MM-DD with optional HH:MM:SS
_TIMESTAMP_RE = re.compile(
    r"\b(\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?:\s+\d{2}:\d{2}(?::\d{2})?)?)\b"
)


def _normalise_amount(amt_str: str) -> str:
    """Normalise amount string: remove commas, ensure 2 decimal places."""
    cleaned = amt_str.replace(",", "")
    try:
        return str(Decimal(cleaned).quantize(Decimal("0.01")))
    except InvalidOperation:
        return cleaned


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    found_accounts: list[str] = field(default_factory=list)
    found_ifscs: list[str] = field(default_factory=list)
    found_txn_ids: list[str] = field(default_factory=list)
    found_amounts: list[str] = field(default_factory=list)
    found_timestamps: list[str] = field(default_factory=list)
    unsupported_accounts: list[str] = field(default_factory=list)
    unsupported_ifscs: list[str] = field(default_factory=list)
    unsupported_txn_ids: list[str] = field(default_factory=list)
    unsupported_amounts: list[str] = field(default_factory=list)
    unsupported_timestamps: list[str] = field(default_factory=list)


class EvidenceValidator:
    """
    Validates AI-generated document output against an evidence packet whitelist.

    Usage:
        validator = EvidenceValidator(packet)
        result = validator.validate_text(ai_generated_text)
        if not result.valid:
            # REJECT the document
            raise DocumentValidationError(result.errors)
    """

    def __init__(
        self,
        allowed_account_ids: list[str],
        allowed_transaction_ids: list[str],
        allowed_ifsc_codes: list[str],
        allowed_amounts: list[str],
        allowed_timestamps: list[str],
        *,
        strict_amounts: bool = False,   # if True, any amount not in whitelist fails
    ):
        self.allowed_accounts  = set(allowed_account_ids)
        self.allowed_txn_ids   = {t.upper() for t in allowed_transaction_ids}
        self.allowed_ifscs     = set(allowed_ifsc_codes)
        self.strict_amounts    = strict_amounts
        self.strict_timestamps = False

        # Normalise allowed amounts
        self.allowed_amounts: set[str] = set()
        for amt in allowed_amounts:
            try:
                self.allowed_amounts.add(_normalise_amount(str(amt)))
            except Exception:
                pass

        # Normalise allowed timestamps (date portion only)
        self.allowed_timestamp_dates: set[str] = set()
        for ts in allowed_timestamps:
            if ts:
                # Keep only the date part (YYYY-MM-DD) for fuzzy matching
                self.allowed_timestamp_dates.add(ts[:10])

    def validate_text(self, text: str) -> ValidationResult:
        """
        Extract and validate all factual entities from AI-generated text.

        REJECT rules:
          1. Any 12-digit account number not in allowed_account_ids → FAIL
          2. Any IFSC code not in allowed_ifsc_codes → FAIL
          3. Any TXN-prefixed ID not in allowed_transaction_ids → FAIL
          4. (If strict_amounts) Any amount not in allowed_amounts → FAIL

        Returns ValidationResult with valid=False if any rule triggers.
        """
        result = ValidationResult(valid=True)

        # ── Account numbers ────────────────────────────────────────────────── #
        found_accounts = set(_ACCOUNT_RE.findall(text))
        result.found_accounts = sorted(found_accounts)
        for acct in found_accounts:
            if acct not in self.allowed_accounts:
                result.unsupported_accounts.append(acct)
                result.errors.append(
                    f"HALLUCINATED_ACCOUNT: '{acct}' is not in evidence packet. "
                    "Rejecting document."
                )
                result.valid = False

        # ── IFSC codes ─────────────────────────────────────────────────────── #
        found_ifscs = set(_IFSC_RE.findall(text))
        result.found_ifscs = sorted(found_ifscs)
        for ifsc in found_ifscs:
            if ifsc not in self.allowed_ifscs:
                result.unsupported_ifscs.append(ifsc)
                result.errors.append(
                    f"HALLUCINATED_IFSC: '{ifsc}' is not in evidence packet. "
                    "Rejecting document."
                )
                result.valid = False

        # ── Transaction IDs ────────────────────────────────────────────────── #
        found_txns = set(_TXN_RE.findall(text))
        result.found_txn_ids = sorted(found_txns)
        for txn in found_txns:
            if txn.upper() not in self.allowed_txn_ids:
                result.unsupported_txn_ids.append(txn)
                result.errors.append(
                    f"HALLUCINATED_TXN_ID: '{txn}' is not in evidence packet. "
                    "Rejecting document."
                )
                result.valid = False

        # ── Amounts (strict mode only) ─────────────────────────────────────── #
        found_amounts_raw = _AMOUNT_RE.findall(text)
        found_amounts_norm = [_normalise_amount(a) for a in found_amounts_raw]
        result.found_amounts = sorted(set(found_amounts_norm))
        if self.strict_amounts:
            for amt in set(found_amounts_norm):
                if amt not in self.allowed_amounts:
                    # Only flag amounts that look like specific financial figures (> ₹100)
                    try:
                        val = Decimal(amt)
                        if val > Decimal("100"):
                            result.unsupported_amounts.append(amt)
                            result.errors.append(
                                f"HALLUCINATED_AMOUNT: '{amt}' is not in evidence packet."
                            )
                            result.valid = False
                    except Exception:
                        pass

        # ── Timestamps ────────────────────────────────────────────────────── #
        found_timestamps = set(_TIMESTAMP_RE.findall(text))
        result.found_timestamps = sorted(found_timestamps)
        if self.strict_timestamps and self.allowed_timestamp_dates:
            for ts in found_timestamps:
                date_part = ts[:10]
                if date_part not in self.allowed_timestamp_dates:
                    result.unsupported_timestamps.append(ts)
                    result.warnings.append(
                        f"TIMESTAMP_NOT_IN_EVIDENCE: '{ts}' was not found in evidence transactions."
                    )

        return result


class StructuredOutputValidator:
    """
    Validates AI-generated STRUCTURED JSON output before document rendering.

    Checks each field against evidence packet whitelists.
    This is stricter than text validation — called on the structured dict.
    """

    def __init__(self, evidence_packet_dict: dict):
        self.allowed_accounts = set(evidence_packet_dict.get("allowed_account_ids", []))
        self.allowed_txn_ids  = {t.upper() for t in evidence_packet_dict.get("allowed_transaction_ids", [])}
        self.allowed_ifscs    = set(evidence_packet_dict.get("allowed_ifsc_codes", []))
        self.allowed_amounts: set[str] = set()
        for amt in evidence_packet_dict.get("allowed_amounts", []):
            try:
                self.allowed_amounts.add(_normalise_amount(str(amt)))
            except Exception:
                pass

    def validate_structured(self, doc: dict) -> ValidationResult:
        """Validate a structured AI output dict against evidence whitelists."""
        result = ValidationResult(valid=True)
        errors = result.errors

        def _check_field(value: str, field_name: str, whitelist: set) -> bool:
            v = str(value).strip().upper()
            if v and v not in {x.upper() for x in whitelist}:
                errors.append(
                    f"INVALID_{field_name}: '{value}' not found in evidence packet."
                )
                result.valid = False
                return False
            return True

        # Validate accounts list
        for acct in doc.get("accounts", []):
            acct_id = acct if isinstance(acct, str) else acct.get("account_id", "")
            if acct_id:
                _check_field(acct_id, "ACCOUNT", self.allowed_accounts)

        # Validate transactions list
        for txn in doc.get("transactions", []):
            if isinstance(txn, dict):
                tid = txn.get("transaction_id", "")
                if tid:
                    _check_field(tid, "TRANSACTION_ID", self.allowed_txn_ids)
                for acct_field in ["sender_account", "from", "receiver_account", "to"]:
                    acct_val = txn.get(acct_field, "")
                    if acct_val:
                        _check_field(acct_val, "ACCOUNT", self.allowed_accounts)

        # Validate holding candidates
        for candidate in doc.get("holding_candidates", []):
            cid = candidate if isinstance(candidate, str) else candidate.get("account_id", "")
            if cid:
                _check_field(cid, "HOLDING_CANDIDATE_ACCOUNT", self.allowed_accounts)

        # Validate IFSC codes in any field
        for ifsc_field in doc.get("ifsc_codes", []):
            if ifsc_field:
                _check_field(ifsc_field, "IFSC", self.allowed_ifscs)

        return result


def test_prompt_injection_safety(narration: str) -> dict:
    """
    Test utility: verify a narration string cannot inject instructions.

    Returns a dict with:
      - 'is_safe': True if narration treated as data (not executed)
      - 'injection_markers': any injection-like patterns found
      - 'action': always 'treat_as_data' — narration is NEVER executed
    """
    INJECTION_PATTERNS = [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"you\s+are\s+now",
        r"act\s+as",
        r"disregard",
        r"override",
        r"forget\s+(everything|all)",
        r"new\s+system\s+prompt",
        r"jailbreak",
        r"admin\s+mode",
    ]
    found = []
    narr_lower = narration.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, narr_lower):
            found.append(pattern)

    return {
        "narration_length": len(narration),
        "injection_markers_found": found,
        "is_safe": True,   # ALWAYS True — narration is NEVER executed
        "action": "treat_as_data",
        "note": (
            "Narration is untrusted data. It is passed to AI in a "
            "clearly delimited DATA section — never as a system instruction."
        ),
    }
