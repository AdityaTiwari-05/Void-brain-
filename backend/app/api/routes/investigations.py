"""
app/api/routes/investigations.py
---------------------------------
Module D API endpoints — Investigation workspace + AI Case Officer.

POST /v1/investigations/evidence-packet       - build structured evidence packet
POST /v1/investigations/case-diary            - generate case diary draft
POST /v1/investigations/freeze-requisition    - generate freeze requisition draft
POST /v1/investigations/validate-document     - validate a document against evidence
GET  /v1/investigations/ai-status             - check local AI availability
POST /v1/investigations/test-injection        - prompt injection safety test
"""

from __future__ import annotations

import logging
from typing import Optional

import duckdb
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.analytics import get_analytics_db
from app.ai.evidence_packet import build_evidence_packet, packet_to_dict
from app.ai.local_model import (
    generate_case_diary,
    generate_freeze_requisition,
    get_available_provider,
)
from app.ai.hallucination_validator import (
    EvidenceValidator,
    StructuredOutputValidator,
    test_prompt_injection_safety,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/investigations", tags=["investigations"])


def _ok(data, **meta):
    return {"success": True, "data": data, **meta}


# ── Request schemas ────────────────────────────────────────────────────────── #

class EvidencePacketRequest(BaseModel):
    victim_account: str = Field(..., min_length=1, max_length=32)
    max_hops: int = Field(4, ge=1, le=4)
    max_paths: int = Field(20, ge=1, le=50)
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    investigation_id: Optional[str] = None


class CaseDiaryRequest(BaseModel):
    victim_account: str = Field(..., min_length=1, max_length=32)
    max_hops: int = Field(4, ge=1, le=4)
    force_deterministic: bool = Field(False)
    investigation_id: Optional[str] = None


class FreezeRequisitionRequest(BaseModel):
    victim_account: str = Field(..., min_length=1, max_length=32)
    max_hops: int = Field(4, ge=1, le=4)
    force_deterministic: bool = Field(False)
    investigation_id: Optional[str] = None


class ValidateDocumentRequest(BaseModel):
    victim_account: str = Field(..., min_length=1, max_length=32)
    document_text: str = Field(..., max_length=200_000)
    strict_amounts: bool = Field(False)
    investigation_id: Optional[str] = None


class InjectionTestRequest(BaseModel):
    narration: str = Field(..., max_length=10_000)


# ── AI Status ──────────────────────────────────────────────────────────────── #

@router.get("/ai-status")
def ai_status():
    """Return current local AI provider availability."""
    provider = get_available_provider()
    return _ok({
        "provider": provider,
        "local_ai_available": provider != "deterministic",
        "deterministic_fallback_available": True,
        "note": (
            "Deterministic fallback always available. "
            "To enable AI generation, install Ollama (https://ollama.ai) "
            "and pull a model: `ollama pull mistral`"
        ),
    })


# ── Evidence Packet ────────────────────────────────────────────────────────── #

@router.post("/evidence-packet")
def create_evidence_packet(
    body: EvidencePacketRequest,
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """
    Build and return the structured investigation evidence packet.
    This is the single validated data source for AI generation.
    """
    try:
        packet = build_evidence_packet(
            adb,
            body.victim_account,
            max_hops=body.max_hops,
            max_paths=body.max_paths,
            start_time=body.start_time,
            end_time=body.end_time,
            investigation_id=body.investigation_id,
        )
        packet_dict = packet_to_dict(packet)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        logger.error("Evidence packet error: %s", exc, exc_info=True)
        raise HTTPException(500, f"Failed to build evidence packet: {exc}")

    return _ok(packet_dict)


# ── Case Diary ─────────────────────────────────────────────────────────────── #

@router.post("/case-diary")
def generate_case_diary_endpoint(
    body: CaseDiaryRequest,
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """
    Generate a validated case diary draft.

    Pipeline:
    1. Build evidence packet (backend validated data).
    2. Generate draft via local AI or deterministic fallback.
    3. Validate AI output against evidence whitelist.
    4. Return draft + validation result.
    """
    # Step 1: Evidence packet
    try:
        packet = build_evidence_packet(
            adb, body.victim_account,
            max_hops=body.max_hops,
            investigation_id=body.investigation_id,
        )
        evidence_dict = packet_to_dict(packet)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        logger.error("Case diary evidence packet error: %s", exc, exc_info=True)
        raise HTTPException(500, f"Failed to build evidence packet: {exc}")

    # Step 2: Generate draft
    try:
        doc, method = generate_case_diary(
            evidence_dict,
            force_deterministic=body.force_deterministic,
        )
    except Exception as exc:
        logger.error("Case diary generation error: %s", exc, exc_info=True)
        raise HTTPException(500, f"Document generation failed: {exc}")

    # Step 3: Validate structured output
    struct_validator = StructuredOutputValidator(evidence_dict)
    struct_result = struct_validator.validate_structured(doc)

    # Step 4: Also validate narrative text for hallucinated entities
    text_validator = EvidenceValidator(
        allowed_account_ids=packet.allowed_account_ids,
        allowed_transaction_ids=packet.allowed_transaction_ids,
        allowed_ifsc_codes=packet.allowed_ifsc_codes,
        allowed_amounts=packet.allowed_amounts,
        allowed_timestamps=packet.allowed_timestamps,
        strict_amounts=False,  # narrative amounts checked loosely
    )
    narrative = doc.get("narrative", "") + " ".join(
        f.get("statement", "") for f in doc.get("facts", [])
    )
    text_result = text_validator.validate_text(narrative)

    validation_passed = struct_result.valid and text_result.valid
    all_errors = struct_result.errors + text_result.errors

    if not validation_passed:
        logger.warning(
            "Case diary validation FAILED for %s: %d errors",
            body.victim_account, len(all_errors)
        )
        return {
            "success": False,
            "validation_passed": False,
            "errors": all_errors,
            "document": None,
            "method": method,
            "message": "Document rejected: contains entities not found in evidence packet.",
        }

    return _ok({
        "document": doc,
        "validation_passed": True,
        "validation_warnings": struct_result.warnings + text_result.warnings,
        "generation_method": method,
        "evidence_summary": {
            "investigation_id": packet.investigation_id,
            "victim_account": packet.victim_account,
            "accounts_count": len(packet.accounts),
            "transactions_count": packet.total_transactions_in_evidence,
            "paths_count": len(packet.paths),
            "paths_truncated": packet.paths_truncated,
        },
    })


# ── Freeze Requisition ─────────────────────────────────────────────────────── #

@router.post("/freeze-requisition")
def generate_freeze_requisition_endpoint(
    body: FreezeRequisitionRequest,
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """
    Generate a validated bank freeze requisition draft.
    Holding candidates come from deterministic backend analysis — not AI.
    """
    # Build evidence packet
    try:
        packet = build_evidence_packet(
            adb, body.victim_account,
            max_hops=body.max_hops,
            investigation_id=body.investigation_id,
        )
        evidence_dict = packet_to_dict(packet)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        logger.error("Freeze requisition evidence error: %s", exc, exc_info=True)
        raise HTTPException(500, f"Failed to build evidence packet: {exc}")

    # Determine holding candidates from backend evidence (not AI)
    # Candidates = deepest layer accounts with highest risk
    from app.ai.evidence_packet import EvidenceAccount
    def _acct_layer(a: EvidenceAccount) -> int:
        return a.layer or 0
    def _acct_risk(a: EvidenceAccount) -> float:
        return a.risk_index or 0.0

    holding_candidates = [
        {
            "account_id": a.account_id,
            "layer": a.layer,
            "risk_index": a.risk_index,
            "detection_indicators": a.detection_indicators,
            "reason": (
                f"Backend analysis: Layer {a.layer} account, "
                f"risk_index={a.risk_index or 'N/A'}, "
                f"indicators={a.detection_indicators}"
            ),
            "incoming_amount": a.incoming_amount,
            "supporting_tx_ids": a.supporting_tx_ids[:5],
        }
        for a in sorted(
            packet.accounts,
            key=lambda x: (-_acct_layer(x), -_acct_risk(x))
        )
        if _acct_layer(a) > 1  # only downstream accounts
    ][:5]

    holding_candidates_serial = holding_candidates  # already dicts

    # Generate draft
    try:
        doc, method = generate_freeze_requisition(
            evidence_dict,
            holding_candidates_serial,
            force_deterministic=body.force_deterministic,
        )
    except Exception as exc:
        logger.error("Freeze requisition generation error: %s", exc, exc_info=True)
        raise HTTPException(500, f"Document generation failed: {exc}")

    # Validate output
    struct_validator = StructuredOutputValidator(evidence_dict)
    struct_result = struct_validator.validate_structured(doc)

    text_validator = EvidenceValidator(
        allowed_account_ids=packet.allowed_account_ids,
        allowed_transaction_ids=packet.allowed_transaction_ids,
        allowed_ifsc_codes=packet.allowed_ifsc_codes,
        allowed_amounts=packet.allowed_amounts,
        allowed_timestamps=packet.allowed_timestamps,
    )
    text_to_validate = " ".join([
        str(doc.get("legal_basis_note", "")),
        " ".join(str(fc.get("reason", "")) for fc in doc.get("freeze_candidates", [])),
    ])
    text_result = text_validator.validate_text(text_to_validate)

    validation_passed = struct_result.valid and text_result.valid
    all_errors = struct_result.errors + text_result.errors

    if not validation_passed:
        return {
            "success": False,
            "validation_passed": False,
            "errors": all_errors,
            "document": None,
            "method": method,
            "message": "Document rejected: contains entities not found in evidence packet.",
        }

    return _ok({
        "document": doc,
        "validation_passed": True,
        "validation_warnings": struct_result.warnings + text_result.warnings,
        "generation_method": method,
        "holding_candidates": holding_candidates_serial,
        "evidence_summary": {
            "investigation_id": packet.investigation_id,
            "victim_account": packet.victim_account,
        },
        "disclaimer": (
            "This is an AI/template-generated DRAFT. "
            "Legal sections must be verified by authorized personnel. "
            "This document must not be dispatched without authorized officer review."
        ),
    })


# ── Document Validation ────────────────────────────────────────────────────── #

@router.post("/validate-document")
def validate_document(
    body: ValidateDocumentRequest,
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """
    Validate arbitrary document text against evidence packet whitelists.
    Used to demonstrate anti-hallucination system in Module D demo.
    """
    try:
        packet = build_evidence_packet(
            adb, body.victim_account,
            max_hops=2, max_paths=10,  # fast packet for validation
            investigation_id=body.investigation_id,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Failed to build evidence packet: {exc}")

    validator = EvidenceValidator(
        allowed_account_ids=packet.allowed_account_ids,
        allowed_transaction_ids=packet.allowed_transaction_ids,
        allowed_ifsc_codes=packet.allowed_ifsc_codes,
        allowed_amounts=packet.allowed_amounts,
        allowed_timestamps=packet.allowed_timestamps,
        strict_amounts=body.strict_amounts,
    )
    result = validator.validate_text(body.document_text)

    return _ok({
        "valid": result.valid,
        "errors": result.errors,
        "warnings": result.warnings,
        "entities_found": {
            "accounts": result.found_accounts,
            "ifscs": result.found_ifscs,
            "transaction_ids": result.found_txn_ids,
            "amounts": result.found_amounts,
            "timestamps": result.found_timestamps,
        },
        "unsupported_entities": {
            "accounts": result.unsupported_accounts,
            "ifscs": result.unsupported_ifscs,
            "transaction_ids": result.unsupported_txn_ids,
            "amounts": result.unsupported_amounts,
            "timestamps": result.unsupported_timestamps,
        },
        "evidence_whitelists": {
            "allowed_accounts_count": len(packet.allowed_account_ids),
            "allowed_transactions_count": len(packet.allowed_transaction_ids),
            "allowed_ifscs_count": len(packet.allowed_ifsc_codes),
        },
    })


# ── Prompt Injection Test ─────────────────────────────────────────────────── #

@router.post("/test-injection")
def test_injection(body: InjectionTestRequest):
    """
    Demonstrate prompt-injection safety.
    Shows that narration is treated as data — never executed.
    """
    result = test_prompt_injection_safety(body.narration)
    return _ok({
        "narration_received": body.narration[:500],
        "safety_result": result,
        "system_behavior": (
            "Narration is placed in a clearly delimited DATA section of the AI prompt. "
            "System instructions are fixed and cannot be overridden by data content. "
            "No injection markers found in this narration will affect system behavior."
        ),
    })
